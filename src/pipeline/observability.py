"""Privacy-safe observability for deterministic pipeline requests."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import logging
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Engine, text


class PipelineEvent(BaseModel):
    """A request summary intentionally excluding question, user ID, SQL and values."""

    query_id: str
    outcome: str
    stage: str
    duration_ms: float = Field(ge=0)
    error_code: str | None = None
    # A bounded map of pipeline stage names to elapsed milliseconds.  It never
    # contains request text, identities, SQL, or bound values.
    stage_durations_ms: dict[str, float] = Field(default_factory=dict)


class PipelineObserver(ABC):
    @abstractmethod
    def record(self, event: PipelineEvent) -> None:
        """Record one privacy-safe terminal event."""
        raise NotImplementedError


class LoggingPipelineObserver(PipelineObserver):
    """Emit structured logs suitable for a standard log collector."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger('text2sql.pipeline')

    def record(self, event: PipelineEvent) -> None:
        self.logger.info('pipeline_event=%s', json.dumps(event.model_dump(), separators=(',', ':'), sort_keys=True))


class PersistedPipelineObserver(PipelineObserver):
    """Persist only safe terminal request metadata and expose aggregate trends.

    The table is deliberately created outside the business schema migrations so
    small deterministic test databases can use it too.  PostgreSQL uses the
    existing ``audit`` namespace; SQLite uses a plain table for test support.
    """

    def __init__(self, engine: Engine, *, max_records: int = 10_000) -> None:
        if type(max_records) is not int or max_records < 1:
            raise ValueError('max_records must be a positive integer')
        self.engine = engine
        self.max_records = max_records
        # Do not connect during application import/construction: API route
        # modules must remain importable while PostgreSQL is temporarily down.
        self._table_ready = False

    @property
    def _table(self) -> str:
        return 'audit.text2sql_query_audits' if self.engine.dialect.name == 'postgresql' else 'text2sql_query_audits'

    def _ensure_table(self) -> None:
        if self._table_ready:
            return
        with self.engine.begin() as connection:
            if self.engine.dialect.name == 'postgresql':
                connection.exec_driver_sql('CREATE SCHEMA IF NOT EXISTS audit')
            connection.exec_driver_sql(
                f'''CREATE TABLE IF NOT EXISTS {self._table} (
                    query_id VARCHAR(64) PRIMARY KEY,
                    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    outcome VARCHAR(16) NOT NULL,
                    stage VARCHAR(64) NOT NULL,
                    duration_ms DOUBLE PRECISION NOT NULL,
                    error_code VARCHAR(64),
                    stage_durations_json TEXT NOT NULL
                )'''
            )
        self._table_ready = True

    def record(self, event: PipelineEvent) -> None:
        # Explicit column selection is the redaction boundary.  Do not add
        # question, actor, SQL, parameters, headers, exception detail, etc.
        self._ensure_table()
        with self.engine.begin() as connection:
            connection.execute(text(
                f'''INSERT INTO {self._table}
                    (query_id, recorded_at, outcome, stage, duration_ms, error_code, stage_durations_json)
                    VALUES (:query_id, :recorded_at, :outcome, :stage, :duration_ms, :error_code, :stage_durations_json)'''
            ), {
                'query_id': event.query_id,
                'recorded_at': datetime.now(timezone.utc),
                'outcome': event.outcome,
                'stage': event.stage,
                'duration_ms': event.duration_ms,
                'error_code': event.error_code,
                'stage_durations_json': json.dumps(event.stage_durations_ms, separators=(',', ':'), sort_keys=True),
            })
            # The dashboard is operational history, not an immutable event
            # archive. Retention bounds table growth and Python aggregation.
            limit_clause = 'OFFSET :max_records' if self.engine.dialect.name == 'postgresql' else 'LIMIT -1 OFFSET :max_records'
            connection.execute(text(
                f'''DELETE FROM {self._table} WHERE query_id IN (
                    SELECT query_id FROM {self._table}
                    ORDER BY recorded_at DESC, query_id DESC {limit_clause}
                )'''
            ), {'max_records': self.max_records})

    def aggregate(self) -> dict[str, Any]:
        """Return bounded-label trend data suitable for a future dashboard."""
        self._ensure_table()
        with self.engine.connect() as connection:
            records = connection.execute(text(
                f'''SELECT recorded_at, outcome, stage, duration_ms, error_code, stage_durations_json
                    FROM {self._table} ORDER BY recorded_at ASC'''
            )).mappings().all()
        failure_by_stage: Counter[str] = Counter()
        error_categories: Counter[str] = Counter()
        stage_values: dict[str, list[float]] = defaultdict(list)
        latency_trend: list[dict[str, Any]] = []
        for record in records:
            if record['outcome'] == 'failure':
                failure_by_stage[record['stage']] += 1
                error_categories[record['error_code'] or 'UNKNOWN'] += 1
            for stage, duration in json.loads(record['stage_durations_json']).items():
                stage_values[stage].append(float(duration))
            recorded_at = record['recorded_at']
            latency_trend.append({
                'recorded_at': recorded_at.isoformat() if hasattr(recorded_at, 'isoformat') else str(recorded_at),
                'duration_ms': float(record['duration_ms']),
                'outcome': record['outcome'],
            })
        return {
            'total_requests': len(records),
            'failure_by_stage': dict(sorted(failure_by_stage.items())),
            'error_categories': dict(sorted(error_categories.items())),
            'stage_latency_ms': {
                stage: {'count': len(values), 'average': round(sum(values) / len(values), 3)}
                for stage, values in sorted(stage_values.items())
            },
            'latency_trend': latency_trend[-100:],
        }


class CompositePipelineObserver(PipelineObserver):
    """Keep structured logging while adding durable privacy-safe audit data."""

    def __init__(self, *observers: PipelineObserver) -> None:
        self.observers = observers

    def record(self, event: PipelineEvent) -> None:
        for observer in self.observers:
            observer.record(event)


def elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 3)

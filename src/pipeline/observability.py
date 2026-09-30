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
    """A request summary; sensitive text is redacted from ordinary logs."""

    query_id: str
    outcome: str
    stage: str
    duration_ms: float = Field(ge=0)
    error_code: str | None = None
    question: str | None = None
    sql: str | None = None
    row_count: int | None = Field(default=None, ge=0)
    truncated: bool | None = None
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
        # Prompt and SQL persistence are admin-only; application logs stay redacted.
        safe_event = event.model_copy(update={'question': None, 'sql': None})
        self.logger.info('pipeline_event=%s', json.dumps(safe_event.model_dump(), separators=(',', ':'), sort_keys=True))


class PersistedPipelineObserver(PipelineObserver):
    """Persist terminal metadata and admin-only SQL trace information.

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
                    question_text TEXT,
                    sql_text TEXT,
                    row_count INTEGER,
                    truncated BOOLEAN,
                    stage_durations_json TEXT NOT NULL
                )'''
            )
            self._ensure_columns(connection)
        self._table_ready = True

    def _ensure_columns(self, connection) -> None:
        """Upgrade the lightweight audit table without a separate migration system."""
        expected = {
            'question_text': 'TEXT',
            'sql_text': 'TEXT',
            'row_count': 'INTEGER',
            'truncated': 'BOOLEAN',
        }
        if self.engine.dialect.name == 'sqlite':
            existing = {row[1] for row in connection.exec_driver_sql(f'PRAGMA table_info({self._table})')}
            for name, type_ in expected.items():
                if name not in existing:
                    connection.exec_driver_sql(f'ALTER TABLE {self._table} ADD COLUMN {name} {type_}')
            return
        for name, type_ in expected.items():
            connection.exec_driver_sql(f'ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS {name} {type_}')

    def record(self, event: PipelineEvent) -> None:
        # Explicit column selection is the redaction boundary. Prompt and SQL
        # are retained exclusively for admin trace inspection; never retain
        # actor identity, parameters, headers, secrets, or exception detail.
        self._ensure_table()
        with self.engine.begin() as connection:
            connection.execute(text(
                f'''INSERT INTO {self._table}
                    (query_id, recorded_at, outcome, stage, duration_ms, error_code, question_text, sql_text, row_count, truncated, stage_durations_json)
                    VALUES (:query_id, :recorded_at, :outcome, :stage, :duration_ms, :error_code, :question_text, :sql_text, :row_count, :truncated, :stage_durations_json)'''
            ), {
                'query_id': event.query_id,
                'recorded_at': datetime.now(timezone.utc),
                'outcome': event.outcome,
                'stage': event.stage,
                'duration_ms': event.duration_ms,
                'error_code': event.error_code,
                'question_text': event.question,
                'sql_text': event.sql,
                'row_count': event.row_count,
                'truncated': event.truncated,
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

    def history(self, *, query_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Return bounded admin trace records; caller authorization lives at the route."""
        self._ensure_table()
        limit = max(1, min(limit, 100))
        statement = (
            f'''SELECT query_id, recorded_at, outcome, stage, duration_ms, error_code,
                       question_text, sql_text, row_count, truncated, stage_durations_json
                FROM {self._table}'''
        )
        params: dict[str, Any] = {'limit': limit}
        if query_id:
            statement += ' WHERE query_id = :query_id'
            params['query_id'] = query_id
        statement += ' ORDER BY recorded_at DESC, query_id DESC LIMIT :limit'
        with self.engine.connect() as connection:
            records = connection.execute(text(statement), params).mappings().all()
        return [self._history_record(record) for record in records]

    @staticmethod
    def _history_record(record: Any) -> dict[str, Any]:
        recorded_at = record['recorded_at']
        return {
            'query_id': record['query_id'],
            'recorded_at': recorded_at.isoformat() if hasattr(recorded_at, 'isoformat') else str(recorded_at),
            'outcome': record['outcome'],
            'stage': record['stage'],
            'duration_ms': float(record['duration_ms']),
            'error_code': record['error_code'],
            'question': record['question_text'],
            'sql': record['sql_text'],
            'row_count': record['row_count'],
            'truncated': record['truncated'],
            'stage_durations_ms': json.loads(record['stage_durations_json']),
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

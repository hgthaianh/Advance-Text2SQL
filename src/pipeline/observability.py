"""Privacy-safe observability for deterministic pipeline requests."""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
import logging
from time import perf_counter

from pydantic import BaseModel, Field


class PipelineEvent(BaseModel):
    """A request summary intentionally excluding question, user ID, SQL and values."""

    query_id: str
    outcome: str
    stage: str
    duration_ms: float = Field(ge=0)
    error_code: str | None = None


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


def elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 3)

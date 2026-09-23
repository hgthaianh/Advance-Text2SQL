"""Planning artifacts; no permission policy or execution behavior."""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.sql import Select

from src.query.ir import QueryIR, TimePeriod
from src.shared.models import SchemaRelationship


class QueryPlan(BaseModel):
    """Canonical query plus ordered physical tables and oriented FK joins."""

    model_config = ConfigDict(extra='forbid')
    query: QueryIR
    tables: list[str]
    joins: list[SchemaRelationship]
    # An internal compilation strategy for a scalar SUM/AVG whose owner is
    # repeated by an INNER one-to-many join. It is derived by QueryPlanner,
    # never accepted from QueryIR/user input.
    pre_aggregate_owner: str | None = None


@dataclass(frozen=True)
class CompiledQuery:
    """SQL for inspection and a SQLAlchemy statement for the existing executor."""

    sql: str
    params: dict[str, Any]
    statement: Select


@dataclass(frozen=True)
class CompiledPeriodQuery:
    """One comparison period and its independently bound aggregate statement."""

    period: TimePeriod
    query: CompiledQuery

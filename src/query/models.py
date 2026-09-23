"""Planning artifacts; no permission policy or execution behavior."""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.sql import Select

from src.query.ir import ColumnRef, InstancePermissionFilter, InstanceQueryFilter, QueryIR, TimePeriod
from src.shared.models import SchemaRelationship


class InstanceJoin(BaseModel):
    """Planner-validated logical self-join; not yet a SQL compilation input."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    source_instance_id: Literal['employee_instance']
    source_column: Literal['manager_id']
    target_instance_id: Literal['manager_instance']
    target_column: Literal['id']
    join_type: Literal['left']
    relationship: SchemaRelationship


class QueryPlan(BaseModel):
    """Canonical query plus ordered physical tables and oriented FK joins."""

    model_config = ConfigDict(extra='forbid')
    query: QueryIR
    tables: list[str]
    joins: list[SchemaRelationship]
    instance_joins: list[InstanceJoin] = Field(default_factory=list)
    # Base-side predicates are for employee_instance; target-side predicates
    # are kept by logical instance so Phase 2C can place them in LEFT JOIN ON.
    instance_base_filters: list[InstancePermissionFilter] = Field(default_factory=list)
    instance_join_filters: dict[str, list[InstancePermissionFilter]] = Field(default_factory=dict)
    instance_select: list[ColumnRef] = Field(default_factory=list)
    instance_filters: list[InstanceQueryFilter] = Field(default_factory=list)
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

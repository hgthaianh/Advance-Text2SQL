"""Query IR shared by planning and upstream permission-filter injection."""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.shared.enums import Aggregation, FilterOperator, Intent, SortDirection
from src.shared.exceptions import (
    InvalidColumnError, MalformedQueryIRError, UnsupportedAggregationError,
    UnsupportedIntentError, UnsupportedOperatorError,
)

if TYPE_CHECKING:
    from src.schema.registry import SchemaRegistry

Scalar = str | int | float | bool | Decimal | datetime | date | None


class TableInstance(BaseModel):
    """A backend-whitelisted logical role for the initial employee-manager capability.

    This is deliberately not a general SQL table alias.  The physical table is
    fixed by the role, so callers cannot substitute a table or supply an alias.
    The employee-manager pair is compiled by the dedicated instance-aware
    planning path.  Other relationships remain physical-table joins until they
    have an equally explicit, permission-aware role contract.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    instance_id: Literal['employee_instance', 'manager_instance']
    semantic_role: Literal['employee', 'manager']

    @model_validator(mode='after')
    def validate_role_identity(self) -> 'TableInstance':
        expected = f'{self.semantic_role}_instance'
        if self.instance_id != expected:
            raise ValueError('Logical instance_id must match its semantic role')
        return self

    @property
    def physical_table(self) -> str:
        """Return the backend-defined physical table; this is never caller input."""
        return 'hr.employees'


class ColumnRef(BaseModel):
    """A future instance-qualified column reference, without SQL alias semantics."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    instance_id: Literal['employee_instance', 'manager_instance']
    column: str = Field(min_length=1, pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')


class InstanceJoinRequest(BaseModel):
    """Logical join input for the single employee-to-manager relationship.

    This is role/instance evidence, never a SQL alias or arbitrary SQL join.
    QueryPlanner resolves it to a registered relationship before it can enter a
    QueryPlan.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    source_instance_id: Literal['employee_instance', 'manager_instance']
    source_column: str = Field(min_length=1, pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')
    target_instance_id: Literal['employee_instance', 'manager_instance']
    target_column: str = Field(min_length=1, pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')
    join_type: Literal['left'] = 'left'


class InstancePermissionFilter(BaseModel):
    """A trusted row-policy predicate bound to a logical table instance.

    Physical-table and SQL-alias identity are intentionally absent.  The
    instance is resolved through QueryIR's backend-whitelisted TableInstance.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    instance_id: Literal['employee_instance', 'manager_instance']
    column: str = Field(min_length=1, pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')
    operator: FilterOperator = FilterOperator.EQ
    value: Scalar | list[Scalar]
    source: Literal['permission'] = 'permission'

    @field_validator('operator', mode='before')
    @classmethod
    def normalize_operator(cls, value: object) -> FilterOperator:
        return QueryFilter.normalize_operator(value)


class InstanceQueryFilter(BaseModel):
    """A user filter bound to a validated logical instance, not a SQL alias."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    instance_id: Literal['employee_instance', 'manager_instance']
    column: str = Field(min_length=1, pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')
    operator: FilterOperator = FilterOperator.EQ
    value: Scalar | list[Scalar]
    source: Literal['user'] = 'user'

    @field_validator('operator', mode='before')
    @classmethod
    def normalize_operator(cls, value: object) -> FilterOperator:
        return QueryFilter.normalize_operator(value)


class QueryFilter(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    column: str = Field(min_length=1)
    operator: FilterOperator = FilterOperator.EQ
    value: Scalar | list[Scalar]
    source: str = 'user'

    @field_validator('operator', mode='before')
    @classmethod
    def normalize_operator(cls, value: object) -> FilterOperator:
        aliases = {'=': 'eq', '!=': 'ne', '<>': 'ne', '>': 'gt', '>=': 'gte', '<': 'lt', '<=': 'lte'}
        key = str(value).lower()
        try:
            return FilterOperator(aliases.get(key, key))
        except ValueError as exc:
            raise UnsupportedOperatorError(f'Unsupported filter operator: {value}') from exc


# Existing callers keep QueryFilter; both names refer to the same model.
FilterCondition = QueryFilter


class SelectItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    column: str | None = None
    aggregation: Aggregation | None = None
    distinct: bool = False

    @field_validator('aggregation', mode='before')
    @classmethod
    def normalize_aggregation(cls, value: object) -> Aggregation | None:
        if value is None:
            return None
        try:
            return Aggregation(str(value).lower())
        except ValueError as exc:
            raise UnsupportedAggregationError(f'Unsupported aggregation: {value}') from exc


class HavingCondition(QueryFilter):
    """An aggregate comparison, never an arbitrary SQL expression."""

    aggregation: Aggregation

    @field_validator('aggregation', mode='before')
    @classmethod
    def normalize_aggregation(cls, value: object) -> Aggregation | None:
        return SelectItem.normalize_aggregation(value)


class OrderBy(BaseModel):
    model_config = ConfigDict(extra='forbid')

    column: str = Field(min_length=1)
    direction: SortDirection = SortDirection.ASC
    aggregation: Aggregation | None = None

    @field_validator('aggregation', mode='before')
    @classmethod
    def normalize_aggregation(cls, value: object) -> Aggregation | None:
        return SelectItem.normalize_aggregation(value)

    @field_validator('direction', mode='before')
    @classmethod
    def normalize_direction(cls, value: object) -> str:
        return str(value).lower()


class QueryIR(BaseModel):
    """Identifiers are untrusted until resolved against registry metadata."""

    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    intent: Intent = Intent.UNKNOWN
    tables: list[str] = Field(default_factory=list)
    select: list[str | ColumnRef | SelectItem] = Field(default_factory=list)
    filters: list[QueryFilter] = Field(default_factory=list)
    having: list[HavingCondition] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=1000, strict=True)
    top_k: int | None = Field(default=None, ge=1, le=1000, strict=True)

    joins: list[tuple[str, str]] = Field(default_factory=list)
    outer_joins: list[str] = Field(default_factory=list)
    # Metadata FK endpoint pairs selected from explicit business-role evidence.
    # They narrow an otherwise ambiguous shortest path; they never permit a
    # caller to supply arbitrary SQL join conditions.
    join_hints: list[tuple[str, str]] = Field(default_factory=list)
    # Logical instances are deliberately limited to the backend-owned
    # employee-manager capability. Legacy physical-table queries do not
    # populate this and continue through the unchanged planning path.
    table_instances: list[TableInstance] = Field(default_factory=list)
    # Optional Phase-2A logical join requests.  These are validated by the
    # planner but deliberately remain non-compilable until Phase 2B.
    instance_joins: list[InstanceJoinRequest] = Field(default_factory=list)
    # Trusted state injected by ScopedRowPolicy for the optional Phase-2B
    # logical join path.  Legacy queries continue to use ``filters`` only.
    instance_base_filters: list[InstancePermissionFilter] = Field(default_factory=list)
    instance_join_filters: dict[str, list[InstancePermissionFilter]] = Field(default_factory=dict)
    instance_filters: list[InstanceQueryFilter] = Field(default_factory=list)
    # Trusted permission predicates for an optional LEFT-joined destination.
    # They compile into the JOIN ON clause so row scope cannot turn the LEFT
    # join into an INNER join. User predicates always remain in ``filters``.
    outer_join_filters: dict[str, list[QueryFilter]] = Field(default_factory=dict)

    @field_validator('intent', mode='before')
    @classmethod
    def normalize_intent(cls, value: object) -> Intent:
        try:
            return Intent(str(value).lower())
        except ValueError as exc:
            raise UnsupportedIntentError(f'Unsupported intent: {value}') from exc

    @model_validator(mode='after')
    def unique_table_instance_ids(self) -> 'QueryIR':
        ids = [instance.instance_id for instance in self.table_instances]
        if len(ids) != len(set(ids)):
            raise ValueError('Logical table instance IDs must be unique')
        join_keys = [
            (join.source_instance_id, join.source_column, join.target_instance_id, join.target_column, join.join_type)
            for join in self.instance_joins
        ]
        if len(join_keys) != len(set(join_keys)):
            raise ValueError('Logical instance joins must be unique')
        return self

    def resolve_instance_column(self, reference: ColumnRef, registry: 'SchemaRegistry') -> str:
        """Resolve an instance reference to its physical registry column.

        This is an explicit validation boundary for the new optional models.
        It deliberately does not alter existing string-column planning or make
        logical instances executable until a later phase introduces an
        instance-aware query plan.
        """
        instances = {instance.instance_id: instance for instance in self.table_instances}
        instance = instances.get(reference.instance_id)
        if instance is None:
            raise MalformedQueryIRError(f'Unknown logical table instance: {reference.instance_id}')
        try:
            return registry.resolve_column(f'{instance.physical_table}.{reference.column}')
        except InvalidColumnError as exc:
            raise InvalidColumnError(
                f'Column {reference.column} does not belong to logical instance {reference.instance_id}'
            ) from exc


class TimePeriod(BaseModel):
    """Caller-resolved calendar interval; no clock or NLP is used here."""

    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, pattern=r'\S')
    start: date | datetime
    end: date | datetime

    @model_validator(mode='after')
    def validate_interval(self) -> 'TimePeriod':
        if isinstance(self.start, datetime):
            if not isinstance(self.end, datetime):
                raise ValueError('Period endpoints must use the same temporal type')
            if (self.start.utcoffset() is None) != (self.end.utcoffset() is None):
                raise ValueError('Period endpoints must use compatible timezone awareness')
            invalid_interval = self.start >= self.end
        else:
            if isinstance(self.end, datetime):
                raise ValueError('Period endpoints must use the same temporal type')
            invalid_interval = self.start >= self.end
        if invalid_interval:
            raise ValueError('Period must have start < end')
        return self


class PeriodComparison(BaseModel):
    """Two explicitly defined periods applied to the same aggregate query."""

    model_config = ConfigDict(extra='forbid')
    query: QueryIR
    date_column: str = Field(min_length=1)
    periods: list[TimePeriod] = Field(min_length=2, max_length=2)

    @model_validator(mode='after')
    def unique_labels(self) -> 'PeriodComparison':
        if self.periods[0].label == self.periods[1].label:
            raise ValueError('Comparison period labels must differ')
        return self

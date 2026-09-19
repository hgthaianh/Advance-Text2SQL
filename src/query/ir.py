"""Query IR shared by planning and upstream permission-filter injection."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.shared.enums import Aggregation, FilterOperator, Intent, SortDirection
from src.shared.exceptions import UnsupportedAggregationError, UnsupportedIntentError, UnsupportedOperatorError

Scalar = str | int | float | bool | Decimal | datetime | date | None


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

    @field_validator('aggregation', mode='before')
    @classmethod
    def normalize_aggregation(cls, value: object) -> Aggregation | None:
        if value is None:
            return None
        try:
            return Aggregation(str(value).lower())
        except ValueError as exc:
            raise UnsupportedAggregationError(f'Unsupported aggregation: {value}') from exc


class OrderBy(BaseModel):
    model_config = ConfigDict(extra='forbid')

    column: str = Field(min_length=1)
    direction: SortDirection = SortDirection.ASC

    @field_validator('direction', mode='before')
    @classmethod
    def normalize_direction(cls, value: object) -> str:
        return str(value).lower()


class QueryIR(BaseModel):
    """Identifiers are untrusted until resolved against registry metadata."""

    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    intent: Intent = Intent.UNKNOWN
    tables: list[str] = Field(default_factory=list)
    select: list[str | SelectItem] = Field(default_factory=list)
    filters: list[QueryFilter] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=1000, strict=True)

    joins: list[tuple[str, str]] = Field(default_factory=list)

    @field_validator('intent', mode='before')
    @classmethod
    def normalize_intent(cls, value: object) -> Intent:
        try:
            return Intent(str(value).lower())
        except ValueError as exc:
            raise UnsupportedIntentError(f'Unsupported intent: {value}') from exc

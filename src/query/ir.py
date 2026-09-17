from pydantic import BaseModel, Field

from src.shared.enums import FilterOperator, Intent, SortDirection

Scalar = str | int | float | bool | None


class QueryFilter(BaseModel):
    column: str = Field(min_length=1)
    operator: FilterOperator = FilterOperator.EQ
    value: Scalar | list[Scalar]


class OrderBy(BaseModel):
    column: str = Field(min_length=1)
    direction: SortDirection = SortDirection.ASC


class QueryIR(BaseModel):
    """Shared intermediate representation; identifiers are not trusted SQL."""

    intent: Intent = Intent.UNKNOWN
    tables: list[str] = Field(default_factory=list)
    select: list[str] = Field(default_factory=list)
    filters: list[QueryFilter] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=1000)

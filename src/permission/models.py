from pydantic import BaseModel, Field

from src.query.ir import QueryFilter


class PermissionContext(BaseModel):
    """Empty allowlists deny access; populate only from trusted policies."""

    allowed_tables: list[str] = Field(default_factory=list)
    allowed_columns: dict[str, list[str]] = Field(default_factory=dict)
    row_filters: list[QueryFilter] = Field(default_factory=list)

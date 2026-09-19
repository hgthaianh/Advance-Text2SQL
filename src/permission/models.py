from pydantic import BaseModel, Field

from src.query.ir import QueryFilter
from src.shared.enums import PermissionScope


class PermissionContext(BaseModel):
    """Empty allowlists deny access; populate only from trusted policies."""

    allowed_tables: list[str] = Field(default_factory=list)
    allowed_columns: dict[str, list[str]] = Field(default_factory=dict)
    denied_columns: list[str] = Field(default_factory=list)
    row_filters: list[QueryFilter] = Field(default_factory=list)
    scope: PermissionScope = PermissionScope.GLOBAL


from typing import Any

from pydantic import BaseModel, Field


class UserContext(BaseModel):
    user_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    department_id: str | None = None
    branch_id: str | None = None
    team_id: str | None = None


class SchemaColumn(BaseModel):
    name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    nullable: bool = True
    primary_key: bool = False
    aliases: list[str] = Field(default_factory=list)


class SchemaTable(BaseModel):
    name: str = Field(min_length=1)
    schema_name: str = "public"
    columns: list[SchemaColumn] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)


class QueryResult(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, ge=0)
    sql: str | None = None
    is_mock: bool = False
    message: str | None = None


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    user: UserContext

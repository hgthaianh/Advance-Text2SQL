from datetime import date
from decimal import Decimal
from typing import Any, Literal

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
    label: str | None = None
    description: str | None = None


class SchemaTable(BaseModel):
    name: str = Field(min_length=1)
    schema_name: str = "public"
    columns: list[SchemaColumn] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    label: str | None = None
    description: str | None = None


class SchemaRelationship(BaseModel):
    source: str
    target: str


class SchemaCandidate(BaseModel):
    """Ranked evidence, not a resolved schema choice or a calibrated probability."""

    name: str
    candidate_type: Literal["table", "column", "concept"]
    score: float = Field(ge=0, le=1)
    matched_text: str
    matched_alias: str
    match_type: Literal["exact", "accent_insensitive", "token", "prefix", "fuzzy", "related_business_concept"]
    alias_source: Literal["manual", "schema", "automatic"]
    reason: str | None = None


class SchemaLinkResult(BaseModel):
    tables: list[SchemaCandidate] = Field(default_factory=list)
    columns: list[SchemaCandidate] = Field(default_factory=list)
    concepts: list[SchemaCandidate] = Field(default_factory=list)
    unmatched_terms: list[str] = Field(default_factory=list)
    ambiguous_terms: list[str] = Field(default_factory=list)


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


class DateRange(BaseModel):
    """Calendar interval with inclusive start and exclusive end."""

    start: date
    end: date
    granularity: Literal["day", "week", "month", "quarter", "year", "range"]
    raw_text: str


class ParsedNumber(BaseModel):
    """Literal quantity; percentages retain percentage points (10% = 10)."""

    raw_text: str
    value: Decimal
    semantic_type: Literal["number", "money", "percentage"] = "number"


class NormalizedText(BaseModel):
    original_text: str
    normalized_text: str
    accent_stripped_text: str


class PreprocessedQuery(NormalizedText):
    date_range: DateRange | None = None
    numbers: list[ParsedNumber] = Field(default_factory=list)

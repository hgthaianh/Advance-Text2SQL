from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.shared.enums import Intent, ScopeDecision


class IntentResult(BaseModel):
    intent: Intent
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reason: str | None = None


class ScopeRoutingResult(BaseModel):
    """Stable, diagnostic-only result of deterministic request routing."""

    decision: ScopeDecision
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    matched_signals: list[str] = Field(default_factory=list)
    candidate_domains: list[str] = Field(default_factory=list)
    candidate_entities: list[str] = Field(default_factory=list)
    scope_hints: list[str] = Field(default_factory=list)


class UserContext(BaseModel):
    user_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    tenant_id: str | None = None
    employee_id: str | None = None
    department_id: str | None = None
    branch_id: str | None = None
    team_id: str | None = None


class SchemaColumn(BaseModel):
    name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    nullable: bool = True
    primary_key: bool = False
    foreign_key: str | None = None
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
    relationship_hints: list[tuple[str, str]] = Field(default_factory=list)
    ambiguous_concepts: list[str] = Field(default_factory=list)
    unmatched_terms: list[str] = Field(default_factory=list)
    ambiguous_terms: list[str] = Field(default_factory=list)


class SchemaRelationship(BaseModel):
    """Foreign key relationship, expressed as schema.table.column endpoints."""

    source: str
    target: str
    composite_keys: list[tuple[str, str]] = Field(default_factory=list)
    is_outer: bool = False
    target_role: str | None = None


class JoinAmbiguity(BaseModel):
    """Safe, optional guidance for a join path that needs clarification."""

    candidate_paths: list[list[str]] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)


class QueryResult(BaseModel):
    success: bool = True
    query_id: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, ge=0)
    truncated: bool = False
    scalar: Any | None = None
    sql: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    is_mock: bool = False
    error_code: str | None = None
    message: str | None = None
    ambiguity: JoinAmbiguity | None = None
    natural_language_response: str | None = None


class PublicQueryResult(BaseModel):
    """The safe business result returned by ``POST /query``.

    SQL text, bound values, execution internals, and authorization data never
    cross this API boundary.  Administrators inspect retained SQL through the
    separately authorized observability endpoints.
    """

    success: bool = True
    query_id: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, ge=0)
    truncated: bool = False
    scalar: Any | None = None
    natural_language_response: str | None = None


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    question: str = Field(min_length=1, max_length=2000, pattern=r"\S")


class ErrorResponse(BaseModel):
    """Stable public error response; it never includes SQL or parameter values."""

    query_id: str | None = None
    error_code: str
    message: str
    ambiguity: JoinAmbiguity | None = None


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
    intent_result: IntentResult | None = None


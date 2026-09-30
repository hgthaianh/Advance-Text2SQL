"""Thin production orchestration of existing deterministic pipeline stages."""

from datetime import date
import logging
from time import perf_counter
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from src.config import get_settings
from src.database.executor import ReadOnlyExecutor
from src.nlp.entity_extractor import DeterministicEntityExtractor, EntityExtractor
from src.nlp.ollama_semantic import OllamaSemanticReviewer, SemanticReviewer
from src.nlp.robustness import NLPRobustnessProcessor
from src.nlp.scope_router import ScopeRouter
from src.permission.column_policy import DefaultColumnPolicy
from src.permission.resolver import DefaultPermissionResolver, PermissionResolver
from src.permission.row_policy import ScopedRowPolicy
from src.pipeline.observability import (
    CompositePipelineObserver, LoggingPipelineObserver, PersistedPipelineObserver,
    PipelineEvent, PipelineObserver, elapsed_ms, safe_bind_params,
)
from src.query.handlers.basic import BasicQueryHandler
from src.query.ir_builder import QueryIRBuilder
from src.response.ollama import NaturalLanguageResponder, OllamaNaturalLanguageResponder
from src.schema.linker import SchemaLinker
from src.schema.registry import SchemaRegistry
from src.shared.exceptions import (
    AmbiguousJoinPathError, AmbiguousQueryError, FanoutRiskError, InvalidTableError, MissingEntityError,
    PermissionDeniedError, QueryExecutionError, QueryValidationError,
    SQLValidationError, UnsupportedQueryError,
)
from src.shared.enums import ScopeDecision
from src.shared.models import JoinAmbiguity, QueryResult, UserContext
from src.validation.sql_validator import ReadOnlySQLValidator, SQLValidator


class Text2SQLPipeline:
    """Compose NLP, linking, IR, permission, SQL and execution without route logic."""

    def __init__(self, *, registry: SchemaRegistry | None = None, engine: Engine | None = None,
                 nlp: NLPRobustnessProcessor | None = None, linker: SchemaLinker | None = None,
                 scope_router: ScopeRouter | None = None,
                 entity_extractor: EntityExtractor | None = None, ir_builder: QueryIRBuilder | None = None,
                 permission_resolver: PermissionResolver | None = None,
                 validator: SQLValidator | None = None, executor: ReadOnlyExecutor | None = None,
                 observer: PipelineObserver | None = None,
                 semantic_reviewer: SemanticReviewer | None = None,
                 responder: NaturalLanguageResponder | None = None) -> None:
        self.registry = registry or SchemaRegistry()
        self.nlp = nlp or NLPRobustnessProcessor()
        self.scope_router = scope_router or ScopeRouter()
        self.linker = linker or SchemaLinker(self.registry)
        if self.linker.registry is not self.registry:
            raise ValueError('Pipeline linker and registry must be identical')
        self.entity_extractor = entity_extractor or DeterministicEntityExtractor()
        self.ir_builder = ir_builder or QueryIRBuilder(self.registry)
        self.permission_resolver = permission_resolver or DefaultPermissionResolver(self.registry, require_tenant=True)
        self.row_policy = ScopedRowPolicy(self.registry, self.linker.graph)
        self.column_policy = DefaultColumnPolicy()
        self.handler = BasicQueryHandler(self.registry, self.linker.graph)
        self.validator = validator or ReadOnlySQLValidator()
        if executor is None:
            engine = engine or create_engine(get_settings().database_url, pool_pre_ping=True)
            executor = ReadOnlyExecutor(engine, timeout_ms=get_settings().query_timeout_ms)
        self.executor = executor
        self.semantic_reviewer = semantic_reviewer or OllamaSemanticReviewer()
        self.responder = responder or OllamaNaturalLanguageResponder()
        # A caller-provided observer remains untouched for tests/integrations.
        # The default combines existing logs with durable, redacted audit data.
        self.observer = observer or CompositePipelineObserver(
            LoggingPipelineObserver(), PersistedPipelineObserver(self.executor.engine),
        )

    def execute(self, question: str, user_context: UserContext, *, reference_date: date | None = None) -> QueryResult:
        """Return data or a controlled public failure; never expose a traceback."""
        query_id = uuid4().hex
        started_at = perf_counter()
        stage = 'preprocess'
        stage_started_at = started_at
        stage_durations: dict[str, float] = {}
        permissions = None
        compiled = None
        try:
            preprocessed = self.nlp.process_query(question, reference_date or date.today())
            stage, stage_started_at = self._next_stage(stage, stage_started_at, stage_durations, 'scope_routing')
            scope = self.scope_router.route(preprocessed)
            if scope.decision == ScopeDecision.OUT_OF_SCOPE:
                raise UnsupportedQueryError('Question is outside the database query scope')
            if scope.decision == ScopeDecision.AMBIGUOUS:
                raise AmbiguousQueryError('Question lacks enough database-query context')
            stage, stage_started_at = self._next_stage(stage, stage_started_at, stage_durations, 'schema_linking')
            links = self.linker.link(preprocessed)
            stage, stage_started_at = self._next_stage(stage, stage_started_at, stage_durations, 'entity_extraction')
            entities = self.entity_extractor.extract(preprocessed.normalized_text)
            stage, stage_started_at = self._next_stage(stage, stage_started_at, stage_durations, 'ir_construction')
            query = self.ir_builder.build(preprocessed, links, entities, user_context)
            stage, stage_started_at = self._next_stage(stage, stage_started_at, stage_durations, 'semantic_review')
            self.semantic_reviewer.validate(question, preprocessed, query)
            stage, stage_started_at = self._next_stage(stage, stage_started_at, stage_durations, 'permission_resolution')
            permissions = self.permission_resolver.resolve(user_context)
            query = self.row_policy.apply(query, permissions)
            # First planning discovers bridge tables; scope filters are then
            # expanded across every tenant-owned table in that validated path.
            preliminary_plan = self.handler.planner.plan(query)
            self._validate_table_access(preliminary_plan.tables, permissions.allowed_tables)
            query = self.row_policy.apply(query, permissions, tables=preliminary_plan.tables)
            stage, stage_started_at = self._next_stage(stage, stage_started_at, stage_durations, 'sql_compilation')
            self.column_policy.validate(query, permissions)
            compiled = self.handler.handle(query)
            stage, stage_started_at = self._next_stage(stage, stage_started_at, stage_durations, 'sql_validation')
            self.validator.validate(compiled.statement, permissions)
            stage, stage_started_at = self._next_stage(stage, stage_started_at, stage_durations, 'execution')
            result = self.executor.execute(compiled).model_copy(update={'query_id': query_id})
            answer = self._generate_response(question, result)
            if answer:
                result = result.model_copy(update={'natural_language_response': answer})
            stage_durations[stage] = elapsed_ms(stage_started_at)
            self._record(PipelineEvent(query_id=query_id, outcome='success', stage=stage,
                                       duration_ms=elapsed_ms(started_at), stage_durations_ms=stage_durations,
                                       question=question, sql=compiled.sql,
                                       row_count=result.row_count, truncated=result.truncated,
                                       safe_params=safe_bind_params(compiled.params)))
            return result
        except Exception as exc:  # The API contract deliberately converts all stage failures.
            stage_durations[stage] = elapsed_ms(stage_started_at)
            error_code = self._error_code(exc)
            self._record(PipelineEvent(query_id=query_id, outcome='failure', stage=stage,
                                       duration_ms=elapsed_ms(started_at), error_code=error_code,
                                       stage_durations_ms=stage_durations,
                                       question=question,
                                       sql=compiled.sql if compiled is not None else None,
                                       safe_params=safe_bind_params(compiled.params) if compiled is not None else {}))
            return QueryResult(
                success=False, query_id=query_id, error_code=error_code, message=self._message(exc),
                ambiguity=self._safe_ambiguity(exc, permissions),
            )

    @staticmethod
    def _next_stage(current: str, started_at: float, durations: dict[str, float], next_stage: str) -> tuple[str, float]:
        durations[current] = elapsed_ms(started_at)
        return next_stage, perf_counter()

    @staticmethod
    def _safe_ambiguity(exc: Exception, permissions) -> JoinAmbiguity | None:
        """Expose only already-authorized table names, never raw exception text."""
        if not isinstance(exc, AmbiguousJoinPathError) or permissions is None:
            return None
        paths = exc.candidate_paths
        if not paths:
            return None
        allowed = set(permissions.allowed_tables)
        if '*' not in allowed and any(table not in allowed for path in paths for table in path):
            return None
        return JoinAmbiguity(
            candidate_paths=paths,
            hints=[
                'Specify the business relationship that should be used.',
                'Add an intermediate table or relationship to clarify the request.',
            ],
        )

    def _record(self, event: PipelineEvent) -> None:
        """Observability must not change a completed query's public outcome."""
        try:
            self.observer.record(event)
        except Exception:
            logging.getLogger('text2sql.pipeline').exception('pipeline observer failed')

    def _generate_response(self, question: str, result: QueryResult) -> str | None:
        """A response-provider outage can never turn successful execution into failure."""
        try:
            return self.responder.generate(question, result)
        except Exception:
            # Provider exceptions may include transport details; never log them.
            logging.getLogger('text2sql.response').warning('natural-language response generation failed')
            return None

    def _validate_table_access(self, tables: list[str], allowed_tables: list[str]) -> None:
        """Fail closed for requested and planner-added bridge tables."""
        if '*' in allowed_tables:
            return
        if not allowed_tables:
            raise PermissionDeniedError('Empty allowlist denies table access')
        try:
            allowed = {self.registry.resolve_table(table) for table in allowed_tables}
        except InvalidTableError as exc:
            raise PermissionDeniedError('Permission policy contains an unknown table') from exc
        denied = sorted(set(tables) - allowed)
        if denied:
            raise PermissionDeniedError(f"Access denied to table '{denied[0]}'")

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, PermissionDeniedError):
            return 'PERMISSION_DENIED'
        if isinstance(exc, (AmbiguousQueryError, AmbiguousJoinPathError, FanoutRiskError)):
            return 'AMBIGUOUS_QUERY'
        if isinstance(exc, MissingEntityError):
            return 'MISSING_ENTITY'
        if isinstance(exc, UnsupportedQueryError):
            return 'UNSUPPORTED_QUERY'
        if isinstance(exc, SQLValidationError):
            return 'SQL_VALIDATION_ERROR'
        if isinstance(exc, QueryExecutionError):
            return 'SQL_EXECUTION_ERROR'
        if isinstance(exc, QueryValidationError):
            return 'INVALID_QUERY_IR'
        return 'SQL_EXECUTION_ERROR'

    @staticmethod
    def _message(exc: Exception) -> str:
        if isinstance(exc, QueryExecutionError):
            return 'Query execution could not be completed.'
        if isinstance(exc, (QueryValidationError, PermissionDeniedError)):
            return str(exc)
        return 'The query could not be completed.'

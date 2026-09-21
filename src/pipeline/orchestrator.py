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
from src.nlp.robustness import NLPRobustnessProcessor
from src.nlp.scope_router import ScopeRouter
from src.permission.column_policy import DefaultColumnPolicy
from src.permission.resolver import DefaultPermissionResolver, PermissionResolver
from src.permission.row_policy import ScopedRowPolicy
from src.pipeline.observability import LoggingPipelineObserver, PipelineEvent, PipelineObserver, elapsed_ms
from src.query.handlers.basic import BasicQueryHandler
from src.query.ir_builder import QueryIRBuilder
from src.schema.linker import SchemaLinker
from src.schema.registry import SchemaRegistry
from src.shared.exceptions import (
    AmbiguousJoinPathError, AmbiguousQueryError, MissingEntityError,
    PermissionDeniedError, QueryExecutionError, QueryValidationError,
    SQLValidationError, UnsupportedQueryError,
)
from src.shared.enums import ScopeDecision
from src.shared.models import QueryResult, UserContext
from src.validation.sql_validator import ReadOnlySQLValidator, SQLValidator


class Text2SQLPipeline:
    """Compose NLP, linking, IR, permission, SQL and execution without route logic."""

    def __init__(self, *, registry: SchemaRegistry | None = None, engine: Engine | None = None,
                 nlp: NLPRobustnessProcessor | None = None, linker: SchemaLinker | None = None,
                 scope_router: ScopeRouter | None = None,
                 entity_extractor: EntityExtractor | None = None, ir_builder: QueryIRBuilder | None = None,
                 permission_resolver: PermissionResolver | None = None,
                 validator: SQLValidator | None = None, executor: ReadOnlyExecutor | None = None,
                 observer: PipelineObserver | None = None) -> None:
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
        self.observer = observer or LoggingPipelineObserver()

    def execute(self, question: str, user_context: UserContext, *, reference_date: date | None = None) -> QueryResult:
        """Return data or a controlled public failure; never expose a traceback."""
        query_id = uuid4().hex
        started_at = perf_counter()
        stage = 'preprocess'
        try:
            preprocessed = self.nlp.process_query(question, reference_date or date.today())
            stage = 'scope_routing'
            scope = self.scope_router.route(preprocessed)
            if scope.decision == ScopeDecision.OUT_OF_SCOPE:
                raise UnsupportedQueryError('Question is outside the database query scope')
            if scope.decision == ScopeDecision.AMBIGUOUS:
                raise AmbiguousQueryError('Question lacks enough database-query context')
            stage = 'schema_linking'
            links = self.linker.link(preprocessed)
            stage = 'entity_extraction'
            entities = self.entity_extractor.extract(question)
            stage = 'ir_construction'
            query = self.ir_builder.build(preprocessed, links, entities)
            stage = 'permission_resolution'
            permissions = self.permission_resolver.resolve(user_context)
            query = self.row_policy.apply(query, permissions)
            # First planning discovers bridge tables; scope filters are then
            # expanded across every tenant-owned table in that validated path.
            preliminary_plan = self.handler.planner.plan(query)
            query = self.row_policy.apply(query, permissions, tables=preliminary_plan.tables)
            stage = 'sql_compilation'
            self.column_policy.validate(query, permissions)
            compiled = self.handler.handle(query)
            stage = 'sql_validation'
            self.validator.validate(compiled.statement, permissions)
            stage = 'execution'
            result = self.executor.execute(compiled).model_copy(update={'query_id': query_id})
            self._record(PipelineEvent(query_id=query_id, outcome='success', stage=stage,
                                       duration_ms=elapsed_ms(started_at)))
            return result
        except Exception as exc:  # The API contract deliberately converts all stage failures.
            error_code = self._error_code(exc)
            self._record(PipelineEvent(query_id=query_id, outcome='failure', stage=stage,
                                       duration_ms=elapsed_ms(started_at), error_code=error_code))
            return QueryResult(success=False, query_id=query_id, error_code=error_code, message=self._message(exc))

    def _record(self, event: PipelineEvent) -> None:
        """Observability must not change a completed query's public outcome."""
        try:
            self.observer.record(event)
        except Exception:
            logging.getLogger('text2sql.pipeline').exception('pipeline observer failed')

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, PermissionDeniedError):
            return 'PERMISSION_DENIED'
        if isinstance(exc, (AmbiguousQueryError, AmbiguousJoinPathError)):
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

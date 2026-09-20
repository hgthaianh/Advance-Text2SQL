"""Thin production orchestration of existing deterministic pipeline stages."""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from src.config import get_settings
from src.database.executor import ReadOnlyExecutor
from src.nlp.entity_extractor import DeterministicEntityExtractor, EntityExtractor
from src.nlp.robustness import NLPRobustnessProcessor
from src.permission.column_policy import DefaultColumnPolicy
from src.permission.resolver import DefaultPermissionResolver, PermissionResolver
from src.permission.row_policy import ScopedRowPolicy
from src.query.handlers.basic import BasicQueryHandler
from src.query.ir_builder import QueryIRBuilder
from src.schema.linker import SchemaLinker
from src.schema.registry import SchemaRegistry
from src.shared.exceptions import (
    AmbiguousJoinPathError, AmbiguousQueryError, MissingEntityError,
    PermissionDeniedError, QueryExecutionError, QueryValidationError,
    SQLValidationError, UnsupportedQueryError,
)
from src.shared.models import QueryResult, UserContext
from src.validation.sql_validator import ReadOnlySQLValidator, SQLValidator


class Text2SQLPipeline:
    """Compose NLP, linking, IR, permission, SQL and execution without route logic."""

    def __init__(self, *, registry: SchemaRegistry | None = None, engine: Engine | None = None,
                 nlp: NLPRobustnessProcessor | None = None, linker: SchemaLinker | None = None,
                 entity_extractor: EntityExtractor | None = None, ir_builder: QueryIRBuilder | None = None,
                 permission_resolver: PermissionResolver | None = None,
                 validator: SQLValidator | None = None, executor: ReadOnlyExecutor | None = None) -> None:
        self.registry = registry or SchemaRegistry()
        self.nlp = nlp or NLPRobustnessProcessor()
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

    def execute(self, question: str, user_context: UserContext, *, reference_date: date | None = None) -> QueryResult:
        """Return data or a controlled public failure; never expose a traceback."""
        try:
            preprocessed = self.nlp.process_query(question, reference_date or date.today())
            links = self.linker.link(preprocessed)
            entities = self.entity_extractor.extract(question)
            query = self.ir_builder.build(preprocessed, links, entities)
            permissions = self.permission_resolver.resolve(user_context)
            query = self.row_policy.apply(query, permissions)
            # First planning discovers bridge tables; scope filters are then
            # expanded across every tenant-owned table in that validated path.
            preliminary_plan = self.handler.planner.plan(query)
            query = self.row_policy.apply(query, permissions, tables=preliminary_plan.tables)
            self.column_policy.validate(query, permissions)
            compiled = self.handler.handle(query)
            self.validator.validate(compiled.statement, permissions)
            return self.executor.execute(compiled)
        except Exception as exc:  # The API contract deliberately converts all stage failures.
            return QueryResult(success=False, error_code=self._error_code(exc), message=self._message(exc))

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

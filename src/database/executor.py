from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import Select
from sqlalchemy.orm import Session

from src.query.models import CompiledQuery
from src.shared.exceptions import QueryExecutionError
from src.shared.models import QueryResult


class QueryExecutor(ABC):
    @abstractmethod
    def execute(self, statement: Select, session: Session) -> QueryResult:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError


class ReadOnlyExecutor:
    """Execute a validated parameterized SELECT using a short-lived connection."""

    def __init__(self, engine: Engine, *, max_rows: int = 1000, timeout_ms: int = 5000) -> None:
        if max_rows < 1 or not 100 <= timeout_ms <= 60000:
            raise ValueError('max_rows must be positive and timeout_ms must be 100-60000')
        self.engine = engine
        self.max_rows = max_rows
        self.timeout_ms = timeout_ms

    def execute(self, query: CompiledQuery) -> QueryResult:
        if not isinstance(query, CompiledQuery):
            raise QueryExecutionError('Executor requires a compiled query')
        try:
            with self.engine.connect() as connection:
                if connection.dialect.name == 'postgresql':
                    # Integer configuration is validated in __init__, never user input.
                    connection.exec_driver_sql(f'SET LOCAL statement_timeout = {self.timeout_ms}')
                result = connection.execute(query.statement, query.params)
                fetched = result.mappings().fetchmany(self.max_rows + 1)
                truncated = len(fetched) > self.max_rows
                rows = [dict(row) for row in fetched[:self.max_rows]]
                columns = list(result.keys())
        except SQLAlchemyError as exc:
            raise QueryExecutionError('Read-only query execution failed') from exc
        scalar: Any | None = rows[0][columns[0]] if len(rows) == 1 and len(columns) == 1 else None
        return QueryResult(success=True, columns=columns, rows=rows, row_count=len(rows), scalar=scalar,
                           truncated=truncated)

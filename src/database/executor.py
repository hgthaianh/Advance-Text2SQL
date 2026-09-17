from abc import ABC, abstractmethod

from sqlalchemy.sql import Select
from sqlalchemy.orm import Session
from src.shared.models import QueryResult


class QueryExecutor(ABC):
    @abstractmethod
    def execute(self, statement: Select, session: Session) -> QueryResult:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

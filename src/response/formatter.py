from abc import ABC, abstractmethod

from src.shared.models import QueryResult


class ResponseFormatter(ABC):
    @abstractmethod
    def format(self, result: QueryResult) -> QueryResult:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

from abc import ABC, abstractmethod

from sqlalchemy.sql import Select
from src.query.ir import QueryIR


class SQLBuilder(ABC):
    @abstractmethod
    def build(self, query: QueryIR) -> Select:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

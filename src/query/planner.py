from abc import ABC, abstractmethod

from src.query.ir import QueryIR
from src.permission.models import PermissionContext


class QueryPlanner(ABC):
    @abstractmethod
    def plan(self, query: QueryIR, permissions: PermissionContext) -> QueryIR:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

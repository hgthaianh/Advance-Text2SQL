from abc import ABC, abstractmethod

from src.query.ir import QueryIR
from src.permission.models import PermissionContext


class RowPolicy(ABC):
    @abstractmethod
    def apply(self, query: QueryIR, permissions: PermissionContext) -> QueryIR:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

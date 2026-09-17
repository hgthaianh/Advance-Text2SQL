from abc import ABC, abstractmethod

from src.query.ir import QueryIR
from src.permission.models import PermissionContext


class ColumnPolicy(ABC):
    @abstractmethod
    def validate(self, query: QueryIR, permissions: PermissionContext) -> None:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

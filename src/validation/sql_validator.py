from abc import ABC, abstractmethod

from sqlalchemy.sql import Select
from src.permission.models import PermissionContext


class SQLValidator(ABC):
    @abstractmethod
    def validate(self, statement: Select, permissions: PermissionContext) -> None:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

from abc import ABC, abstractmethod

from src.shared.models import UserContext
from src.permission.models import PermissionContext


class PermissionResolver(ABC):
    @abstractmethod
    def resolve(self, user: UserContext) -> PermissionContext:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

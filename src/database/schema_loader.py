from abc import ABC, abstractmethod

from sqlalchemy import Engine
from src.shared.models import SchemaTable


class SchemaLoader(ABC):
    @abstractmethod
    def load(self, engine: Engine) -> list[SchemaTable]:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

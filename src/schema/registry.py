from abc import ABC, abstractmethod

from src.shared.models import SchemaTable


class SchemaRegistry(ABC):
    @abstractmethod
    def get_table(self, name: str) -> SchemaTable | None:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

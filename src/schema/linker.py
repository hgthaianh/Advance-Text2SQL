from abc import ABC, abstractmethod

from src.shared.models import SchemaTable


class SchemaLinker(ABC):
    @abstractmethod
    def link(self, text: str, tables: list[SchemaTable]) -> list[str]:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

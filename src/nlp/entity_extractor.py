from abc import ABC, abstractmethod

from pydantic import BaseModel


class Entity(BaseModel):
    text: str
    label: str
    start: int
    end: int


class EntityExtractor(ABC):
    @abstractmethod
    def extract(self, text: str) -> list[Entity]:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

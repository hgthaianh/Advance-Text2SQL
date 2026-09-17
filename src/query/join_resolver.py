from abc import ABC, abstractmethod

from src.schema.relationship_graph import RelationshipGraph


class JoinResolver(ABC):
    @abstractmethod
    def resolve(self, tables: list[str], graph: RelationshipGraph) -> list[tuple[str, str]]:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

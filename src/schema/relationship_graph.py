from abc import ABC, abstractmethod




class RelationshipGraph(ABC):
    @abstractmethod
    def find_path(self, source: str, target: str) -> list[tuple[str, str]]:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

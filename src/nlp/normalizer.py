from abc import ABC, abstractmethod




class Normalizer(ABC):
    @abstractmethod
    def normalize(self, text: str) -> str:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

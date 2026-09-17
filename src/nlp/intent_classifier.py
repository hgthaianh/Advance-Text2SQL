from abc import ABC, abstractmethod

from src.shared.enums import Intent


class IntentClassifier(ABC):
    @abstractmethod
    def classify(self, text: str) -> Intent:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

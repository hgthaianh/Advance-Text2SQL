from abc import ABC, abstractmethod

from datetime import date


class DateParser(ABC):
    @abstractmethod
    def parse(self, text: str, reference_date: date) -> tuple[date, date] | None:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError

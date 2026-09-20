"""Small deterministic business-value extractor used by the production bridge."""

from abc import ABC, abstractmethod
import re
from typing import Any

from pydantic import BaseModel, Field

from src.nlp.normalizer import Normalizer, strip_accents


class ExtractedEntity(BaseModel):
    """A semantic value, deliberately independent from database columns or SQL."""

    text: str
    normalized_value: Any
    entity_type: str
    start: int | None = None
    end: int | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class Entity(BaseModel):
    """Legacy entity shape retained for older callers."""

    text: str
    label: str
    start: int
    end: int


class EntityExtractor(ABC):
    @abstractmethod
    def extract(self, text: str) -> list[ExtractedEntity]:
        """Return semantic values without selecting schema columns."""
        raise NotImplementedError


class DeterministicEntityExtractor(EntityExtractor):
    """Recognize a conservative set of common Vietnamese business values."""

    _VALUES: tuple[tuple[str, str, str], ...] = (
        ('vip', 'VIP', 'customer_type'),
        ('da hoan thanh', 'COMPLETED', 'status'),
        ('hoan thanh', 'COMPLETED', 'status'),
        ('cho xu ly', 'PENDING', 'status'),
        ('dang xu ly', 'PENDING', 'status'),
        ('huy', 'CANCELLED', 'status'),
        ('hcm', 'Hồ Chí Minh', 'branch_name'),
        ('ho chi minh', 'Hồ Chí Minh', 'branch_name'),
        ('ha noi', 'Hà Nội', 'branch_name'),
    )
    _DEPARTMENT = re.compile(r'\b(?:phong|bo phan)\s+([\w\s]{2,40}?)(?=\s+(?:tai|theo|va|thang|nam)\b|$)')

    def __init__(self, normalizer: Normalizer | None = None) -> None:
        self.normalizer = normalizer or Normalizer()

    def extract(self, text: str) -> list[ExtractedEntity]:
        normalized = self.normalizer.normalize(text).normalized_text
        folded = strip_accents(normalized)
        entities: list[ExtractedEntity] = []
        for surface, value, entity_type in self._VALUES:
            match = re.search(rf'(?<!\w){re.escape(surface)}(?!\w)', folded)
            if match:
                entities.append(ExtractedEntity(
                    text=normalized[match.start():match.end()], normalized_value=value,
                    entity_type=entity_type, start=match.start(), end=match.end(), confidence=1.0,
                ))
        for match in self._DEPARTMENT.finditer(folded):
            value = match.group(1).strip()
            if value:
                entities.append(ExtractedEntity(
                    text=normalized[match.start(1):match.end(1)], normalized_value=value.title(),
                    entity_type='department_name', start=match.start(1), end=match.end(1), confidence=0.9,
                ))
        return entities

"""Deterministic catalog and code extraction for the production pipeline."""

from abc import ABC, abstractmethod
import re
from typing import Any

from pydantic import BaseModel, Field

from src.nlp.entity_catalog import EntityCatalog
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
    """Extract exact catalog values and explicitly labelled business codes."""

    _CODE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ('branch_code', re.compile(r'\b(?:ma\s+chi\s+nhanh|branch\s+code|cn)\b\s*[:#-]?\s*([a-z][a-z0-9_-]*)\b')),
        ('department_code', re.compile(r'\b(?:ma\s+(?:phong\s+ban|bo\s+phan)|department\s+code|pb)\b\s*[:#-]?\s*([a-z][a-z0-9_-]*)\b')),
        ('product_code', re.compile(r'\b(?:ma\s+san\s+pham|product\s+code|sp)\b\s*[:#-]?\s*([a-z][a-z0-9_-]*)\b')),
        ('customer_code', re.compile(r'\b(?:ma\s+khach\s+hang|customer\s+code|kh)\b\s*[:#-]?\s*([a-z][a-z0-9_-]*)\b')),
    )

    def __init__(self, normalizer: Normalizer | None = None, catalog: EntityCatalog | None = None) -> None:
        self.normalizer = normalizer or Normalizer()
        self.catalog = catalog or EntityCatalog.default()

    def extract(self, text: str) -> list[ExtractedEntity]:
        normalized = self.normalizer.normalize(text).normalized_text
        folded = strip_accents(normalized)
        entities: list[ExtractedEntity] = []
        for match in self.catalog.match(normalized):
            entities.append(ExtractedEntity(
                text=normalized[match.start:match.end], normalized_value=match.value,
                entity_type=match.entity_type, start=match.start, end=match.end, confidence=1.0,
            ))
        for entity_type, pattern in self._CODE_PATTERNS:
            for match in pattern.finditer(folded):
                start, end = match.span(1)
                entities.append(ExtractedEntity(
                    text=normalized[start:end], normalized_value=match.group(1).upper(),
                    entity_type=entity_type, start=start, end=end, confidence=1.0,
                ))
        return sorted(entities, key=lambda item: (item.start or 0, item.end or 0, item.entity_type))

"""Deterministic catalog matching for business values mentioned in a question.

The catalog contains canonical values and aliases only. It does not know
database columns, issue SQL, or make fuzzy guesses; the IR builder maps its
typed entities to schema fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from src.business_semantics import VIP_CUSTOMER_ALIASES, VIP_CUSTOMER_VALUE
from src.nlp.normalizer import Normalizer, strip_accents


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """A canonical business value and the phrases that identify it."""

    entity_type: str
    value: str
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogMatch:
    entity_type: str
    value: str
    start: int
    end: int


class EntityCatalog:
    """In-memory exact-match catalog, injectable for each deployment/tenant."""

    def __init__(self, entries: Iterable[CatalogEntry] = ()) -> None:
        self.entries = tuple(entries)
        seen: set[tuple[str, str]] = set()
        alias_targets: dict[str, tuple[str, str]] = {}
        normalizer = Normalizer()
        for entry in self.entries:
            if not entry.aliases:
                raise ValueError(f'Catalog entry {entry.entity_type}:{entry.value} has no aliases')
            key = (entry.entity_type, entry.value)
            if key in seen:
                raise ValueError(f'Duplicate catalog value: {entry.entity_type}:{entry.value}')
            seen.add(key)
            for alias in entry.aliases:
                surface = strip_accents(normalizer.normalize(alias).normalized_text)
                if not surface:
                    raise ValueError(f'Empty catalog alias: {entry.entity_type}:{entry.value}')
                previous = alias_targets.setdefault(surface, key)
                if previous != key:
                    raise ValueError(f'Ambiguous catalog alias: {alias}')
        self._surfaces = tuple(
            (entry.entity_type, entry.value, strip_accents(normalizer.normalize(alias).normalized_text))
            for entry in self.entries for alias in entry.aliases
        )

    def match(self, text: str) -> list[CatalogMatch]:
        """Return non-overlapping longest exact matches in deterministic order."""
        folded = strip_accents(Normalizer().normalize(text).normalized_text)
        candidates: list[CatalogMatch] = []
        for entity_type, value, surface in self._surfaces:
            for found in re.finditer(rf'(?<!\w){re.escape(surface)}(?!\w)', folded):
                candidates.append(CatalogMatch(entity_type, value, found.start(), found.end()))
        candidates.sort(key=lambda item: (item.start, -(item.end - item.start), item.entity_type, item.value))
        accepted: list[CatalogMatch] = []
        occupied: list[tuple[int, int]] = []
        for item in candidates:
            if any(item.start < end and start < item.end for start, end in occupied):
                continue
            accepted.append(item)
            occupied.append((item.start, item.end))
        return accepted

    @classmethod
    def default(cls) -> 'EntityCatalog':
        """Small safe baseline; deployments add their own values at startup."""
        return cls((
            CatalogEntry('branch_name', 'Hồ Chí Minh', ('hcm', 'ho chi minh', 'hồ chí minh')),
            CatalogEntry('branch_name', 'Hà Nội', ('ha noi', 'hà nội')),
            CatalogEntry('department_name', 'Kinh doanh', ('kinh doanh', 'sales')),
            CatalogEntry('department_name', 'Nhân sự', ('nhan su', 'nhân sự', 'hr')),
            CatalogEntry('department_name', 'Kế toán', ('ke toan', 'kế toán', 'accounting')),
            CatalogEntry('vip_flag', VIP_CUSTOMER_VALUE, VIP_CUSTOMER_ALIASES),
            CatalogEntry('customer_tier', 'STANDARD', ('standard',)),
            CatalogEntry('customer_tier', 'SILVER', ('silver',)),
            CatalogEntry('customer_tier', 'GOLD', ('gold',)),
            CatalogEntry('customer_tier', 'PLATINUM', ('platinum',)),
            CatalogEntry('priority', 'LOW', ('low',)),
            CatalogEntry('priority', 'NORMAL', ('normal',)),
            CatalogEntry('priority', 'HIGH', ('high',)),
            CatalogEntry('priority', 'URGENT', ('urgent',)),
            CatalogEntry('status', 'COMPLETED', ('da hoan thanh', 'đã hoàn thành', 'hoan thanh', 'hoàn thành', 'completed')),
            CatalogEntry('status', 'PENDING', ('cho xu ly', 'chờ xử lý', 'dang xu ly', 'đang xử lý', 'pending')),
            CatalogEntry('status', 'CANCELLED', ('huy', 'hủy', 'cancelled', 'canceled')),
            CatalogEntry('status', 'DRAFT', ('nhap', 'nháp', 'draft')),
            CatalogEntry('status', 'ACTIVE', ('active',)),
            CatalogEntry('status', 'INACTIVE', ('inactive',)),
            CatalogEntry('status', 'APPROVED', ('approved',)),
            CatalogEntry('status', 'REJECTED', ('rejected',)),
            CatalogEntry('status', 'CONFIRMED', ('da xac nhan', 'đã xác nhận', 'xac nhan', 'xác nhận', 'confirmed')),
            CatalogEntry('status', 'OPEN', ('open',)),
            CatalogEntry('status', 'OVERDUE', ('overdue',)),
            CatalogEntry('status', 'PAID', ('paid',)),
            CatalogEntry('status', 'POSTED', ('posted',)),
            CatalogEntry('status', 'PARTIAL', ('partial',)),
            CatalogEntry('status', 'RECEIVED', ('received',)),
            CatalogEntry('status', 'RESOLVED', ('resolved',)),
        ))

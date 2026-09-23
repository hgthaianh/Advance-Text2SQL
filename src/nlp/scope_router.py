"""Deterministic scope routing before schema linking.

This module decides whether a request is a database question.  It deliberately
does not inspect schema aliases or choose tables, so lexical overlap alone
cannot send general conversation into the schema linker.
"""

import re

from src.nlp.normalizer import Normalizer
from src.shared.enums import ScopeDecision
from src.shared.models import PreprocessedQuery, ScopeRoutingResult


_DATA_ACTIONS = frozenset((
    'bao nhieu', 'tong', 'trung binh', 'top', 'cao nhat', 'thap nhat',
    'danh sach', 'liet ke', 'list', 'cho xem', 'thong tin', 'tim', 'tra cuu',
    'trang thai', 'trong thang', 'tu ngay', 'den ngay', 'theo ',
))
_DATA_ENTITIES = frozenset((
    'nhan vien', 'khach hang', 'san pham', 'don hang', 'hoa don',
    'thanh toan', 'chi nhanh', 'phong ban', 'nha cung cap', 'kho',
    'doanh thu', 'ngan sach', 'but toan', 'vai tro', 'nguoi dung', 'luong',
    'yeu cau mua hang', 'giao dich kho', 'ticket ho tro', 'thuong hieu',
    'don nghi phep', 'don mua hang', 'lo hang', 'dieu chuyen kho', 'team',
    'contact', 'quotation',
    'salary', 'so dien thoai', 'email', 'ton kho', 'gia ban',
    'tai khoan', 'department', 'branch', 'employee', 'customer',
    'employees', 'customers', 'product', 'products', 'order', 'orders',
    'invoice', 'invoices', 'status',
))
_OUT_OF_SCOPE = frozenset((
    'thoi tiet', 'du bao thoi tiet', 'tin tuc', 'dich status', 'dich giup',
    'viet giup', 'huong dan', 'cach lam', 'tai sao', 'giai thich',
    'dinh nghia', 'la gi', 'nghia la gi', 'cong thuc', 'bai hat',
    'am nhac', 'nau an', 'suc khoe', 'phim',
))
_IDENTIFIER_MARKERS = frozenset(('ma', 'code', 'id'))
_IDENTIFIER = re.compile(r'\b[a-z]{1,4}\d{2,}\b')
_STATUS_VALUES = frozenset(('pending', 'completed', 'hoan thanh', 'cho xu ly', 'active', 'inactive'))
_DIRECT_RECORD_ENTITIES = frozenset((
    'nhan vien', 'khach hang', 'san pham', 'don hang', 'hoa don',
    'chi nhanh', 'phong ban', 'employee', 'employees', 'customer',
    'customers', 'product', 'products', 'order', 'orders', 'invoice', 'invoices',
))
_TEMPORAL_FILTERS = frozenset(('thang', 'nam', 'quy', 'tuan', 'hom nay', 'hom qua'))


class ScopeRouter:
    """Small interpretable classifier with conservative three-way decisions."""

    def __init__(self, normalizer: Normalizer | None = None) -> None:
        self.normalizer = normalizer or Normalizer()

    def route(self, text: str | PreprocessedQuery) -> ScopeRoutingResult:
        normalized = text.accent_stripped_text if isinstance(text, PreprocessedQuery) else self.normalizer.normalize(text).accent_stripped_text
        normalized = f' {normalized.strip()} '
        if not normalized.strip():
            return ScopeRoutingResult(decision=ScopeDecision.AMBIGUOUS, confidence=0.0, reasons=['EMPTY_REQUEST'])

        negative = self._matches(normalized, _OUT_OF_SCOPE)
        actions = self._matches(normalized, _DATA_ACTIONS)
        entities = self._matches(normalized, _DATA_ENTITIES)
        identifiers = self._matches(normalized, _IDENTIFIER_MARKERS)
        statuses = self._matches(normalized, _STATUS_VALUES)
        temporal = self._matches(normalized, _TEMPORAL_FILTERS)
        if _IDENTIFIER.search(normalized):
            identifiers.append('record_identifier')
        signals = [*(f'NEG:{value}' for value in negative), *(f'ACTION:{value}' for value in actions),
                   *(f'ENTITY:{value}' for value in entities), *(f'IDENTIFIER:{value}' for value in identifiers)]
        signals.extend(f'STATUS:{value}' for value in statuses)
        signals.extend(f'TIME:{value}' for value in temporal)

        # Explanation/how-to/general-knowledge structure wins even when it
        # contains a business noun such as account, manager, or HCM.
        if negative:
            return ScopeRoutingResult(decision=ScopeDecision.OUT_OF_SCOPE, confidence=0.97,
                                      reasons=['NON_DATA_REQUEST'], matched_signals=signals)
        if actions and entities:
            return ScopeRoutingResult(decision=ScopeDecision.IN_SCOPE, confidence=0.96,
                                      reasons=['QUERY_ACTION_AND_ENTITY'], matched_signals=signals)
        if entities and identifiers:
            return ScopeRoutingResult(decision=ScopeDecision.IN_SCOPE, confidence=0.90,
                                      reasons=['ENTITY_WITH_IDENTIFIER'], matched_signals=signals)
        if any(entity != 'status' for entity in entities) and statuses:
            return ScopeRoutingResult(decision=ScopeDecision.IN_SCOPE, confidence=0.86,
                                      reasons=['ENTITY_WITH_STATUS_FILTER'], matched_signals=signals)
        if entities and temporal:
            return ScopeRoutingResult(decision=ScopeDecision.IN_SCOPE, confidence=0.84,
                                      reasons=['ENTITY_WITH_TEMPORAL_FILTER'], matched_signals=signals)
        if len(entities) == 1 and entities[0] in _DIRECT_RECORD_ENTITIES:
            return ScopeRoutingResult(decision=ScopeDecision.IN_SCOPE, confidence=0.78,
                                      reasons=['DIRECT_RECORD_SUBJECT'], matched_signals=signals)
        if len(entities) >= 2:
            return ScopeRoutingResult(decision=ScopeDecision.IN_SCOPE, confidence=0.82,
                                      reasons=['MULTIPLE_BUSINESS_ENTITIES'], matched_signals=signals)
        return ScopeRoutingResult(decision=ScopeDecision.AMBIGUOUS, confidence=0.45,
                                  reasons=['INSUFFICIENT_DATABASE_EVIDENCE'], matched_signals=signals)

    @staticmethod
    def _matches(text: str, phrases: frozenset[str]) -> list[str]:
        return sorted(phrase.strip() for phrase in phrases
                      if re.search(rf'(?<!\w){re.escape(phrase.strip())}(?!\w)', text))

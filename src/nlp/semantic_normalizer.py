"""Controlled semantic normalization for Vietnamese ERP language."""

from __future__ import annotations

from dataclasses import dataclass
import re

from src.nlp.normalizer import Normalizer, strip_accents


@dataclass(frozen=True, slots=True)
class BusinessConcept:
    domain: str
    entity: str
    aliases: tuple[str, ...]


# One shared vocabulary drives routing signals.  Aliases describe business
# concepts, not individual benchmark sentences.
BUSINESS_CONCEPTS = (
    BusinessConcept('hr', 'employee', ('nhan vien', 'nhan su', 'employee', 'employees', 'staff')),
    BusinessConcept('hr', 'department', ('phong ban', 'phong nhan su', 'bo phan', 'department')),
    BusinessConcept('support', 'ticket', ('ticket', 'ticket ho tro', 'support ticket')),
    BusinessConcept('finance', 'vendor_bill', ('vendor bill', 'hoa don nha cung cap')),
    BusinessConcept('finance', 'cost_center', ('cost center', 'trung tam chi phi')),
    BusinessConcept('finance', 'journal_entry', ('but toan', 'journal entry')),
    BusinessConcept('finance', 'account', ('tai khoan ke toan', 'chart of account')),
    BusinessConcept('inventory', 'stock_serial', ('serial', 'stock serial')),
    BusinessConcept('inventory', 'stock_lot', ('lo hang', 'stock lot')),
    BusinessConcept('inventory', 'inventory', ('ton kho', 'giao dich kho', 'warehouse', 'kho')),
    BusinessConcept('inventory', 'stock_transfer', ('dieu chuyen kho', 'stock transfer')),
    BusinessConcept('procurement', 'supplier', ('nha cung cap', 'supplier')),
    BusinessConcept('procurement', 'purchase_order', ('purchase order', 'don mua hang', ' po ')),
    BusinessConcept('procurement', 'goods_receipt', ('hang da nhan', 'goods receipt')),
    BusinessConcept('sales', 'sales_order', ('don hang', 'sales order')),
    BusinessConcept('sales', 'invoice', ('hoa don', 'invoice')),
    BusinessConcept('sales', 'payment', ('thanh toan', 'payment')),
    BusinessConcept('customer', 'customer', ('khach hang', 'customer')),
    BusinessConcept('product', 'product', ('san pham', 'product')),
    BusinessConcept('org', 'branch', ('chi nhanh', 'branch')),
    BusinessConcept('iam', 'user', ('nguoi dung', 'user')),
    BusinessConcept('audit', 'audit_log', ('audit log', 'text2sql log')),
)

_TOKEN_FIXES = {
    'bn': 'bao nhieu',
    'nv': 'nhan vien',
    'kh': 'khach hang',
    'ncc': 'nha cung cap',
    'po': 'purchase order',
    'ds': 'danh sach',
    'dt': 'doanh thu',
    'hd': 'hop dong',
    'pb': 'phong ban',
    'cn': 'chi nhanh',
    'phog': 'phong',
}

_PHRASE_FIXES = (
    (r'\bhead\s*count\b', 'bao nhieu nhan vien'),
    (r'\bso luong nhan (?:vien|su)\b', 'bao nhieu nhan vien'),
    (r'\btong so nhan (?:vien|su)\b', 'bao nhieu nhan vien'),
    (r'\bso nguoi\b', 'bao nhieu nhan vien'),
    (r'\bmay nguoi\b', 'bao nhieu nhan vien'),
    (r'\bdem nhan su\b', 'dem nhan vien'),
    (r'\bcount nhan vien\b', 'dem nhan vien'),
)


class SemanticNormalizer:
    """Normalize only controlled ERP vocabulary; never fuzzy-match a sentence."""

    def __init__(self, normalizer: Normalizer | None = None) -> None:
        self.normalizer = normalizer or Normalizer()

    def normalize(self, text: str) -> str:
        folded = strip_accents(self.normalizer.normalize(text).normalized_text)
        words = [_TOKEN_FIXES.get(word, word) for word in folded.split()]
        normalized = ' '.join(words)
        for pattern, replacement in _PHRASE_FIXES:
            normalized = re.sub(pattern, replacement, normalized)

        # In an explicit HR/department context, Vietnamese “người” denotes the
        # employee population.  Outside that controlled context it is untouched.
        if re.search(r'\b(?:hr|nhan su|phong|bo phan)\b', normalized):
            normalized = re.sub(r'\bnguoi\b', 'nhan vien', normalized)
        normalized = re.sub(r'\bhr\b', 'phong ban nhan su', normalized)
        return ' '.join(normalized.split())

    def concepts(self, text: str) -> tuple[list[str], list[str]]:
        normalized = f' {self.normalize(text)} '
        domains: list[str] = []
        entities: list[str] = []
        for concept in BUSINESS_CONCEPTS:
            if any(re.search(rf'(?<!\w){re.escape(alias.strip())}(?!\w)', normalized)
                   for alias in concept.aliases):
                if concept.domain not in domains:
                    domains.append(concept.domain)
                if concept.entity not in entities:
                    entities.append(concept.entity)
        return domains, entities

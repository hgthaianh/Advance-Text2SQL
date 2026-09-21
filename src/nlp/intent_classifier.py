import logging
import re
from abc import ABC, abstractmethod

from src.nlp.normalizer import Normalizer
from src.shared.enums import Intent
from src.shared.models import IntentResult

logger = logging.getLogger(__name__)


class IntentClassifier(ABC):
    @abstractmethod
    def classify(self, text: str) -> IntentResult:
        """Classify a natural-language query into a structured IntentResult."""
        raise NotImplementedError


class RuleBasedIntentClassifier(IntentClassifier):
    """Deterministic Vietnamese Intent Classifier using pattern matching and normalization."""

    def __init__(self, normalizer: Normalizer | None = None) -> None:
        self.normalizer = normalizer or Normalizer()

    def classify(self, text: str) -> IntentResult:
        """Classify query text safely with error handling and fallback."""
        if not text or not text.strip():
            logger.warning("Empty or blank query provided for intent classification.")
            return IntentResult(
                intent=Intent.UNKNOWN,
                confidence=0.0,
                reason="Query is empty or whitespace-only.",
            )

        try:
            normalized = self.normalizer.normalize(text)
            norm_text = normalized.normalized_text
            accent_stripped = normalized.accent_stripped_text

            # Rule ordering:
            # 1. TREND (changes over time, fluctuations, historical movement)
            trend_patterns = [
                r"thay doi (nhu )?the nao", r"bien dong", r"xu huong", r"tang giam",
                r"qua cac thang", r"qua cac nam", r"qua cac quy", r"theo thoi gian",
            ]
            for pat in trend_patterns:
                if re.search(pat, accent_stripped):
                    return IntentResult(
                        intent=Intent.TREND,
                        confidence=0.92,
                        reason=f"Matched trend pattern '{pat}'.",
                    )

            # 2. COMPARISON (compare across entities, branches, categories)
            comparison_patterns = [
                r"so sanh", r"doi chieu", r"khac nhau (nhu )?the nao", r"hon hay kem", r"so ssnah"
            ]
            for pat in comparison_patterns:
                if re.search(pat, accent_stripped):
                    return IntentResult(
                        intent=Intent.COMPARISON,
                        confidence=0.92,
                        reason=f"Matched comparison pattern '{pat}'.",
                    )

            # 3. TOP_N (ranking, highest, lowest, best selling)
            top_n_patterns = [
                r"\btop\b", r"ban chay nhat", r"cao nhat", r"nhieu nhat",
                r"thap nhat", r"it nhat", r"xep hang", r"hang dau", r"lon nhat", r"nho nhat"
            ]
            for pat in top_n_patterns:
                if re.search(pat, accent_stripped):
                    return IntentResult(
                        intent=Intent.TOP_N,
                        confidence=0.95,
                        reason=f"Matched top-N ranking pattern '{pat}'.",
                    )

            # 4. AVERAGE (mean / average calculations)
            avg_patterns = [
                r"trung binh", r"binh quan", r"muc trung binh", r"trung binh cong"
            ]
            for pat in avg_patterns:
                if re.search(pat, accent_stripped):
                    return IntentResult(
                        intent=Intent.AVERAGE,
                        confidence=0.95,
                        reason=f"Matched average pattern '{pat}'.",
                    )

            # 5. REVENUE (financial revenue inquiries take precedence over general question words)
            revenue_patterns = [
                r"doanh thu", r"doanh so", r"tong thu", r"thu nhap"
            ]
            for pat in revenue_patterns:
                if re.search(pat, accent_stripped):
                    return IntentResult(
                        intent=Intent.REVENUE,
                        confidence=0.92,
                        reason=f"Matched revenue keyword '{pat}'.",
                    )

            # 6. SUM (summation of non-revenue values)
            sum_patterns = [
                r"tong so tien", r"tong chi phi", r"tong gia tri", r"tong thanh toan",
                r"tinh tong", r"\btong\b"
            ]
            for pat in sum_patterns:
                if re.search(pat, accent_stripped):
                    return IntentResult(
                        intent=Intent.SUM,
                        confidence=0.90,
                        reason=f"Matched sum calculation keyword '{pat}'.",
                    )

            # 7. COUNT (counting entities / quantity inquiries)
            count_patterns = [
                r"co bao nhieu", r"\bso luong\b", r"\bdem\b", r"dem so", r"\bbao nhieu\b"
            ]
            for pat in count_patterns:
                if re.search(pat, accent_stripped):
                    return IntentResult(
                        intent=Intent.COUNT,
                        confidence=0.92,
                        reason=f"Matched count pattern '{pat}'.",
                    )

            # 8. DETAIL (specific record/entity information lookup)
            detail_patterns = [
                r"thong tin", r"chi tiet", r"ho so", r"cho toi thong tin"
            ]
            for pat in detail_patterns:
                if re.search(pat, accent_stripped):
                    return IntentResult(
                        intent=Intent.DETAIL,
                        confidence=0.90,
                        reason=f"Matched detail lookup pattern '{pat}'.",
                    )

            # 9. LIST / FILTER (list items, filter criteria)
            list_patterns = [
                r"danh sach", r"liet ke", r"\bnhung\b", r"\bcac\b"
            ]
            for pat in list_patterns:
                if re.search(pat, accent_stripped):
                    return IntentResult(
                        intent=Intent.LIST,
                        confidence=0.88,
                        reason=f"Matched list/filter pattern '{pat}'.",
                    )

            # Default fallback for unknown intents
            return IntentResult(
                intent=Intent.UNKNOWN,
                confidence=0.50,
                reason="No matching intent rules found for query.",
            )

        except Exception as exc:
            logger.error("Intent classification failed: %s", type(exc).__name__, exc_info=True)
            return IntentResult(
                intent=Intent.UNKNOWN,
                confidence=0.0,
                reason="Intent classification failed.",
            )

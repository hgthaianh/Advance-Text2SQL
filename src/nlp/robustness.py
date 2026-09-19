"""NLP Robustness processor handling accentless text, synonyms, typos, and relative date expressions."""

from datetime import date
from typing import Sequence

from src.nlp.date_parser import DateParser
from src.nlp.intent_classifier import RuleBasedIntentClassifier
from src.nlp.normalizer import Normalizer, strip_accents
from src.nlp.number_parser import NumberParser
from src.shared.models import PreprocessedQuery

# Common Vietnamese business/Text2SQL typos and abbreviations dictionary
COMMON_TYPOS_AND_ABBREVIATIONS: dict[str, str] = {
    # Typos for intentions / actions
    "dnah sach": "danh sách",
    "dnah ssnah": "danh sách",
    "danh ssnah": "danh sách",
    "so ssnah": "so sánh",
    "liet kee": "liệt kê",
    "xem chi tiiet": "xem chi tiết",
    "tong chi phii": "tổng chi phí",
    # Common abbreviations & synonyms
    "nv": "nhân viên",
    "nhan vienn": "nhân viên",
    "nhan vien": "nhân viên",
    "kh": "khách hàng",
    "khach hnag": "khách hàng",
    "khach hang": "khách hàng",
    "ncc": "nhà cung cấp",
    "po": "purchase order",
    "ds": "danh sách",
    "dt": "doanh thu",
    "doanh so": "doanh thu",
    "don hnag": "đơn hàng",
    "don hang": "đơn hàng",
    "hd": "hợp đồng",
    "hop dong": "hợp đồng",
    "pb": "phòng ban",
    "phong ban": "phòng ban",
    "cn": "chi nhánh",
    "chi nhanh": "chi nhánh",
}


def compute_levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return compute_levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


class NLPRobustnessProcessor:
    """Robustness processor enhancing queries against accentless Vietnamese, typos, synonyms, and relative dates."""

    def __init__(
        self,
        normalizer: Normalizer | None = None,
        date_parser: DateParser | None = None,
        intent_classifier: RuleBasedIntentClassifier | None = None,
        number_parser: NumberParser | None = None,
    ) -> None:
        self.normalizer = normalizer or Normalizer()
        self.date_parser = date_parser or DateParser()
        self.intent_classifier = intent_classifier or RuleBasedIntentClassifier(self.normalizer)
        self.number_parser = number_parser or NumberParser()

    def correct_typos_and_abbreviations(self, text: str) -> str:
        """Replace common typos and abbreviations deterministically."""
        norm = self.normalizer.normalize(text)
        accentless = norm.accent_stripped_text
        words = accentless.split()

        corrected_words: list[str] = []
        for word in words:
            if word in COMMON_TYPOS_AND_ABBREVIATIONS:
                corrected_words.append(COMMON_TYPOS_AND_ABBREVIATIONS[word])
            else:
                corrected_words.append(word)

        corrected_text = " ".join(corrected_words)
        for typo, correction in COMMON_TYPOS_AND_ABBREVIATIONS.items():
            if " " in typo and typo in accentless:
                corrected_text = corrected_text.replace(typo, correction)

        return corrected_text

    def fuzzy_match(self, term: str, candidates: Sequence[str], max_distance: int = 2) -> str | None:
        """Find the best candidate match within max_distance edit distance."""
        term_clean = strip_accents(term).lower()
        best_match: str | None = None
        min_dist = max_distance + 1

        for candidate in candidates:
            cand_clean = strip_accents(candidate).lower()
            dist = compute_levenshtein_distance(term_clean, cand_clean)
            if dist < min_dist:
                min_dist = dist
                best_match = candidate

        return best_match if min_dist <= max_distance else None

    def process_query(self, text: str, reference_date: date) -> PreprocessedQuery:
        """Preprocess a natural language query with accent normalization, typo correction, date parsing, and intent classification."""
        corrected_text = self.correct_typos_and_abbreviations(text)
        norm = self.normalizer.normalize(text)

        # Parse date range using DateParser
        date_range = self.date_parser.parse(text, reference_date)
        if date_range is None and text != corrected_text:
            date_range = self.date_parser.parse(corrected_text, reference_date)

        # Classify intent
        intent_result = self.intent_classifier.classify(text)
        if intent_result.intent.value == "unknown" and text != corrected_text:
            intent_result = self.intent_classifier.classify(corrected_text)

        # Parse numbers
        numbers = self.number_parser.parse(text)
        if not numbers and text != corrected_text:
            numbers = self.number_parser.parse(corrected_text)

        return PreprocessedQuery(
            original_text=text,
            normalized_text=norm.normalized_text,
            accent_stripped_text=norm.accent_stripped_text,
            date_range=date_range,
            numbers=numbers,
            intent_result=intent_result,
        )

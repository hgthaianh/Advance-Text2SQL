"""Vietnamese text cleanup without guessing missing diacritics."""

import re
import unicodedata
from datetime import date

from src.shared.models import NormalizedText, PreprocessedQuery


def strip_accents(text: str) -> str:
    """Remove combining marks and map Vietnamese đ for insensitive matching."""
    return "".join(
        char for char in unicodedata.normalize("NFD", text)
        if not unicodedata.combining(char)
    ).translate(str.maketrans("đĐ", "dD"))


class Normalizer:
    def normalize(self, text: str) -> NormalizedText:
        """Fold case, Unicode, whitespace and common typographic punctuation.

        Numeric separators, signs, slashes and percent signs are preserved.
        Repeated sentence punctuation is collapsed to one character.
        """
        normalized = unicodedata.normalize("NFC", text).lower()
        normalized = normalized.translate(str.maketrans({
            "“": '"', "”": '"', "‘": "'", "’": "'",
            "–": "-", "—": "-", "…": ".",
        }))
        normalized = re.sub(r"([!?.,;:])\1+", r"\1", normalized)
        normalized = " ".join(normalized.split())
        return NormalizedText(
            original_text=text, normalized_text=normalized,
            accent_stripped_text=strip_accents(normalized),
        )


def preprocess_query(text: str, *, reference_date: date) -> PreprocessedQuery:
    """Normalize a query and parse its first date interval and literal numbers."""
    from src.nlp.date_parser import DateParser
    from src.nlp.number_parser import NumberParser

    normalized = Normalizer().normalize(text)
    return PreprocessedQuery(
        **normalized.model_dump(),
        date_range=DateParser().parse(normalized.normalized_text, reference_date),
        numbers=NumberParser().parse(normalized.normalized_text),
    )

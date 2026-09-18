"""Parse numeric literals without inferring business entities or columns."""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from src.nlp.date_parser import date_expressions
from src.nlp.normalizer import strip_accents
from src.shared.models import ParsedNumber

_NUMBER = re.compile(
    r"(?<![\w.,/])[-+]?\d+(?:[.,]\d+)*(?!\w|[.,/]\d)"
    r"(?:\s*(%|phan\s+tram\b|nghin\b|ngan\b|trieu\b|ty\b|ti\b))?"
    r"(?:\s*(?:vnd\b|dong\b|₫))?"
)
_MAGNITUDES = {"nghin": 1000, "ngan": 1000, "trieu": 10**6, "ty": 10**9, "ti": 10**9}


def _decimal(literal: str) -> Decimal:
    """Three-digit groups are thousands; otherwise the last separator is decimal."""
    if re.fullmatch(r"[-+]?\d{1,3}(?:,\d{3})+|[-+]?\d{1,3}(?:\.\d{3})+", literal):
        return Decimal(literal.replace(",", "").replace(".", ""))
    if "," in literal and "." in literal:
        decimal = "," if literal.rfind(",") > literal.rfind(".") else "."
        grouping = "." if decimal == "," else ","
        integer, fraction = literal.rsplit(decimal, 1)
        if not re.fullmatch(rf"[-+]?\d{{1,3}}(?:\{grouping}\d{{3}})+", integer):
            raise InvalidOperation
        return Decimal(integer.replace(grouping, "") + "." + fraction)
    return Decimal(literal.replace(",", "."))


class NumberParser:
    def parse(self, text: str) -> list[ParsedNumber]:
        """Return literals in order, excluding date expressions.

        Bare magnitudes remain quantities; explicit currency marks money.
        Percentages are percentage points. raw_text preserves NFC spelling.
        """
        text = unicodedata.normalize("NFC", text)
        matching = strip_accents(text).lower()
        date_spans = [match.span() for match in date_expressions(text)]
        results = []
        for match in _NUMBER.finditer(matching):
            if any(match.start() < end and match.end() > start for start, end in date_spans):
                continue
            unit = match.group(1)
            literal = re.match(r"[-+]?\d+(?:[.,]\d+)*", match.group()).group()
            try:
                value = _decimal(literal)
            except InvalidOperation:
                continue
            semantic_type = "number"
            if unit in _MAGNITUDES:
                value *= _MAGNITUDES[unit]
            elif unit:
                semantic_type = "percentage"
            if semantic_type != "percentage" and re.search(r"(?:vnd|dong|₫)$", match.group()):
                semantic_type = "money"
            results.append(ParsedNumber(
                raw_text=text[match.start():match.end()], value=value, semantic_type=semantic_type,
            ))
        return results

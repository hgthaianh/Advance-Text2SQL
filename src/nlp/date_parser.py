"""Deterministic Vietnamese calendar expressions; weeks start on Monday."""

import re
import unicodedata
from collections.abc import Iterator
from datetime import date, timedelta

from src.nlp.normalizer import strip_accents
from src.shared.models import DateRange

_DAY = r"(?:\d{1,2}/\d{1,2}/\d{4}|\d{1,2}-\d{1,2}-\d{4}|\d{1,2}\s+thang\s+\d{1,2}\s+nam\s+\d{4})"
_MONTH = r"thang\s+\d{1,2}(?:\s+nam\s+\d{4})?"
_ATOM = rf"(?:{_DAY}|{_MONTH}|quy\s+\d+(?:\s+nam\s+\d{{4}})?|nam\s+\d{{4}}|hom\s+(?:nay|qua)|(?:tuan|thang)\s+(?:nay|truoc)|nam\s+(?:nay|truoc))"
_EXPRESSION = re.compile(
    rf"(?<![\w/\-])(?:(?:tu\s+)?{_ATOM}\s+den\s+{_ATOM}|{_ATOM})(?![\w/\-])"
)


def date_expressions(text: str) -> Iterator[re.Match[str]]:
    """Yield date spans in NFC text, including syntactically matched invalid dates."""
    return _EXPRESSION.finditer(strip_accents(unicodedata.normalize("NFC", text)).lower())


def _month_start(year: int, month: int, offset: int = 0) -> date:
    index = year * 12 + month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def _interval(expression: str, reference: date) -> tuple[date, date, str]:
    numbers = [int(value) for value in re.findall(r"\d+", expression)]
    if re.fullmatch(_DAY, expression):
        day, month, year = numbers
        start = date(year, month, day)
        return start, start + timedelta(days=1), "day"
    previous = expression.endswith("truoc")
    if expression.startswith("hom"):
        start = reference - timedelta(days=int(expression.endswith("qua")))
        return start, start + timedelta(days=1), "day"
    if expression.startswith("tuan"):
        start = reference - timedelta(days=reference.weekday() + 7 * previous)
        return start, start + timedelta(days=7), "week"
    if expression.startswith("thang"):
        month = numbers[0] if numbers else reference.month
        year = numbers[1] if len(numbers) > 1 else reference.year
        start = date(year, month, 1)
        if not numbers and previous:
            start = _month_start(year, month, -1)
        return start, _month_start(start.year, start.month, 1), "month"
    if expression.startswith("quy"):
        quarter = numbers[0]
        if not 1 <= quarter <= 4:
            raise ValueError("Quarter must be between 1 and 4")
        year = numbers[1] if len(numbers) > 1 else reference.year
        start = date(year, quarter * 3 - 2, 1)
        return start, _month_start(year, start.month, 3), "quarter"
    year = numbers[0] if numbers else reference.year - previous
    return date(year, 1, 1), date(year + 1, 1, 1), "year"


class DateParser:
    def parse(self, text: str, reference_date: date) -> DateRange | None:
        """Return the first interval, or None for invalid/no dates.

        Missing years use reference_date.year. Range endpoints are inclusive
        calendar periods; an explicit year applies to both when absent.
        Reversed ranges are invalid; no implicit year rollover is performed.
        """
        text = unicodedata.normalize("NFC", text)
        match = next(date_expressions(text), None)
        if match is None:
            return None
        expression = match.group()
        try:
            endpoints = re.split(r"\s+den\s+", re.sub(r"^tu\s+", "", expression))
            if len(endpoints) == 2:
                years = re.findall(r"(?:nam\s+|[/\-])(\d{4})\b", expression)
                reference = date(int(years[-1]), 1, 1) if years else reference_date
                start, _, _ = _interval(endpoints[0], reference)
                _, end, _ = _interval(endpoints[1], reference)
                if start >= end:
                    return None
                granularity = "range"
            else:
                start, end, granularity = _interval(expression, reference_date)
        except (ValueError, OverflowError):
            return None
        return DateRange(
            start=start, end=end, granularity=granularity,
            raw_text=text[match.start():match.end()],
        )

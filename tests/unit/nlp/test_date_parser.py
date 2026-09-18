from datetime import date

import pytest

from src.nlp.date_parser import DateParser
from src.nlp.normalizer import strip_accents

REFERENCE = date(2026, 9, 18)


@pytest.mark.parametrize("text, start, end, granularity", [
    ("hôm nay", "2026-09-18", "2026-09-19", "day"),
    ("hôm qua", "2026-09-17", "2026-09-18", "day"),
    ("tuần này", "2026-09-14", "2026-09-21", "week"),
    ("tuần trước", "2026-09-07", "2026-09-14", "week"),
    ("tháng này", "2026-09-01", "2026-10-01", "month"),
    ("tháng trước", "2026-08-01", "2026-09-01", "month"),
    ("tháng 8", "2026-08-01", "2026-09-01", "month"),
    ("tháng 8 năm 2026", "2026-08-01", "2026-09-01", "month"),
    ("quý 1", "2026-01-01", "2026-04-01", "quarter"),
    ("quý 2", "2026-04-01", "2026-07-01", "quarter"),
    ("quý 3", "2026-07-01", "2026-10-01", "quarter"),
    ("quý 4", "2026-10-01", "2027-01-01", "quarter"),
    ("quý 3 năm 2026", "2026-07-01", "2026-10-01", "quarter"),
    ("năm nay", "2026-01-01", "2027-01-01", "year"),
    ("năm trước", "2025-01-01", "2026-01-01", "year"),
    ("năm 2025", "2025-01-01", "2026-01-01", "year"),
    ("18/09/2026", "2026-09-18", "2026-09-19", "day"),
    ("18-09-2026", "2026-09-18", "2026-09-19", "day"),
    ("18 tháng 9 năm 2026", "2026-09-18", "2026-09-19", "day"),
    ("từ tháng 7 đến tháng 9", "2026-07-01", "2026-10-01", "range"),
    ("từ tháng 7 đến tháng 9 năm 2024", "2024-07-01", "2024-10-01", "range"),
    ("từ 01/08/2026 đến 31/08/2026", "2026-08-01", "2026-09-01", "range"),
    ("01/08/2026 đến 31/08/2026", "2026-08-01", "2026-09-01", "range"),
    ("29/02/2024", "2024-02-29", "2024-03-01", "day"),
])
def test_dates(text, start, end, granularity):
    for variant in (text, strip_accents(text), text.upper()):
        result = DateParser().parse("doanh thu " + variant, REFERENCE)
        assert (result.start, result.end, result.granularity) == (date.fromisoformat(start), date.fromisoformat(end), granularity)
        assert result.raw_text == variant


@pytest.mark.parametrize("text", ["", "không có ngày", "31/02/2026", "tháng 13", "quý 5", "29/02/2025", "từ tháng 9 đến tháng 7", "năm 9999", "abc18/09/2026"])
def test_invalid_dates(text):
    assert DateParser().parse(text, REFERENCE) is None


@pytest.mark.parametrize("text, reference, start, end", [
    ("tháng trước", date(2026, 1, 1), date(2025, 12, 1), date(2026, 1, 1)),
    ("hôm qua", date(2026, 1, 1), date(2025, 12, 31), date(2026, 1, 1)),
    ("tháng trước", date(2024, 3, 1), date(2024, 2, 1), date(2024, 3, 1)),
    ("tuần này", date(2026, 9, 20), date(2026, 9, 14), date(2026, 9, 21)),
    ("tuần này", date(2026, 1, 1), date(2025, 12, 29), date(2026, 1, 5)),
])
def test_calendar_boundaries(text, reference, start, end):
    result = DateParser().parse(text, reference)
    assert (result.start, result.end) == (start, end)


def test_range_whitespace():
    result = DateParser().parse("từ tháng 7\tđến   tháng 9", REFERENCE)
    assert (result.start, result.end) == (date(2026, 7, 1), date(2026, 10, 1))


def test_first_date_only():
    assert DateParser().parse("tháng 7 và tháng 8", REFERENCE).raw_text == "tháng 7"

from decimal import Decimal

import pytest

from src.nlp.number_parser import NumberParser
from src.nlp.normalizer import strip_accents


@pytest.mark.parametrize("text, value, kind", [
    ("5", "5", "number"), ("10", "10", "number"), ("1000", "1000", "number"),
    ("1,000", "1000", "number"), ("1.000", "1000", "number"),
    ("10 nghìn", "10000", "number"), ("100 nghìn", "100000", "number"),
    ("1 triệu", "1000000", "number"), ("10 triệu", "10000000", "number"),
    ("1.5 triệu", "1500000", "number"), ("1,5 triệu", "1500000", "number"),
    ("2 tỷ", "2000000000", "number"), ("1.2 tỷ", "1200000000", "number"),
    ("1.5 tỷ", "1500000000", "number"),
    ("10%", "10", "percentage"), ("20 phần trăm", "20", "percentage"),
    ("1.000.000", "1000000", "number"), ("1,000,000", "1000000", "number"),
    ("1.234,56", "1234.56", "number"), ("1,234.56", "1234.56", "number"),
    ("-1,5 triệu", "-1500000", "number"), ("0", "0", "number"),
    ("0,1%", "0.1", "percentage"), ("10 triệu đồng", "10000000", "money"),
    ("500 VND", "500", "money"),
])
def test_numbers(text, value, kind):
    for variant in (text, strip_accents(text), text.upper()):
        result = NumberParser().parse(variant)
        assert len(result) == 1
        assert (result[0].raw_text, result[0].value, result[0].semantic_type) == (variant, Decimal(value), kind)


@pytest.mark.parametrize("text, raw, value", [
    ("top 5", "5", 5), ("5 sản phẩm", "5", 5),
    ("trên 10 triệu", "10 triệu", 10_000_000),
    ("dưới 5 triệu", "5 triệu", 5_000_000),
    ("top 5.", "5", 5), ("5, rồi tiếp tục", "5", 5),
])
def test_query_numbers(text, raw, value):
    result = NumberParser().parse(text)
    assert [(n.raw_text, n.value) for n in result] == [(raw, value)]


@pytest.mark.parametrize("text", ["", "không có số", "abc123", "123abc", "1,23,45", "31/02/2026", "18-09-2026", "tháng 8 năm 2026", "quý 3"])
def test_no_quantity(text):
    assert NumberParser().parse(text) == []


def test_multiple_numbers_and_dates():
    result = NumberParser().parse("top 5 từ tháng 7 đến tháng 9 năm 2026 trên 10 triệu và 20%")
    assert [n.value for n in result] == [5, 10_000_000, 20]

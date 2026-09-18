import unicodedata
from datetime import date

import pytest

from src.nlp.normalizer import Normalizer, preprocess_query


@pytest.mark.parametrize("text, expected", [
    ("Cho TOI HCM", "cho toi hcm"),
    ("  a   b\t c\n", "a b c"),
    ("xin!!! chào???", "xin! chào?"),
    ('“Chào”—‘bạn’…', '\"chào\"-\'bạn\'.'),
    ("Doanh thu chi nhánh HCM", "doanh thu chi nhánh hcm"),
    ("ban", "ban"),
    ("", ""),
    (" \t\n", ""),
    ("1,5 triệu 18/09/2026 10%", "1,5 triệu 18/09/2026 10%"),
    (unicodedata.normalize("NFD", "THÁNG TRƯỚC"), "tháng trước"),
])
def test_normalize(text, expected):
    result = Normalizer().normalize(text)
    assert result.original_text == text
    assert result.normalized_text == expected
    assert Normalizer().normalize(expected).normalized_text == expected


@pytest.mark.parametrize("accented, plain", [
    ("chi nhánh", "chi nhanh"), ("ĐỒNG", "dong"),
    ("tháng trước", "thang truoc"), ("doanh thu chi nhánh HCM", "doanh thu chi nhanh hcm"),
])
def test_accent_insensitive(accented, plain):
    assert Normalizer().normalize(accented).accent_stripped_text == Normalizer().normalize(plain).accent_stripped_text


def test_preprocess():
    text = " Cho toi doanh thu chi nhánh HCM thang truoc tren 10 trieu "
    result = preprocess_query(text, reference_date=date(2026, 9, 18))
    assert result.original_text == text
    assert result.date_range.start == date(2026, 8, 1)
    assert result.date_range.end == date(2026, 9, 1)
    assert result.date_range.raw_text == "thang truoc"
    assert [(n.raw_text, n.value) for n in result.numbers] == [("10 trieu", 10_000_000)]
    assert result.model_dump(mode="json")["date_range"]["start"] == "2026-08-01"


def test_preprocess_excludes_dates():
    result = preprocess_query("top 5 trong tháng 8 năm 2026 trên 10 triệu", reference_date=date(2026, 9, 18))
    assert [n.value for n in result.numbers] == [5, 10_000_000]


def test_preprocess_empty():
    result = preprocess_query("", reference_date=date(2026, 9, 18))
    assert result.date_range is None
    assert result.numbers == []

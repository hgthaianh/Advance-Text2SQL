from datetime import date
import pytest

from src.nlp.robustness import NLPRobustnessProcessor, compute_levenshtein_distance
from src.nlp.scope_router import ScopeRouter
from src.shared.enums import Intent
from src.shared.enums import ScopeDecision


@pytest.fixture
def processor() -> NLPRobustnessProcessor:
    return NLPRobustnessProcessor()


def test_levenshtein_distance():
    assert compute_levenshtein_distance("nhan vien", "nhan vien") == 0
    assert compute_levenshtein_distance("nhan vienn", "nhan vien") == 1
    assert compute_levenshtein_distance("dnah sach", "danh sach") == 2
    assert compute_levenshtein_distance("abc", "xyz") == 3


def test_typo_and_abbreviation_correction(processor: NLPRobustnessProcessor):
    corrected = processor.correct_typos_and_abbreviations("dnah sach nv")
    assert "danh sách" in corrected
    assert "nhân viên" in corrected

    corrected_kh = processor.correct_typos_and_abbreviations("dnah ssnah kh")
    assert "danh sách" in corrected_kh
    assert "khách hàng" in corrected_kh


def test_accentless_vietnamese_query(processor: NLPRobustnessProcessor):
    ref_date = date(2026, 9, 19)
    result = processor.process_query("danh sach nhan vien theo phong ban", ref_date)

    assert result.intent_result.intent == Intent.LIST
    assert result.original_text == "danh sach nhan vien theo phong ban"
    assert result.accent_stripped_text == "danh sach nhan vien theo phong ban"
    assert result.accent_stripped_text == "danh sach nhan vien theo phong ban"


def test_corrected_text_is_the_canonical_input_for_scope_routing(processor: NLPRobustnessProcessor):
    result = processor.process_query('dt thang 8', date(2026, 9, 19))

    assert result.original_text == 'dt thang 8'
    assert result.accent_stripped_text == 'doanh thu thang 8'
    assert ScopeRouter().route(result).decision == ScopeDecision.IN_SCOPE


def test_relative_date_expressions(processor: NLPRobustnessProcessor):
    ref_date = date(2026, 9, 19)

    # 1. Hom nay (Today)
    res_today = processor.process_query("doanh thu hom nay", ref_date)
    assert res_today.intent_result.intent == Intent.REVENUE
    assert res_today.date_range is not None
    assert res_today.date_range.start == date(2026, 9, 19)
    assert res_today.date_range.end == date(2026, 9, 20)

    # 2. Hom qua (Yesterday)
    res_yesterday = processor.process_query("tong so don hang hom qua", ref_date)
    assert res_yesterday.date_range is not None
    assert res_yesterday.date_range.start == date(2026, 9, 18)
    assert res_yesterday.date_range.end == date(2026, 9, 19)

    # 3. Thang nay (This month)
    res_month = processor.process_query("doanh so thang nay", ref_date)
    assert res_month.date_range is not None
    assert res_month.date_range.start == date(2026, 9, 1)
    assert res_month.date_range.end == date(2026, 10, 1)

    # 4. Thang truoc (Last month)
    res_prev_month = processor.process_query("doanh thu thang truoc", ref_date)
    assert res_prev_month.date_range is not None
    assert res_prev_month.date_range.start == date(2026, 8, 1)
    assert res_prev_month.date_range.end == date(2026, 9, 1)


def test_bare_year_range_is_parsed_as_inclusive_calendar_years(processor: NLPRobustnessProcessor):
    result = processor.process_query('so nhan vien theo nam tu 2021 den 2025', date(2026, 9, 19))

    assert result.date_range.start == date(2021, 1, 1)
    assert result.date_range.end == date(2026, 1, 1)


def test_fuzzy_matching(processor: NLPRobustnessProcessor):
    candidates = ["employees", "customers", "orders", "departments"]
    matched = processor.fuzzy_match("employes", candidates, max_distance=2)
    assert matched == "employees"

    matched_cust = processor.fuzzy_match("customrs", candidates, max_distance=2)
    assert matched_cust == "customers"

    matched_none = processor.fuzzy_match("xyz_unknown", candidates, max_distance=1)
    assert matched_none is None

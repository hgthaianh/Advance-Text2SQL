import pytest
from src.nlp.intent_classifier import RuleBasedIntentClassifier
from src.shared.enums import Intent
from src.shared.models import IntentResult


@pytest.fixture
def classifier():
    return RuleBasedIntentClassifier()


@pytest.mark.parametrize("query, expected_intent", [
    ("Có bao nhiêu khách hàng?", Intent.COUNT),
    ("Tổng doanh thu tháng 8 là bao nhiêu?", Intent.REVENUE),
    ("Giá trung bình của sản phẩm là bao nhiêu?", Intent.AVERAGE),
    ("Top 5 sản phẩm bán chạy nhất?", Intent.TOP_N),
    ("Cho tôi thông tin khách hàng Nguyễn Văn A.", Intent.DETAIL),
    ("Danh sách khách hàng VIP.", Intent.LIST),
    ("So sánh doanh thu giữa các chi nhánh.", Intent.COMPARISON),
    ("Doanh thu thay đổi như thế nào trong 6 tháng qua?", Intent.TREND),
])
def test_required_intent_cases(classifier, query, expected_intent):
    result = classifier.classify(query)
    assert isinstance(result, IntentResult)
    assert result.intent == expected_intent
    assert result.confidence > 0.8
    assert result.reason is not None


@pytest.mark.parametrize("query, expected_intent", [
    ("CO BAO NHIEU KHACH HANG?", Intent.COUNT),
    ("tong doanh thu thang 8 la bao nhieu?", Intent.REVENUE),
    ("gia trung binh cua san pham la bao nhieu?", Intent.AVERAGE),
    ("TOP 5 SAN PHAM BAN CHAY NHAT?", Intent.TOP_N),
    ("cho toi thong tin khach hang nguyen van a", Intent.DETAIL),
    ("danh sach khach hang vip", Intent.LIST),
    ("so ssnah doanh thu giua cac chi nhanh", Intent.COMPARISON),
    ("doanh thu thay doi the nao trong 6 thang qua", Intent.TREND),
])
def test_vietnamese_casing_and_accent_variations(classifier, query, expected_intent):
    result = classifier.classify(query)
    assert result.intent == expected_intent
    assert result.confidence > 0.8


@pytest.mark.parametrize("empty_query", [
    "",
    "   ",
    "\t\n",
])
def test_empty_or_whitespace_query(classifier, empty_query):
    result = classifier.classify(empty_query)
    assert result.intent == Intent.UNKNOWN
    assert result.confidence == 0.0
    assert "empty" in result.reason.lower()


def test_unknown_query(classifier):
    result = classifier.classify("xyz 12345 abc")
    assert result.intent == Intent.UNKNOWN
    assert result.confidence == 0.5
    assert result.reason is not None


def test_sum_intent(classifier):
    result = classifier.classify("Tính tổng số tiền thanh toán")
    assert result.intent == Intent.SUM
    assert result.confidence > 0.8


def test_classifier_exception_safety(monkeypatch, classifier):
    # Simulate an error inside normalizer to verify fallback safety
    def mock_normalize(text):
        raise RuntimeError("Simulated normalization error")

    monkeypatch.setattr(classifier.normalizer, "normalize", mock_normalize)
    result = classifier.classify("Doanh thu tháng 8")
    assert result.intent == Intent.UNKNOWN
    assert result.confidence == 0.0
    assert result.reason == "Intent classification failed."
    assert "Simulated normalization error" not in result.reason

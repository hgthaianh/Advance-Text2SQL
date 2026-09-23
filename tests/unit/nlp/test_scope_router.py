import pytest

from src.nlp.scope_router import ScopeRouter
from src.shared.enums import ScopeDecision


@pytest.fixture(scope='module')
def router():
    return ScopeRouter()


@pytest.mark.parametrize('question', [
    'Tổng doanh thu tháng 8', 'Có bao nhiêu nhân viên phòng HR?',
    'top 5 sản phẩm bán chạy', 'liệt kê khách hàng VIP tại chi nhánh HCM',
    'tai khoan cua user U001', 'luong nhan vien',
])
def test_clear_database_requests_are_in_scope(router, question):
    assert router.route(question).decision == ScopeDecision.IN_SCOPE


@pytest.mark.parametrize('question', [
    'co bao nhieu yeu cau mua hang', 'co bao nhieu giao dich kho',
    'co bao nhieu ticket ho tro', 'co bao nhieu thuong hieu',
    'co bao nhieu don nghi phep', 'co bao nhieu don mua hang',
    'co bao nhieu lo hang', 'co bao nhieu dieu chuyen kho',
    'co bao nhieu team', 'co bao nhieu contact', 'co bao nhieu quotation',
])
def test_cross_domain_business_counts_are_in_scope(router, question):
    assert router.route(question).decision == ScopeDecision.IN_SCOPE


@pytest.mark.parametrize('question', [
    'Thời tiết HCM hôm nay thế nào?', 'Tài khoản ngân hàng là gì?',
    'Giải thích vai trò của manager', 'dịch status pending sang tiếng Việt',
    'hướng dẫn cách làm báo cáo doanh thu', 'tin tức về sản phẩm công nghệ',
])
def test_general_questions_with_schema_vocabulary_are_out_of_scope(router, question):
    result = router.route(question)
    assert result.decision == ScopeDecision.OUT_OF_SCOPE
    assert result.reasons == ['NON_DATA_REQUEST']


@pytest.mark.parametrize('question', ['mã', 'điểm', 'quản lý', 'status pending', 'tài khoản'])
def test_short_or_insufficient_questions_are_ambiguous(router, question):
    assert router.route(question).decision == ScopeDecision.AMBIGUOUS


@pytest.mark.parametrize('question', ['nhân viên', 'khách hàng', 'products', 'đơn hàng'])
def test_unambiguous_record_subjects_preserve_lookup_shorthand(router, question):
    result = router.route(question)
    assert result.decision == ScopeDecision.IN_SCOPE
    assert result.reasons == ['DIRECT_RECORD_SUBJECT']


def test_accentless_input_and_routing_diagnostics_are_deterministic(router):
    result = router.route('co bao nhieu nhan vien phong hr')
    assert result.decision == ScopeDecision.IN_SCOPE
    assert result.reasons == ['QUERY_ACTION_AND_ENTITY']
    assert 'ACTION:bao nhieu' in result.matched_signals
    assert 'ENTITY:nhan vien' in result.matched_signals

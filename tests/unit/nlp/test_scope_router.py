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


@pytest.mark.parametrize('question', [
    'Phòng HR đang có bao nhiêu người?',
    'Headcount phòng nhân sự là bao nhiêu?',
    'Số người phòng HR',
    'hr co bao nhieu nguoi',
    'Có bn nhân viên phòng hr?',
    'phog HR có bao nhiêu ngừoi?',
])
def test_hr_headcount_paraphrases_are_in_scope_after_semantic_normalization(question):
    from datetime import date
    from src.nlp.robustness import NLPRobustnessProcessor

    processed = NLPRobustnessProcessor().process_query(question, date(2026, 9, 29))
    result = ScopeRouter().route(processed)

    assert result.decision == ScopeDecision.IN_SCOPE
    assert 'hr' in result.candidate_domains
    assert {'employee', 'department'} <= set(result.candidate_entities)


@pytest.mark.parametrize('question, domain, entity', [
    ('Có bao nhiêu ticket OPEN?', 'support', 'ticket'),
    ('Liệt kê vendor bill OVERDUE.', 'finance', 'vendor_bill'),
    ('Có bao nhiêu serial đang được quản lý?', 'inventory', 'stock_serial'),
    ('Tổng chi phí theo cost center.', 'finance', 'cost_center'),
])
def test_business_subject_without_legacy_entity_word_is_in_scope(router, question, domain, entity):
    result = router.route(question)

    assert result.decision == ScopeDecision.IN_SCOPE
    assert domain in result.candidate_domains
    assert entity in result.candidate_entities

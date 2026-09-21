import json
from pathlib import Path

import pytest
import yaml

from evaluation.evaluate_schema_linking import evaluate
from scripts.build_schema_spec import extract_spec
from src.schema.linker import SchemaLinker, alias_coverage, identifier_aliases, validate_aliases
from src.schema.registry import ROOT, SchemaRegistry, load_yaml
from src.schema.relationship_graph import RelationshipGraph


@pytest.fixture(scope='module')
def registry():
    return SchemaRegistry()


@pytest.fixture(scope='module')
def aliases():
    return load_yaml(ROOT / 'schema/aliases.yaml')


def test_full_coverage(registry, aliases):
    report = alias_coverage(registry, aliases)
    assert report['tables_indexed'] == report['tables_explicit'] == 100
    assert report['columns_indexed'] == 855
    assert report['table_coverage_percent'] == 100
    assert report['columns_explicit'] + report['columns_automatic_only'] == 855
    assert report['columns_explicit'] >= 600
    assert report['unknown_references'] == 0
    assert {name.split('.')[0] for name in registry.tables} == {'org', 'hr', 'iam', 'customer', 'product', 'inventory', 'procurement', 'sales', 'finance', 'support', 'audit'}


def test_spec_matches_bundled_ddl_and_manifest(registry):
    import csv
    spec = load_yaml(ROOT / 'schema/schema_spec.yaml')
    ddl = (ROOT / 'dataset_v2/sql/01_schema.sql').read_text(encoding='utf-8')
    assert spec == extract_spec(ddl)
    with (ROOT / 'dataset_v2/docs/table_manifest.csv').open(encoding='utf-8-sig') as stream:
        names = {f"{r['schema']}.{r['table']}" for r in csv.DictReader(stream)}
    assert names == set(registry.tables)
    assert len(registry.relationships) > 100
    salary = registry.columns['hr.employees.base_salary']
    assert salary.data_type == 'numeric(18,2)'
    assert salary.nullable is False


@pytest.mark.parametrize('data, message', [
    ({'tables': {'fake': {'aliases': ['test']}}}, 'table'),
    ({'columns': {'customers.fake': {'aliases': ['test']}}}, 'column'),
    ({'columns': {'abc.fake_column': {'aliases': ['test']}}}, 'table'),
    ({'tables': {'customers': {'aliases': []}}}, 'Nonempty'),
    ({'tables': {'customers': {'aliases': ['']}}}, 'Nonempty'),
    ({'tables': {'customers': {'aliases': ['!!!']}}}, 'Nonempty'),
    ({'tables': {'customers': {'aliases': ['khách', 'khach']}}}, 'Duplicate'),
    ({'tables': {'customers': {'aliases': ['customer', 'Customer']}}}, 'Duplicate'),
    ({'tables': {'customers': {'aliases': 'customer'}}}, 'Nonempty'),
    ({'tables': {'customers': {'aliases': [1]}}}, 'Nonempty'),
    ({'tables': []}, 'mapping'),
    ({'wrong': {}}, 'Unknown'),
    ({'tables': {'customers': {'alias': ['test']}}}, 'Invalid'),
    ({'concepts': {'test': {'aliases': ['test']}}}, 'no schema targets'),
    ({'concepts': {'test': {'aliases': ['test'], 'tables': ['fake']}}}, 'table'),
    ({'concepts': {'test': {'aliases': ['test'], 'columns': ['customers.fake']}}}, 'column'),
    ({'concepts': {'test': {'aliases': ['test'], 'tables': 'customers'}}}, 'list'),
    ({'concepts': {'test': {'aliases': ['test'], 'tables': ['customers'], 'ambiguous': 'yes'}}}, 'boolean'),
    ({'concepts': {'test': {'aliases': ['test'], 'tables': ['customers'], 'join_hints': ['customers.id']}}}, 'column pair'),
    ({'concepts': {'test': {'aliases': ['test'], 'tables': ['customers'], 'join_hints': [['customers.id', 'employees.id']]}}}, 'not a relationship'),
    ({'tables': {'customers': {'aliases': ['one']}, 'customer.customers': {'aliases': ['two']}}}, 'Duplicate canonical'),
])
def test_invalid_aliases(registry, data, message):
    with pytest.raises(ValueError, match=message):
        validate_aliases(data, registry)


def test_shared_aliases_are_ambiguity_not_invalid(registry):
    data = {'columns': {'customers.name': {'aliases': ['name']}, 'products.name': {'aliases': ['name']}}}
    assert len(validate_aliases(data, registry)['columns']) == 2


def test_ambiguous_concept_is_valid(registry):
    data = {'concepts': {'code': {
        'aliases': ['mã'], 'tables': ['customers'], 'columns': ['customers.code'], 'ambiguous': True,
    }}}
    assert validate_aliases(data, registry)['concepts']['code']['ambiguous'] is True


def test_concept_join_hints_are_canonical_and_validated(registry):
    data = {'concepts': {'customer_orders': {
        'aliases': ['đơn của khách'], 'tables': ['customers', 'orders'],
        'join_hints': [['orders.customer_id', 'customers.id']],
    }}}
    assert validate_aliases(data, registry)['concepts']['customer_orders']['join_hints'] == [
        ('sales.orders.customer_id', 'customer.customers.id')
    ]


def test_duplicate_yaml_keys_rejected(tmp_path):
    path = tmp_path / 'aliases.yaml'
    path.write_text('tables:\n  customers: {aliases: [a]}\n  customers: {aliases: [b]}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='Duplicate YAML key'):
        load_yaml(path)


@pytest.mark.parametrize('value', ['[]', 'null', 'hello'])
def test_yaml_root_validation(tmp_path, value):
    path = tmp_path / 'bad.yaml'
    path.write_text(value, encoding='utf-8')
    with pytest.raises(ValueError, match='mapping'):
        load_yaml(path)


@pytest.mark.parametrize('identifier, expected', [
    ('customer_orders', {'customer orders', 'customer order'}),
    ('total_amount', {'total amount'}),
    ('employee_department_id', {'employee department id', 'employee department', 'department'}),
    ('categories', {'categories', 'category'}),
    ('branches', {'branches', 'branch'}),
    ('status', {'status'}),
    ('', set()),
])
def test_identifier_aliases(identifier, expected):
    assert set(identifier_aliases(identifier)) == expected


def test_registry_resolution(registry):
    assert registry.resolve_table('employees') == 'hr.employees'
    assert registry.resolve_column('employees.base_salary') == 'hr.employees.base_salary'
    assert registry.get_table('missing') is None
    with pytest.raises(ValueError):
        registry.resolve_column('name')
    with pytest.raises(ValueError):
        registry.resolve_column('employees.salary')


def test_graph_and_reverse_path(registry):
    graph = RelationshipGraph(registry)
    assert graph.find_path('orders', 'customers') == [('sales.orders', 'customer.customers')]
    assert graph.find_path('customers', 'orders') == [('customer.customers', 'sales.orders')]
    # Both tenant and membership bridges are shortest: do not silently choose.
    from src.shared.exceptions import AmbiguousJoinPathError
    with pytest.raises(AmbiguousJoinPathError):
        graph.find_path('customer_groups', 'customers')
    assert graph.find_path('orders', 'orders') == []


def test_duplicate_short_names_require_qualification(tmp_path):
    spec = {'tables': [dict(name='items', schema_name=s, columns=[dict(name='id', data_type='int')]) for s in ('one', 'two')]}
    path = tmp_path / 'schema.yaml'
    path.write_text(yaml.safe_dump(spec), encoding='utf-8')
    registry = SchemaRegistry(path)
    assert registry.resolve_table('one.items') == 'one.items'
    with pytest.raises(ValueError, match='ambiguous'):
        registry.resolve_table('items')
    assert RelationshipGraph(registry).find_path('one.items', 'two.items') == []


@pytest.mark.parametrize('mutation', ['duplicate_table', 'duplicate_column', 'bad_relationship', 'empty_tables', 'empty_columns'])
def test_invalid_schema(tmp_path, mutation):
    table = dict(name='x', columns=[dict(name='id', data_type='int')])
    spec = {'tables': [table]}
    if mutation == 'duplicate_table': spec['tables'].append(table)
    if mutation == 'duplicate_column': table['columns'].append(table['columns'][0])
    if mutation == 'bad_relationship': spec['relationships'] = [dict(source='public.x.id', target='public.x.missing')]
    if mutation == 'empty_tables': spec['tables'] = []
    if mutation == 'empty_columns': table['columns'] = []
    path = tmp_path / 'schema.yaml'
    path.write_text(yaml.safe_dump(spec), encoding='utf-8')
    with pytest.raises(ValueError):
        SchemaRegistry(path)


def test_evaluation_dataset_and_metrics(registry):
    cases = json.loads((ROOT / 'evaluation/schema_linking_cases.json').read_text(encoding='utf-8'))
    assert 150 <= len(cases) <= 300
    assert {t for c in cases for t in c['expected_tables']} == set(registry.tables)
    assert len({c['domain'] for c in cases}) == 11
    report = evaluate(SchemaLinker(registry), cases)
    assert report['metrics']['table_recall_at_5'] >= .95
    assert report['metrics']['column_recall_at_10'] >= .90
    assert set(report['metrics_by_kind']) == {'coverage', 'challenge'}


def test_metrics_use_actual_predictions(registry):
    linker = SchemaLinker(registry)
    cases = [dict(id='miss', question='zzzxqqq', domain='sales', expected_tables=['sales.orders'], expected_columns=['sales.orders.total_amount'])]
    report = evaluate(linker, cases)
    retrieval = {k: v for k, v in report['metrics'].items() if 'recall' in k or 'precision' in k or k.startswith('top_1')}
    assert all(v == 0 for v in retrieval.values())
    assert report['metrics']['false_rejection_rate'] == 1
    assert len(report['failures_at_10']) == 1
    with pytest.raises(ValueError, match='Duplicate case'):
        evaluate(linker, cases * 2)


@pytest.mark.parametrize('question, expected_column', [
    ('Choo xem số ngày nghỉ còn lại trong số dư nghỉ phép.', 'hr.leave_balances.remaining_days'),
    ('Cho xem ngày học xong ghi trong kết quả đào tạo.', 'hr.training_records.completion_date'),
    ('Các khách mua hàng đang hoạt động trong lĩnh vực nào?', 'customer.customers.industry'),
    ('Mặt hàng chạm mức tồn nào thì phải đặt mua lại?', 'product.products.reorder_level'),
    ('Nhiệt độ nhỏ nhất và lớn nhất được phép ở từng khu vực kho là bao nhiêu?', 'inventory.warehouse_zones.temperature_max'),
    ('Mỗi lần xuất nhập kho có bao nhiêu đơn vị hàng?', 'inventory.inventory_transactions.quantity'),
    ('Bộ phận yêu cầu mua hàng cần nhận hàng vào ngày nào?', 'procurement.purchase_requisitions.required_date'),
    ('Khi nào hết thời gian nhận chào giá mua?', 'procurement.rfqs.closing_date'),
    ('Cho xem hạn đóng của các yêu cầu báo giá mua.', 'procurement.rfqs.closing_date'),
    ('Cho xem lượng hàng thực nhận trên chi tiết đơn mua hàng.', 'procurement.purchase_order_items.received_qty'),
    ('Mỗi nhân viên bán hàng được giao doanh số mục tiêu bao nhiêu?', 'sales.sales_targets.revenue_target'),
    ('Người mua đã trả tiền bằng hình thức nào?', 'sales.payments.payment_method'),
    ('Lấy phương thức dùng để thanh toán khách hàng.', 'sales.payments.payment_method'),
    ('Các tài khoản kế toán thuộc những loại nào?', 'finance.chart_of_accounts.account_type'),
    ('Yêu cầu hỗ trợ được xử lý xong lúc nào?', 'support.support_tickets.resolved_at'),
    ('Các sự kiện SLA chậm cam kết bao nhiêu phút?', 'support.ticket_sla_events.breach_minutes'),
    ('Theo chính sách dịch vụ thì phải trả lời lần đầu trong bao lâu?', 'support.sla_policies.first_response_minutes'),
    ('Phòng ban nào trực thuộc phòng ban nào?', 'org.departments.parent_department_id'),
])
def test_critical_business_column_aliases(registry, question, expected_column):
    linker = SchemaLinker(registry)
    result = linker.link(question, column_top_k=10)
    candidate_cols = [col.name for col in result.columns]
    assert expected_column in candidate_cols


@pytest.mark.parametrize('bare_query, contextual_query, expected_column', [
    ('Cho xem đơn giá.', 'Cho xem đơn giá của chi tiết đơn hàng.', 'sales.order_items.unit_price'),
    ('Số lượng là bao nhiêu?', 'Số lượng tồn kho là bao nhiêu?', 'inventory.inventory_balances.qty_on_hand'),
])
def test_contextual_phrase_disambiguation(registry, bare_query, contextual_query, expected_column):
    linker = SchemaLinker(registry)
    bare_res = linker.link(bare_query)
    assert len(bare_res.ambiguous_terms) > 0

    ctx_res = linker.link(contextual_query)
    candidate_cols = [col.name for col in ctx_res.columns]
    assert expected_column in candidate_cols


@pytest.mark.parametrize('idiom_query', [
    'Kể một câu chuyện về kho báu dưới biển.',
    'Từ khách sáo dùng trong giao tiếp nghĩa là gì?',
    'Tôi mắc lỗi chính tả trong bài văn, chỉ cách tự kiểm tra.',
    'Giải thích thành ngữ lương tâm thanh thản.',
    'Chi nhánh cây trong bức tranh này bị gãy, sửa câu cho tự nhiên.',
])
def test_no_match_idiom_rejection(registry, idiom_query):
    linker = SchemaLinker(registry)
    res = linker.link(idiom_query)
    assert len(res.tables) == 0
    assert len(res.columns) == 0



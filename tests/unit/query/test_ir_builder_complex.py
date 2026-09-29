from datetime import date

from src.nlp.entity_extractor import DeterministicEntityExtractor
from src.nlp.robustness import NLPRobustnessProcessor
from src.query.handlers.basic import BasicQueryHandler
from src.query.ir_builder import QueryIRBuilder
from src.schema.linker import SchemaLinker
from src.schema.registry import SchemaRegistry
from src.shared.enums import Aggregation, FilterOperator, SortDirection
from src.shared.models import UserContext


def _build(question: str):
    registry = SchemaRegistry()
    preprocessed = NLPRobustnessProcessor().process_query(question, date(2026, 9, 20))
    query = QueryIRBuilder(registry).build(
        preprocessed, SchemaLinker(registry).link(preprocessed), DeterministicEntityExtractor().extract(question),
    )
    return registry, query


def test_group_having_and_direct_join_are_constructed_from_explicit_semantics():
    registry, query = _build('doanh thu theo chi nhanh tren 500')

    assert query.group_by == ['org.branches.name']
    assert query.having[0].column == 'sales.orders.total_amount'
    assert query.having[0].aggregation == Aggregation.SUM
    assert query.having[0].operator == FilterOperator.GT
    compiled = BasicQueryHandler(registry).handle(query)
    assert len(compiled.params) == 1
    assert 'HAVING sum(' in compiled.sql
    assert compiled.sql.count(' JOIN ') == 1


def test_at_least_threshold_builds_having_without_top_k_limit():
    registry, query = _build('doanh thu theo chi nhanh it nhat 300')

    assert query.top_k is None
    assert query.having[0].operator == FilterOperator.GTE
    compiled = BasicQueryHandler(registry).handle(query)
    assert 'HAVING sum(' in compiled.sql
    assert 'LIMIT' not in compiled.sql


def test_multiple_catalog_code_filters_add_their_fk_owner_tables():
    registry, query = _build('doanh thu khach hang ma khach hang C1 tai ma chi nhanh B1')

    filters = {(item.column, item.value) for item in query.filters}
    assert ('customer.customers.code', 'C1') in filters
    assert ('org.branches.code', 'B1') in filters
    compiled = BasicQueryHandler(registry).handle(query)
    assert compiled.sql.count(' JOIN ') >= 2


def test_top_k_uses_related_transactional_column_and_limits_after_grouping():
    registry, query = _build('top 5 san pham ban chay ma san pham P01')

    assert query.top_k == 5
    assert query.group_by == ['product.products.name']
    assert query.select[1].column == 'sales.order_items.quantity'
    compiled = BasicQueryHandler(registry).handle(query)
    assert compiled.sql.index('GROUP BY') < compiled.sql.index('ORDER BY') < compiled.sql.index('LIMIT')
    assert compiled.params['limit'] == 5


def test_top_k_dimension_wins_over_later_filter_mentions():
    _, query = _build('top 5 san pham ban chay cua khach hang vip tai chi nhanh hcm thang truoc')

    assert query.group_by == ['product.products.name']


def test_top_k_roots_joins_at_the_aggregate_fact_table():
    _, query = _build('top 5 san pham ban chay cua khach hang vip tai chi nhanh hcm thang truoc')

    assert query.tables[0] == 'sales.order_items'
    assert BasicQueryHandler(SchemaRegistry()).handle(query).sql


def test_lowest_employee_salary_ranks_employees_ascending():
    registry, query = _build('chuyen vien 1 luong thap nhat la bao nhieu')

    assert query.top_k == 1
    assert query.group_by == ['hr.employees.full_name']
    assert query.select[1].column == 'hr.employees.base_salary'
    assert query.order_by[0].direction == SortDirection.ASC
    compiled = BasicQueryHandler(registry).handle(query)
    assert 'ORDER BY sum("hr"."employees"."base_salary") ASC' in compiled.sql


def test_self_department_uses_trusted_employee_identity():
    registry = SchemaRegistry()
    preprocessed = NLPRobustnessProcessor().process_query('toi thuoc phong ban nao', date(2026, 9, 20))
    query = QueryIRBuilder(registry).build(
        preprocessed,
        SchemaLinker(registry).link(preprocessed),
        [],
        UserContext(user_id='161', role='admin', tenant_id='1', employee_id='161'),
    )

    assert query.select == ['org.departments.name']
    assert [(item.column, item.value) for item in query.filters] == [('hr.employees.id', 161)]
    assert query.join_hints == [('hr.employees.department_id', 'org.departments.id')]


def test_role_specific_catalog_filter_selects_the_corresponding_fk():
    registry, query = _build('nhan vien ma phong ban D1')

    assert ('hr.employees.department_id', 'org.departments.id') in query.join_hints
    compiled = BasicQueryHandler(registry).handle(query)
    assert '"hr"."employees"."department_id" = "org"."departments"."id"' in compiled.sql


def test_date_range_can_use_direct_transactional_neighbor_for_top_k():
    registry, query = _build('top 2 san pham ban chay thang 8 nam 2026')

    assert 'sales.orders' in query.tables
    assert {filter_.column for filter_ in query.filters} == {'sales.orders.order_date'}
    compiled = BasicQueryHandler(registry).handle(query)
    assert 'orders"."order_date' in compiled.sql


def test_recent_years_adds_date_filters_to_revenue():
    registry, query = _build('cho toi tong tien doanh thu 2 nam gan day')

    assert [(filter_.column, filter_.value) for filter_ in query.filters] == [
        ('sales.orders.order_date', date(2024, 9, 20)),
        ('sales.orders.order_date', date(2026, 9, 21)),
    ]
    compiled = BasicQueryHandler(registry).handle(query)
    assert 'orders"."order_date' in compiled.sql


def test_status_filter_uses_explicit_business_context():
    _, query = _build('don hang da hoan thanh')

    assert [(filter_.column, filter_.value) for filter_ in query.filters] == [
        ('sales.orders.status', 'COMPLETED')
    ]


def test_business_codes_status_tier_and_department_values_survive_ir_construction():
    _, employee = _build('Cho toi thong tin nhan vien EMP000120')
    _, order = _build('Cho toi thong tin don hang SO000123')
    _, partial = _build('Co bao nhieu PO trang thai PARTIAL')
    _, gold = _build('Co bao nhieu khach hang GOLD')
    _, department = _build('Co bao nhieu nhan vien phong nhan su')

    assert ('hr.employees.employee_code', 'EMP000120') in {
        (item.column, item.value) for item in employee.filters
    }
    assert ('sales.orders.order_no', 'SO000123') in {
        (item.column, item.value) for item in order.filters
    }
    assert ('procurement.purchase_orders.status', 'PARTIAL') in {
        (item.column, item.value) for item in partial.filters
    }
    assert ('customer.customers.customer_tier', 'GOLD') in {
        (item.column, item.value) for item in gold.filters
    }
    assert any(item.column == 'org.departments.name' and item.operator.value == 'like'
               for item in department.filters)


def test_compositional_aggregate_ranking_grouping_and_time_are_preserved():
    _, customers = _build('Top 5 khach hang co doanh so cao nhat')
    _, inventory = _build('Tong ton kho kha dung theo san pham')
    _, cancelled = _build('So don hang CANCELLED theo chi nhanh')
    _, monthly = _build('Doanh thu tung thang tu thang 1 den thang 9/2026')

    assert customers.group_by == ['customer.customers.name']
    assert customers.select[1].column == 'sales.orders.total_amount'
    assert customers.select[1].aggregation.value == 'sum'
    assert customers.top_k == 5
    assert inventory.group_by == ['product.products.name']
    assert inventory.select[1].column == 'inventory.inventory_balances.qty_available'
    assert inventory.select[1].aggregation.value == 'sum'
    assert cancelled.group_by == ['org.branches.name']
    assert cancelled.select[1].aggregation.value == 'count'
    assert monthly.time_group.column == 'sales.orders.order_date'
    assert monthly.time_group.granularity == 'month'


def test_superlative_top_k_ignores_numbers_from_date_filters():
    _, query = _build('Nha cung cap co tong gia tri PO cao nhat trong thang 8/2026')

    assert query.top_k == 1


def test_named_status_comparison_becomes_grouped_count_with_in_filter():
    _, query = _build('So sanh so PO RECEIVED va PARTIAL')

    assert query.group_by == ['procurement.purchase_orders.status']
    assert query.select[1].aggregation == Aggregation.COUNT
    assert query.filters[0].operator == FilterOperator.IN
    assert query.filters[0].value == ['RECEIVED', 'PARTIAL']


def test_explicit_tenant_number_survives_as_an_ir_filter():
    _, query = _build('Co bao nhieu nhan vien cua tenant 2')

    assert ('org.tenants.id', 2) in {(item.column, item.value) for item in query.filters}

def test_group_by_uses_the_dimension_after_theo():
    registry, query = _build('doanh thu khach hang theo chi nhanh')

    assert query.group_by == ['org.branches.name']
    assert BasicQueryHandler(registry).handle(query).sql


def test_explicit_absence_language_builds_a_left_anti_join():
    registry, query = _build('liet ke khach hang chua co don hang')

    assert query.outer_joins == ['sales.orders']
    assert [(filter_.column, filter_.value) for filter_ in query.filters] == [
        ('sales.orders.id', None),
    ]
    compiled = BasicQueryHandler(registry).handle(query)
    assert 'LEFT OUTER JOIN "sales"."orders"' in compiled.sql
    assert '"sales"."orders"."id" IS NULL' in compiled.sql


def test_inclusive_absence_language_keeps_matching_rows_with_a_left_join():
    registry, query = _build('liet ke khach hang bao gom ca khach chua co don hang')

    assert query.outer_joins == ['sales.orders']
    assert not query.filters
    assert 'LEFT OUTER JOIN "sales"."orders"' in BasicQueryHandler(registry).handle(query).sql


def test_absence_language_supports_an_optional_fk_parent():
    registry, query = _build('liet ke don hang khong co khach hang')

    assert query.outer_joins == ['customer.customers']
    assert [(filter_.column, filter_.value) for filter_ in query.filters] == [
        ('customer.customers.id', None),
    ]
    assert 'LEFT OUTER JOIN "customer"."customers"' in BasicQueryHandler(registry).handle(query).sql

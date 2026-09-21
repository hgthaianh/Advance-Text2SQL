from datetime import date

from src.nlp.entity_extractor import DeterministicEntityExtractor
from src.nlp.robustness import NLPRobustnessProcessor
from src.query.handlers.basic import BasicQueryHandler
from src.query.ir_builder import QueryIRBuilder
from src.schema.linker import SchemaLinker
from src.schema.registry import SchemaRegistry
from src.shared.enums import Aggregation, FilterOperator


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


def test_status_filter_uses_explicit_business_context():
    _, query = _build('don hang da hoan thanh')

    assert [(filter_.column, filter_.value) for filter_ in query.filters] == [
        ('sales.orders.status', 'COMPLETED')
    ]


def test_group_by_uses_the_dimension_after_theo():
    registry, query = _build('doanh thu khach hang theo chi nhanh')

    assert query.group_by == ['org.branches.name']
    assert BasicQueryHandler(registry).handle(query).sql

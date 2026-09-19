from datetime import date

import pytest
from sqlalchemy import create_engine

from src.query.handlers.complex import ComplexQueryHandler
from src.shared.exceptions import (
    AmbiguousJoinPathError, InvalidColumnError, MalformedQueryIRError,
    JoinPathNotFoundError, MissingAggregationColumnError, UnsupportedAggregationError, UnsupportedOperatorError,
)
from tests.unit.query.conftest import make_registry


def grouped(**changes):
    query = dict(intent='SELECT', tables=['orders'],
                 select=['customer_id', dict(column='total_amount', aggregation='SUM')],
                 group_by=['customer_id'])
    query.update(changes)
    return query


@pytest.mark.parametrize('dimension,joins', [('orders.customer_id', 0), ('customers.name', 1), ('customer_groups.name', 2)])
@pytest.mark.parametrize('aggregation', ['SUM', 'COUNT', 'AVG'])
def test_grouped_aggregates_and_having(registry, dimension, joins, aggregation):
    compiled = ComplexQueryHandler(registry).handle(grouped(
        select=[dimension, dict(column='orders.total_amount', aggregation=aggregation)],
        group_by=[dimension],
        having=[dict(column='orders.total_amount', aggregation=aggregation, operator='>', value=50)],
    ))
    assert compiled.sql.count(' JOIN ') == joins
    assert f'HAVING {aggregation.lower()}(' in compiled.sql
    assert compiled.params == {'p0': 50}
    assert 'LIMIT' not in compiled.sql


@pytest.mark.parametrize('direction', ['ASC', 'DESC'])
@pytest.mark.parametrize('mode', ['top_k', 'TOP_N'])
def test_aggregate_top_five(registry, direction, mode):
    changes = dict(top_k=5) if mode == 'top_k' else dict(intent='TOP_N', limit=5)
    handler = ComplexQueryHandler(registry)
    query = grouped(order_by=[dict(column='total_amount', aggregation='SUM', direction=direction)], **changes)
    plan = handler.planner.plan(query)
    compiled = handler.builder.build(plan)
    assert f'ORDER BY sum("public"."orders"."total_amount") {direction}, "public"."orders"."customer_id" ASC' in compiled.sql
    assert compiled.params == {'limit': 5}
    assert handler.planner.plan(plan.query) == plan


def test_distinct_aggregate_expressions_on_same_column(registry):
    compiled = ComplexQueryHandler(registry).handle(grouped(
        select=['customer_id', dict(column='total_amount', aggregation='SUM'), dict(column='total_amount', aggregation='AVG')],
        order_by=[dict(column='total_amount', aggregation='AVG', direction='DESC')],
        having=[dict(column='*', aggregation='COUNT', operator='>=', value=2)],
    ))
    assert 'HAVING count(*) >= :p0' in compiled.sql
    assert 'ORDER BY avg(' in compiled.sql


def test_having_and_order_can_reference_unselected_aggregates(registry):
    compiled = ComplexQueryHandler(registry).handle(grouped(
        having=[dict(column='customers.id', aggregation='COUNT', operator='>', value=1)],
        order_by=[dict(column='*', aggregation='COUNT', direction='DESC')],
    ))
    assert 'JOIN "public"."customers"' in compiled.sql
    assert 'ORDER BY count(*) DESC' in compiled.sql


@pytest.mark.parametrize('changes,error', [
    (dict(group_by=['missing']), InvalidColumnError),
    (dict(having=[dict(column='missing', aggregation='SUM', value=1)]), InvalidColumnError),
    (dict(having=[dict(column='status', aggregation='SUM', value=1)]), MalformedQueryIRError),
    (dict(having=[dict(column='*', aggregation='AVG', value=1)]), MissingAggregationColumnError),
    (dict(having=[dict(column='id', aggregation='MEDIAN', value=1)]), UnsupportedAggregationError),
    (dict(having=[dict(column='id', aggregation='COUNT', operator='OR', value=1)]), UnsupportedOperatorError),
    (dict(having=[dict(column='id', aggregation='COUNT', operator='LIKE', value='1')]), MalformedQueryIRError),
    (dict(having=[dict(column='id', aggregation='COUNT', operator='IN', value=1)]), MalformedQueryIRError),
    (dict(having=[dict(column='id', aggregation='COUNT', value=[1])]), MalformedQueryIRError),
    (dict(having=[dict(column='id', aggregation='COUNT', operator='>', value=None)]), MalformedQueryIRError),
    (dict(having=[dict(column='id', value=1)]), MalformedQueryIRError),
    (dict(order_by=[dict(column='status')]), MalformedQueryIRError),
    (dict(order_by=[dict(column='total_amount', aggregation='SUM', direction='DESC; DROP TABLE orders')]), MalformedQueryIRError),
    (dict(order_by=[dict(column='total_amount', aggregation='MAX')]), UnsupportedAggregationError),
    (dict(order_by=[dict(column='status', aggregation='AVG')]), MalformedQueryIRError),
    (dict(top_k=5), MalformedQueryIRError),
    (dict(top_k=0), MalformedQueryIRError),
    (dict(top_k=-1), MalformedQueryIRError),
    (dict(top_k=1001), MalformedQueryIRError),
    (dict(top_k=True), MalformedQueryIRError),
    (dict(top_k='5'), MalformedQueryIRError),
    (dict(top_k=1.5), MalformedQueryIRError),
    (dict(limit=1001), MalformedQueryIRError),
    (dict(raw_sql='SELECT 1'), MalformedQueryIRError),
    (dict(cte=[]), MalformedQueryIRError),
    (dict(union=[]), MalformedQueryIRError),
])
def test_complex_validation(registry, changes, error):
    with pytest.raises(error):
        ComplexQueryHandler(registry).handle(grouped(**changes))


@pytest.mark.parametrize('field', ['having', 'order_by'])
def test_row_query_rejects_aggregate_clauses(registry, field):
    clause = dict(column='id', aggregation='COUNT')
    if field == 'having':
        clause['value'] = 1
    with pytest.raises(MalformedQueryIRError):
        ComplexQueryHandler(registry).handle(dict(intent='LOOKUP', tables=['orders'], select=['id'], **{field: [clause]}))


def test_ambiguous_aggregate_order_requires_explicit_function(registry):
    with pytest.raises(MalformedQueryIRError):
        ComplexQueryHandler(registry).handle(grouped(
            select=['customer_id', dict(column='total_amount', aggregation='SUM'), dict(column='total_amount', aggregation='AVG')],
            order_by=[dict(column='total_amount')],
        ))


@pytest.mark.parametrize('max_limit', [0, 1001, True, '5'])
def test_invalid_limit_configuration(registry, max_limit):
    with pytest.raises(MalformedQueryIRError):
        ComplexQueryHandler(registry, max_limit=max_limit)


def test_configured_limit_is_enforced(registry, monkeypatch):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), 'query_max_limit', 5)
    handler = ComplexQueryHandler(registry)
    with pytest.raises(MalformedQueryIRError):
        handler.handle(grouped(top_k=6, order_by=[dict(column='total_amount')]))
    with pytest.raises(MalformedQueryIRError):
        handler.handle(dict(intent='LOOKUP', tables=['orders'], select=['id'], limit=6))
    assert handler.handle(grouped(top_k=5, order_by=[dict(column='total_amount')])).params['limit'] == 5
    assert 'LIMIT' not in handler.handle(grouped()).sql


@pytest.mark.parametrize('clause', ['having', 'order_by'])
def test_unknown_or_malicious_aggregate_identifier(registry, clause):
    expression = dict(column='id); DROP TABLE orders; --', aggregation='COUNT')
    if clause == 'having':
        expression['value'] = 1
    with pytest.raises(InvalidColumnError):
        ComplexQueryHandler(registry).handle(grouped(**{clause: [expression]}))


def test_having_injection_remains_bound(registry):
    value = "0 OR 1=1; DROP TABLE orders; --"
    compiled = ComplexQueryHandler(registry).handle(grouped(
        filters=[dict(column='status', value=value)],
        having=[dict(column='*', aggregation='COUNT', operator='>', value=value)],
    ))
    assert value not in compiled.sql
    assert compiled.params == {'p0': value, 'p1': value}


@pytest.mark.parametrize('ambiguous', [True, False])
def test_complex_join_fails_safely(ambiguous):
    edges = [('a', 'b_id', 'b'), ('a', 'other_b_id', 'b')] if ambiguous else []
    registry = make_registry(['a', 'b'], edges)
    with pytest.raises(AmbiguousJoinPathError if ambiguous else JoinPathNotFoundError):
        ComplexQueryHandler(registry).handle(dict(
            intent='COUNT', tables=['a'], select=['b.name', dict(column='a.id', aggregation='COUNT')],
            group_by=['b.name'], having=[dict(column='a.id', aggregation='COUNT', operator='>', value=1)],
        ))


def test_grouped_top_k_filters_dates_and_permissions_execute(registry):
    handler = ComplexQueryHandler(registry)
    query = grouped(
        select=['customer_groups.name', dict(column='orders.total_amount', aggregation='SUM')],
        group_by=['customer_groups.name'], top_k=5,
        order_by=[dict(column='orders.total_amount', aggregation='SUM', direction='DESC')],
        filters=[
            dict(column='customers.customer_type', value='VIP', source='permission'),
            dict(column='orders.status', value='PAID'),
            dict(column='order_date', operator='>=', value=date(2026, 8, 1)),
            dict(column='order_date', operator='<', value=date(2026, 9, 1)),
        ],
        having=[dict(column='orders.total_amount', aggregation='SUM', operator='>', value=100),
                dict(column='*', aggregation='COUNT', operator='>=', value=150)],
    )
    plan = handler.planner.plan(query)
    assert plan.query.filters[0].source == 'permission'
    compiled = handler.builder.build(plan)
    assert compiled.sql.index('WHERE') < compiled.sql.index('GROUP BY') < compiled.sql.index('HAVING') < compiled.sql.index('ORDER BY') < compiled.sql.index('LIMIT')
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS public")
            conn.exec_driver_sql('CREATE TABLE public.orders (id INTEGER, customer_id INTEGER, total_amount NUMERIC, order_date DATE, status TEXT)')
            conn.exec_driver_sql('CREATE TABLE public.customers (id INTEGER, name TEXT, customer_type TEXT, group_id INTEGER)')
            conn.exec_driver_sql('CREATE TABLE public.customer_groups (id INTEGER, name TEXT)')
            for i in range(1, 8):
                conn.exec_driver_sql('INSERT INTO public.customer_groups VALUES (?, ?)', (i, f'G{i}'))
                conn.exec_driver_sql('INSERT INTO public.customers VALUES (?, ?, ?, ?)', (i, f'C{i}', 'VIP' if i < 7 else 'OTHER', i))
                conn.exec_driver_sql('INSERT INTO public.orders VALUES (?, ?, ?, ?, ?)', [(j, i, i, '2026-08-01', 'PAID') for j in range(150)])
            # Excluded by end boundary and status; neither can boost the total.
            conn.exec_driver_sql("INSERT INTO public.orders VALUES (1000, 1, 99999, '2026-09-01', 'PAID'), (1001, 1, 99999, '2026-08-15', 'NEW')")
            assert conn.execute(compiled.statement).all() == [('G6', 900), ('G5', 750), ('G4', 600), ('G3', 450), ('G2', 300)]
            query['filters'][0] = dict(column='customers.id', operator='IN', value=[], source='permission')
            assert conn.execute(handler.handle(query).statement).all() == []
    finally:
        engine.dispose()


def test_legacy_permission_adapter_checks_having_owner():
    from src.permission.models import PermissionContext
    from src.query.ir import QueryIR
    from src.query.planner import FKQueryPlanner
    from src.schema.relationship_graph import FKRelationshipGraph
    from src.shared.exceptions import PermissionDeniedError

    graph = FKRelationshipGraph()
    graph.add_relationship('orders', 'customer_id', 'customers', 'id')
    query = QueryIR(intent='COUNT', tables=['orders'], having=[
        dict(column='customers.id', aggregation='COUNT', operator='>', value=0),
    ])
    with pytest.raises(PermissionDeniedError, match='customers'):
        FKQueryPlanner(graph=graph).plan(query, PermissionContext(allowed_tables=['orders']))


def test_row_top_k_retains_permission_filter(registry):
    compiled = ComplexQueryHandler(registry).handle(dict(
        intent='TOP_N', tables=['orders'], select=['id'], limit=5,
        order_by=[dict(column='total_amount', direction='DESC'), dict(column='id')],
        filters=[dict(column='customer_id', value=12, source='permission')],
    ))
    assert compiled.params == {'p0': 12, 'limit': 5}
    assert 'WHERE "public"."orders"."customer_id" = :p0' in compiled.sql


def test_row_top_k_overrides_default_limit(registry):
    compiled = ComplexQueryHandler(registry).handle(dict(
        intent='LOOKUP', tables=['orders'], select=['id'], top_k=3,
        order_by=[dict(column='id', direction='ASC')],
    ))
    assert compiled.params == {'limit': 3}
    assert 'ORDER BY "public"."orders"."id" ASC' in compiled.sql


def test_scalar_aggregate_top_k_is_rejected(registry):
    with pytest.raises(MalformedQueryIRError):
        ComplexQueryHandler(registry).handle(dict(
            intent='SUM', tables=['orders'], select=['total_amount'],
            top_k=5, order_by=[dict(column='total_amount')],
        ))

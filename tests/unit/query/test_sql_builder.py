from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from src.query.ir import SelectItem
from src.shared.exceptions import InvalidColumnError, MalformedQueryIRError, UnsupportedAggregationError
from src.shared.models import SchemaRelationship


def build(planner, builder, **changes):
    ir = dict(intent='LOOKUP',tables=['orders'],select=['id'])
    ir.update(changes)
    return builder.build(planner.plan(ir))


def test_lookup_is_quoted_and_limited(planner,builder):
    result = build(planner,builder)
    assert 'SELECT "public"."orders"."id"' in result.sql
    assert 'FROM "public"."orders"' in result.sql
    assert 'LIMIT :limit' in result.sql
    assert result.params == {'limit':100}
    assert result.statement is not None


@pytest.mark.parametrize('operator,sql_operator', [('=','='),('!=','!='),('>','>'),('>=','>='),('<','<'),('<=','<='),('eq','='),('GTE','>=')])
def test_comparisons_are_bound(planner,builder,operator,sql_operator):
    result = build(planner,builder,intent='FILTER',filters=[dict(column='total_amount',operator=operator,value=12345)])
    assert f'"total_amount" {sql_operator} :p0' in result.sql
    assert result.params['p0'] == 12345
    assert '12345' not in result.sql


def test_like_is_bound(planner,builder):
    result = build(planner,builder,filters=[dict(column='status',operator='LIKE',value='PAID%')])
    assert 'LIKE :p0' in result.sql
    assert result.params['p0'] == 'PAID%'


def test_in_values_are_individually_bound(planner,builder):
    result = build(planner,builder,filters=[dict(column='id',operator='IN',value=[7,11,15])])
    assert 'IN (:p0, :p1, :p2)' in result.sql
    assert result.params == {'p0':7,'p1':11,'p2':15,'limit':100}


def test_empty_in_fails_closed(planner,builder):
    result = build(planner,builder,filters=[dict(column='id',operator='IN',value=[],source='permission')])
    assert 'WHERE false' in result.sql


@pytest.mark.parametrize('operator,expression',[('=','IS NULL'),('!=','IS NOT NULL')])
def test_null_semantics(planner,builder,operator,expression):
    result = build(planner,builder,filters=[dict(column='status',operator=operator,value=None)])
    assert expression in result.sql
    assert result.params == {'limit':100}


def test_multiple_filters_and_sources(planner,builder):
    result = build(planner,builder,filters=[dict(column='status',operator='=',value='PAID'),dict(column='customer_id',operator='=',value=12,source='permission')])
    assert ' AND ' in result.sql
    assert result.params == {'p0':'PAID','p1':12,'limit':100}


@pytest.mark.parametrize('value', ["'; DROP TABLE users; --", "x' OR 1=1 --", ':p0', 'VIP\\test', 'Khách hàng'])
def test_sql_injection_stays_in_parameters(planner,builder,value):
    result = build(planner,builder,filters=[dict(column='status',operator='=',value=value)])
    assert result.params['p0'] == value
    # :p0 itself is necessarily visible as a placeholder, not interpolated text.
    if value != ':p0': assert value not in result.sql
    assert 'WHERE "public"."orders"."status" = :p0' in result.sql


def test_identifier_injection_is_rejected(planner,builder):
    with pytest.raises(InvalidColumnError):
        build(planner,builder,select=['id); DROP TABLE orders; --'])


@pytest.mark.parametrize('intent,function', [('COUNT','count'),('SUM','sum'),('AVG','avg')])
def test_aggregations(planner,builder,intent,function):
    result = build(planner,builder,intent=intent,select=['total_amount'])
    assert f'{function}("public"."orders"."total_amount")' in result.sql


def test_direct_join_predicate_uses_fk(planner,builder):
    result = build(planner,builder,intent='SUM',tables=['orders','customers'],select=[dict(column='orders.total_amount',aggregation='SUM')],filters=[dict(column='customers.customer_type',operator='=',value='VIP')])
    assert 'JOIN "public"."customers" ON "public"."orders"."customer_id" = "public"."customers"."id"' in result.sql
    assert result.params['p0'] == 'VIP'


def test_multihop_joins_have_no_cartesian_product(planner,builder):
    result = build(planner,builder,select=['customer_groups.name'])
    assert result.sql.count(' JOIN ') == 2
    assert '"customers"."group_id" = "public"."customer_groups"."id"' in result.sql


def test_group_by_and_aggregate_order(planner,builder):
    result = build(planner,builder,intent='SUM',select=['customer_id','total_amount'],group_by=['customer_id'],order_by=[dict(column='total_amount',direction='desc')])
    assert 'GROUP BY "public"."orders"."customer_id"' in result.sql
    assert 'ORDER BY sum("public"."orders"."total_amount") DESC' in result.sql


def test_dates_and_decimal_are_bound(planner,builder):
    result = build(planner,builder,filters=[dict(column='order_date',operator='>=',value=date(2026,9,19)),dict(column='total_amount',operator='>=',value=Decimal('99.99'))])
    assert result.params['p0'] == date(2026,9,19)
    assert result.params['p1'] == Decimal('99.99')
    assert '2026' not in result.sql and '99.99' not in result.sql


def test_forged_join_is_rejected(planner,builder):
    plan = planner.plan(dict(intent='COUNT',tables=['orders','customers']))
    plan.joins = [SchemaRelationship(source='public.orders.id',target='public.customers.id')]
    with pytest.raises(MalformedQueryIRError): builder.build(plan)


def test_mutated_plan_projection_is_revalidated(planner,builder):
    plan = planner.plan(dict(intent='COUNT',tables=['orders']))
    plan.query.select = [SelectItem.model_construct(column='orders.id',aggregation='DROP TABLE')]
    with pytest.warns(UserWarning, match='Pydantic serializer warnings'):
        with pytest.raises(UnsupportedAggregationError):
            builder.build(plan)


def test_invalid_plan_and_forged_physical_tables(planner,builder):
    with pytest.raises(MalformedQueryIRError): builder.build({})
    plan = planner.plan(dict(intent='COUNT',tables=['orders']))
    plan.tables = ['public.customers']
    with pytest.raises(MalformedQueryIRError): builder.build(plan)


def test_real_relational_results_in_memory(planner,builder):
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS public")
            connection.exec_driver_sql('CREATE TABLE public.orders (id INTEGER, customer_id INTEGER, total_amount NUMERIC, order_date DATE, status TEXT)')
            connection.exec_driver_sql('CREATE TABLE public.customers (id INTEGER, name TEXT, customer_type TEXT, group_id INTEGER)')
            connection.exec_driver_sql('CREATE TABLE public.customer_groups (id INTEGER, name TEXT)')
            connection.exec_driver_sql("INSERT INTO public.customer_groups VALUES (1, 'Gold'), (2, 'Standard')")
            connection.exec_driver_sql("INSERT INTO public.customers VALUES (1,'A','VIP',1), (2,'B','NORMAL',2)")
            connection.exec_driver_sql("INSERT INTO public.orders VALUES (1,1,100,'2026-09-18','PAID'),(2,1,200,'2026-09-18','PAID'),(3,2,900,'2026-09-18','NEW')")
            for intent,expected in [('SUM',300),('AVG',150),('COUNT',2)]:
                result = build(planner,builder,intent=intent,select=['total_amount'],filters=[dict(column='customer_groups.name',operator='=',value='Gold',source='permission')])
                assert connection.execute(result.statement).scalar_one() == expected
            malicious = "'; DROP TABLE users; --"
            result = build(planner,builder,filters=[dict(column='status',operator='=',value=malicious)])
            assert connection.execute(result.statement).all() == []
            assert connection.exec_driver_sql('SELECT count(*) FROM public.orders').scalar_one() == 3
            result = build(planner,builder,filters=[dict(column='id',operator='IN',value=[])])
            assert connection.execute(result.statement).all() == []
    finally:
        engine.dispose()


@pytest.mark.parametrize('intent', ['COUNT', 'SUM', 'AVG', 'SELECT'])
def test_aggregate_never_emits_limit(planner, builder, intent):
    projection = [dict(column='total_amount', aggregation='SUM')] if intent == 'SELECT' else ['total_amount']
    compiled = build(planner, builder, intent=intent, select=projection, limit=1)
    assert 'LIMIT' not in compiled.sql.upper()
    assert 'limit' not in compiled.params


def test_aggregates_use_more_than_100_input_rows(planner, builder):
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS public")
            connection.exec_driver_sql('CREATE TABLE public.orders (id INTEGER, customer_id INTEGER, total_amount NUMERIC)')
            connection.exec_driver_sql('INSERT INTO public.orders VALUES (?, ?, ?)', [(i, i, i) for i in range(1, 151)])
            for intent, expected in [('COUNT', 150), ('SUM', 11325), ('AVG', 75.5)]:
                result = build(planner, builder, intent=intent, select=['total_amount'], limit=1)
                assert connection.execute(result.statement).scalar_one() == expected
            grouped = build(planner, builder, intent='SUM', select=['customer_id', 'total_amount'], group_by=['customer_id'])
            assert 'LIMIT' not in grouped.sql.upper()
            assert len(connection.execute(grouped.statement).all()) == 150
            for intent in ('LOOKUP', 'FILTER'):
                rows = build(planner, builder, intent=intent)
                assert len(connection.execute(rows.statement).all()) == 100
    finally:
        engine.dispose()


def test_count_customer_after_join_counts_duplicates_in_v1():
    from src.query.handlers.basic import BasicQueryHandler
    from tests.unit.query.conftest import make_registry

    registry = make_registry(['customers', 'orders', 'order_items'], [
        ('orders', 'customer_id', 'customers'), ('order_items', 'order_id', 'orders'),
    ])
    result = BasicQueryHandler(registry).handle(dict(
        intent='COUNT', tables=['customers', 'order_items'], select=['customers.id'],
    ))
    assert 'DISTINCT' not in result.sql.upper()
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS public")
            connection.exec_driver_sql('CREATE TABLE public.customers (id INTEGER)')
            connection.exec_driver_sql('CREATE TABLE public.orders (id INTEGER, customer_id INTEGER)')
            connection.exec_driver_sql('CREATE TABLE public.order_items (id INTEGER, order_id INTEGER)')
            connection.exec_driver_sql('INSERT INTO public.customers VALUES (1)')
            connection.exec_driver_sql('INSERT INTO public.orders VALUES (10, 1), (20, 1)')
            connection.exec_driver_sql('INSERT INTO public.order_items VALUES (100, 10), (101, 10), (102, 20)')
            assert connection.execute(result.statement).scalar_one() == 3
    finally:
        engine.dispose()

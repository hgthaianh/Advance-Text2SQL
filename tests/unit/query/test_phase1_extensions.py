import pytest
from sqlalchemy import create_engine

from src.query.handlers.basic import BasicQueryHandler
from src.query.ir import QueryIR, SelectItem
from src.query.models import QueryPlan
from src.query.planner import QueryPlanner
from src.query.sql_builder import SQLBuilder
from src.schema.registry import SchemaRegistry
from src.schema.relationship_graph import RelationshipGraph
from src.shared.enums import Aggregation, Intent
from src.shared.exceptions import AmbiguousJoinPathError, FanoutRiskError, MalformedQueryIRError
from src.shared.models import SchemaColumn, SchemaRelationship, SchemaTable
from tests.unit.query.conftest import make_registry


# ---------------------------------------------------------------------------
# 1. Aggregate over one-to-many joins (COUNT DISTINCT)
# ---------------------------------------------------------------------------

def test_count_parent_auto_promotes_distinct_on_child_fanout():
    """When a parent table is joined with a child table, COUNT(parent.id) auto-promotes to DISTINCT."""
    registry = make_registry(['customers', 'orders'], [('orders', 'customer_id', 'customers')])
    query = dict(intent='COUNT', tables=['customers', 'orders'], select=['customers.id'])
    plan = QueryPlanner(registry).plan(query)

    # Verify that the planner set distinct = True on the parent column projection
    assert isinstance(plan.query.select[0], SelectItem)
    assert plan.query.select[0].distinct is True

    compiled = SQLBuilder(registry).build(plan)
    assert 'COUNT(DISTINCT "PUBLIC"."CUSTOMERS"."ID")' in compiled.sql.upper()


def test_count_child_does_not_auto_promote_distinct():
    """Counting a child table column over a many-to-one join has no fan-out and does not auto-promote DISTINCT."""
    registry = make_registry(['customers', 'orders'], [('orders', 'customer_id', 'customers')])
    query = dict(intent='COUNT', tables=['orders', 'customers'], select=['orders.id'])
    plan = QueryPlanner(registry).plan(query)

    assert plan.query.select[0].distinct is False
    compiled = SQLBuilder(registry).build(plan)
    assert 'COUNT(DISTINCT' not in compiled.sql.upper()
    assert 'COUNT("PUBLIC"."ORDERS"."ID")' in compiled.sql.upper()


def test_count_star_preserves_row_semantics_on_fanout():
    """COUNT(*) deliberately counts joined rows and is never promoted to DISTINCT."""
    registry = make_registry(['customers', 'orders'], [('orders', 'customer_id', 'customers')])
    query = dict(intent='COUNT', tables=['customers', 'orders'], select=['*'])
    plan = QueryPlanner(registry).plan(query)

    compiled = SQLBuilder(registry).build(plan)
    assert 'COUNT(*)' in compiled.sql.upper()
    assert 'DISTINCT' not in compiled.sql.upper()


def test_explicit_distinct_on_select_item():
    """Explicitly setting distinct=True on SelectItem produces COUNT(DISTINCT ...)."""
    registry = make_registry(['orders'], [])
    plan = QueryPlanner(registry).plan(QueryIR(
        intent=Intent.COUNT,
        tables=['orders'],
        select=[SelectItem(column='orders.id', aggregation=Aggregation.COUNT, distinct=True)],
    ))
    compiled = SQLBuilder(registry).build(plan)
    assert 'count(DISTINCT "public"."orders"."id")' in compiled.sql


def test_sum_and_avg_preserve_fanout_rejection():
    """SUM and AVG must still fail closed with FanoutRiskError rather than converting to DISTINCT."""
    registry = make_registry(['customers', 'orders'], [('orders', 'customer_id', 'customers')])
    with pytest.raises(FanoutRiskError):
        QueryPlanner(registry).plan(dict(
            intent='SUM', tables=['customers', 'orders'], select=['customers.id'],
        ))


# ---------------------------------------------------------------------------
# 2. Composite Foreign Keys
# ---------------------------------------------------------------------------

def test_composite_foreign_key_compiles_compound_and_join():
    """A relationship with composite_keys compiles an AND-joined ON condition."""
    tables = [
        SchemaTable(name='orders', schema_name='public', columns=[
            SchemaColumn(name='tenant_id', data_type='int', primary_key=True),
            SchemaColumn(name='order_id', data_type='int', primary_key=True),
        ]),
        SchemaTable(name='order_items', schema_name='public', columns=[
            SchemaColumn(name='tenant_id', data_type='int'),
            SchemaColumn(name='order_id', data_type='int'),
            SchemaColumn(name='item_id', data_type='int', primary_key=True),
        ]),
    ]
    relationships = [
        SchemaRelationship(
            source='public.order_items.order_id',
            target='public.orders.order_id',
            composite_keys=[
                ('public.order_items.tenant_id', 'public.orders.tenant_id'),
                ('public.order_items.order_id', 'public.orders.order_id'),
            ],
        ),
    ]
    registry = SchemaRegistry(tables=tables, relationships=relationships)
    plan = QueryPlanner(registry).plan(dict(
        intent='LOOKUP', tables=['orders', 'order_items'], select=['order_items.item_id'],
    ))
    compiled = SQLBuilder(registry).build(plan)

    # Verify both join predicates appear combined with AND
    assert 'JOIN "public"."order_items"' in compiled.sql
    assert '"public"."orders"."tenant_id" = "public"."order_items"."tenant_id"' in compiled.sql
    assert '"public"."orders"."order_id" = "public"."order_items"."order_id"' in compiled.sql
    assert ' AND ' in compiled.sql


def test_single_column_foreign_key_preserved():
    """Standard single-column FKs continue to compile normal binary equality."""
    registry = make_registry(['customers', 'orders'], [('orders', 'customer_id', 'customers')])
    plan = QueryPlanner(registry).plan(dict(
        intent='LOOKUP', tables=['orders', 'customers'], select=['customers.name'],
    ))
    compiled = SQLBuilder(registry).build(plan)
    assert 'ON "public"."orders"."customer_id" = "public"."customers"."id"' in compiled.sql


# ---------------------------------------------------------------------------
# 3. Self-join with Role / Alias
# ---------------------------------------------------------------------------

def test_self_join_with_target_role_compiles_aliased_table():
    """SQLBuilder supports compiling a self-join with an aliased table role."""
    tables = [
        SchemaTable(name='employees', schema_name='public', columns=[
            SchemaColumn(name='id', data_type='int', primary_key=True),
            SchemaColumn(name='name', data_type='text'),
            SchemaColumn(name='manager_id', data_type='int'),
        ]),
    ]
    registry = SchemaRegistry(tables=tables, relationships=[])
    plan = QueryPlan(
        query=QueryIR(
            intent=Intent.LOOKUP,
            tables=['public.employees'],
            select=['public.employees.name'],
        ),
        tables=['public.employees'],
        joins=[
            SchemaRelationship(
                source='public.employees.manager_id',
                target='public.employees.id',
                target_role='manager',
            ),
        ],
    )
    compiled = SQLBuilder(registry)._compile_plan(plan)
    assert 'JOIN "public"."employees" AS "manager" ON "public"."employees"."manager_id" = "manager"."id"' in compiled.sql


# ---------------------------------------------------------------------------
# 4. Outer Join (LEFT OUTER JOIN)
# ---------------------------------------------------------------------------

def test_outer_join_compiles_left_outer_join():
    """When outer_joins is specified, SQLBuilder generates LEFT OUTER JOIN."""
    registry = make_registry(['customers', 'orders'], [('orders', 'customer_id', 'customers')])
    plan = QueryPlanner(registry).plan(dict(
        intent='LOOKUP',
        tables=['customers', 'orders'],
        select=['customers.id'],
        outer_joins=['orders'],
    ))
    assert plan.joins[0].is_outer is True
    compiled = SQLBuilder(registry).build(plan)
    assert 'LEFT OUTER JOIN "public"."orders"' in compiled.sql


def test_outer_join_executes_and_returns_null_for_unmatched_records():
    """LEFT OUTER JOIN preserves parent records when child rows are missing."""
    registry = make_registry(['customers', 'orders'], [('orders', 'customer_id', 'customers')])
    plan = QueryPlanner(registry).plan(dict(
        intent='LOOKUP',
        tables=['customers', 'orders'],
        select=['customers.id', 'orders.id'],
        outer_joins=['orders'],
    ))
    compiled = SQLBuilder(registry).build(plan)
    assert 'LEFT OUTER JOIN' in compiled.sql

    engine = create_engine('sqlite://')
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS public")
            conn.exec_driver_sql('CREATE TABLE public.customers (id INTEGER, name TEXT)')
            conn.exec_driver_sql('CREATE TABLE public.orders (id INTEGER, customer_id INTEGER)')
            conn.exec_driver_sql("INSERT INTO public.customers VALUES (1, 'Alice'), (2, 'Bob')")
            # Only Alice has an order; Bob has no orders
            conn.exec_driver_sql("INSERT INTO public.orders VALUES (100, 1)")
            rows = conn.execute(compiled.statement).fetchall()
            # Alice has order 100, Bob has NULL order
            assert len(rows) == 2
            assert rows[0] == (1, 100)
            assert rows[1] == (2, None)
    finally:
        engine.dispose()


def test_outer_join_rejects_the_root_table_instead_of_silently_ignoring_it():
    registry = make_registry(['customers', 'orders'], [('orders', 'customer_id', 'customers')])

    with pytest.raises(MalformedQueryIRError, match='non-root destinations'):
        QueryPlanner(registry).plan(dict(
            intent='LOOKUP', tables=['customers', 'orders'], select=['customers.id'],
            outer_joins=['customers'],
        ))


# ---------------------------------------------------------------------------
# 5. Ambiguous Join Error Message
# ---------------------------------------------------------------------------

def test_ambiguous_join_error_message_contains_candidate_paths():
    """AmbiguousJoinPathError explains which paths were ambiguous."""
    registry = make_registry(
        ['a', 'b_direct', 'b_indirect', 'c'],
        [
            ('b_direct', 'a_id', 'a'),
            ('c', 'b_id', 'b_direct'),
            ('b_indirect', 'a_id', 'a'),
            ('c', 'b_id', 'b_indirect'),
        ],
    )
    graph = RelationshipGraph(registry)
    with pytest.raises(AmbiguousJoinPathError) as exc_info:
        graph.find_relationship_path('public.a', 'public.c')

    msg = str(exc_info.value)
    assert 'Multiple shortest FK paths between' in msg
    assert 'public.a' in msg
    assert 'public.c' in msg
    # Confirms candidate paths details are exposed
    assert 'public.b_direct' in msg or 'public.b_indirect' in msg
    assert 'Please specify clarifying join hints' in msg

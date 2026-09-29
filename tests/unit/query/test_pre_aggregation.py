import pytest
from sqlalchemy import create_engine

from src.permission.models import PermissionContext
from src.permission.row_policy import ScopedRowPolicy
from src.query.handlers.basic import BasicQueryHandler
from src.query.ir import QueryIR
from src.query.planner import QueryPlanner
from src.schema.registry import SchemaRegistry
from src.shared.enums import Intent
from src.shared.exceptions import FanoutRiskError
from src.shared.models import SchemaColumn, SchemaRelationship, SchemaTable
from src.validation.sql_validator import ReadOnlySQLValidator


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry(
        tables=[
            SchemaTable(name='parents', schema_name='public', columns=[
                SchemaColumn(name='id', data_type='integer', primary_key=True),
                SchemaColumn(name='tenant_id', data_type='text'),
                SchemaColumn(name='amount', data_type='numeric'),
            ]),
            SchemaTable(name='children', schema_name='public', columns=[
                SchemaColumn(name='id', data_type='integer', primary_key=True),
                SchemaColumn(name='parent_id', data_type='integer'),
                SchemaColumn(name='tenant_id', data_type='text'),
                SchemaColumn(name='amount', data_type='numeric'),
            ]),
        ],
        relationships=[SchemaRelationship(
            source='public.children.parent_id', target='public.parents.id',
        )],
    )


@pytest.fixture
def engine():
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS public")
        connection.exec_driver_sql('CREATE TABLE public.parents (id INTEGER, tenant_id TEXT, amount NUMERIC)')
        connection.exec_driver_sql('CREATE TABLE public.children (id INTEGER, parent_id INTEGER, tenant_id TEXT, amount NUMERIC)')
        connection.exec_driver_sql("INSERT INTO public.parents VALUES (1, 'T1', 100), (2, 'T1', 50), (3, 'T1', 25), (4, 'T2', 900)")
        connection.exec_driver_sql("INSERT INTO public.children VALUES (10, 1, 'T1', 1), (11, 1, 'T1', 2), (12, 1, 'T1', 3), (13, 2, 'T1', 4), (14, 4, 'T2', 5)")
    try:
        yield engine
    finally:
        engine.dispose()


def aggregate_query(intent: Intent | str) -> QueryIR:
    return QueryIR(
        intent=intent, tables=['parents', 'children'], select=['parents.amount'],
        filters=[{'column': 'parents.tenant_id', 'value': 'T1'}],
    )


@pytest.mark.parametrize(('intent', 'expected'), [(Intent.SUM, 150), (Intent.AVG, 75)])
def test_scalar_parent_aggregate_pre_aggregates_at_parent_grain(registry, engine, intent, expected):
    handler = BasicQueryHandler(registry)
    compiled = handler.handle(aggregate_query(intent))

    assert 'FROM (SELECT' in compiled.sql
    assert 'GROUP BY "public"."parents"."id", "public"."parents"."amount"' in compiled.sql
    ReadOnlySQLValidator().validate(compiled.statement)
    with engine.connect() as connection:
        assert connection.execute(compiled.statement).scalar_one() == expected


def test_pre_aggregation_preserves_inner_join_no_child_semantics(registry, engine):
    compiled = BasicQueryHandler(registry).handle(aggregate_query(Intent.SUM))

    with engine.connect() as connection:
        # Parent 3 has no child and remains excluded, exactly as the original INNER JOIN did.
        assert connection.execute(compiled.statement).scalar_one() == 150


@pytest.mark.parametrize('intent', [Intent.SUM, Intent.AVG])
def test_pre_aggregation_preserves_empty_inner_join_aggregate_semantics(registry, engine, intent):
    query = QueryIR(
        intent=intent, tables=['parents', 'children'], select=['parents.amount'],
        filters=[{'column': 'parents.id', 'value': 3}],
    )
    compiled = BasicQueryHandler(registry).handle(query)

    with engine.connect() as connection:
        assert connection.execute(compiled.statement).scalar_one() is None


def test_pre_aggregation_keeps_tenant_filters_inside_the_parent_grain_subquery(registry, engine):
    query = ScopedRowPolicy(registry).apply(
        aggregate_query(Intent.SUM), PermissionContext(tenant_id='T1'),
    )
    compiled = BasicQueryHandler(registry).handle(query)

    assert 'WHERE "public"."parents"."tenant_id" = :p0' in compiled.sql
    assert '"public"."parents"."tenant_id" = :p1' in compiled.sql
    assert '"public"."children"."tenant_id" = :p2' in compiled.sql
    with engine.connect() as connection:
        assert connection.execute(compiled.statement).scalar_one() == 150


def test_child_owned_sum_without_fanout_keeps_ordinary_aggregate_sql(registry, engine):
    compiled = BasicQueryHandler(registry).handle(QueryIR(
        intent=Intent.SUM, tables=['children', 'parents'], select=['children.amount'],
    ))

    assert 'pre_aggregated' not in compiled.sql
    with engine.connect() as connection:
        assert connection.execute(compiled.statement).scalar_one() == 15


def test_forged_pre_aggregation_marker_is_ignored_by_public_builder_boundary(registry):
    handler = BasicQueryHandler(registry)
    plan = handler.planner.plan(QueryIR(
        intent=Intent.SUM, tables=['children', 'parents'], select=['children.amount'],
    ))
    plan.pre_aggregate_owner = 'public.parents'

    compiled = handler.builder.build(plan)
    assert 'pre_aggregated' not in compiled.sql


@pytest.mark.parametrize(('intent', 'expected'), [(Intent.COUNT, 3), (Intent.COUNT, 5)])
def test_existing_count_semantics_are_preserved(registry, engine, intent, expected):
    select = ['parents.id'] if expected == 3 else ['*']
    compiled = BasicQueryHandler(registry).handle(QueryIR(
        intent=intent, tables=['parents', 'children'], select=select,
    ))

    with engine.connect() as connection:
        assert connection.execute(compiled.statement).scalar_one() == expected


@pytest.mark.parametrize(('intent', 'expected'), [(Intent.SUM, [('T1', 150), ('T2', 900)]),
                                                   (Intent.AVG, [('T1', 75.0), ('T2', 900.0)])])
def test_grouped_parent_aggregate_pre_aggregates_at_owner_grain(registry, engine, intent, expected):
    compiled = BasicQueryHandler(registry).handle(QueryIR(
        intent=intent,
        tables=['parents', 'children'],
        select=['parents.tenant_id', 'parents.amount'],
        group_by=['parents.tenant_id'],
    ))

    assert 'pre_aggregated' in compiled.sql
    assert 'GROUP BY pre_aggregated._preagg_group_0' in compiled.sql
    with engine.connect() as connection:
        rows = [(row[0], float(row[1])) for row in connection.execute(compiled.statement)]
    assert rows == expected


def test_grouped_parent_sum_with_having_remains_fail_closed(registry):
    with pytest.raises(FanoutRiskError):
        QueryPlanner(registry).plan(QueryIR(
            intent=Intent.SUM,
            tables=['parents', 'children'],
            select=['parents.tenant_id', 'parents.amount'],
            group_by=['parents.tenant_id'],
            having=[{'column': 'parents.amount', 'aggregation': 'SUM', 'operator': '>', 'value': 1}],
        ))

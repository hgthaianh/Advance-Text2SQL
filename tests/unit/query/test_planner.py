from datetime import date
from decimal import Decimal

import pytest

from src.query.ir import FilterCondition, QueryFilter, QueryIR
from src.shared.enums import Aggregation, Intent
from src.shared.exceptions import (
    FanoutRiskError, InvalidColumnError, InvalidTableError, MalformedQueryIRError,
    MissingAggregationColumnError, UnsupportedAggregationError,
    UnsupportedIntentError, UnsupportedOperatorError,
)


def test_old_ir_shape_still_works(planner):
    ir = QueryIR(intent=Intent.SELECT, tables=['employees'], select=['name'], limit=10)
    plan = planner.plan(ir)
    assert plan.query.select[0].column == 'public.employees.name'
    assert ir.select == ['name'] and ir.tables == ['employees']
    assert plan.query.limit == 10


@pytest.mark.parametrize('intent, aggregation', [('COUNT','count'),('SUM','sum'),('AVG','avg'),('average','avg')])
def test_aggregate_intent(planner, intent, aggregation):
    plan = planner.plan(dict(intent=intent,tables=['orders'],select=['total_amount']))
    assert plan.query.select[0].aggregation == aggregation


def test_count_star_default(planner):
    plan = planner.plan(dict(intent='COUNT',tables=['orders']))
    assert plan.query.select[0].aggregation == Aggregation.COUNT
    assert plan.query.select[0].column == '*'


def test_explicit_aggregate_and_permission_filter(planner):
    permission_filter = FilterCondition(column='employees.department_id',operator='=',value=12,source='permission')
    assert isinstance(permission_filter, QueryFilter)
    plan = planner.plan(QueryIR(intent='COUNT',tables=['employees'],filters=[permission_filter]))
    assert plan.query.filters[0].source == 'permission'
    assert plan.query.filters[0].value == 12


def test_qualified_columns_add_owner_and_bridge_tables(planner):
    plan = planner.plan(dict(intent='LOOKUP',tables=['orders'],select=['customer_groups.name']))
    assert plan.tables == ['public.orders','public.customers','public.customer_groups']
    assert len(plan.joins) == 2


def test_related_permission_filter_is_not_dropped(planner):
    plan = planner.plan(dict(intent='COUNT',tables=['orders'],filters=[dict(column='customers.customer_type',operator='=',value='VIP',source='permission')]))
    assert len(plan.joins) == 1
    assert plan.query.filters[0].source == 'permission'


def test_parent_column_aggregate_rejects_child_join_fanout(planner):
    with pytest.raises(FanoutRiskError):
        planner.plan(dict(
            intent='SUM', tables=['customers', 'orders'],
            select=['customers.id'],
        ))


def test_parent_lookup_join_does_not_reject_child_owned_aggregate(planner):
    plan = planner.plan(dict(
        intent='SUM', tables=['orders', 'customers'], select=['orders.total_amount'],
    ))
    assert plan.query.select[0].column == 'public.orders.total_amount'


def test_parent_aggregate_rejects_fanout_beyond_a_safe_many_to_one_hop():
    from src.query.planner import QueryPlanner
    from src.schema.relationship_graph import RelationshipGraph
    from tests.unit.query.conftest import make_registry

    registry = make_registry(['orders', 'customers', 'customer_contacts'], [
        ('orders', 'customer_id', 'customers'),
        ('customer_contacts', 'customer_id', 'customers'),
    ])
    with pytest.raises(FanoutRiskError):
        QueryPlanner(registry, RelationshipGraph(registry)).plan(dict(
            intent='SUM', tables=['orders', 'customer_contacts'], select=['orders.id'],
        ))


@pytest.mark.parametrize('query,error', [
    (dict(intent='TREND',tables=['orders']), UnsupportedIntentError),
    (dict(intent='DELETE',tables=['orders']), UnsupportedIntentError),
    (dict(intent='LOOKUP',tables=['missing'],select=['id']), InvalidTableError),
    (dict(intent='LOOKUP',tables=['orders'],select=['orders.fake']), InvalidColumnError),
    (dict(intent='LOOKUP',tables=['orders','customers'],select=['id']), InvalidColumnError),
    (dict(intent='LOOKUP',tables=['orders'],select=['id'],filters=[dict(column='status',operator='REGEXP',value='x')]), UnsupportedOperatorError),
    (dict(intent='SUM',tables=['orders']), MissingAggregationColumnError),
    (dict(intent='AVG',tables=['orders'],select=[dict(aggregation='AVG')]), MissingAggregationColumnError),
    (dict(intent='SUM',tables=['orders'],select=['*']), MissingAggregationColumnError),
    (dict(intent='SUM',tables=['orders'],select=['status']), MalformedQueryIRError),
    (dict(intent='SUM',tables=['orders'],select=[dict(column='total_amount',aggregation='COUNT')]), MalformedQueryIRError),
    (dict(intent='LOOKUP',tables=['orders'],select=[dict(column='id',aggregation='evil')]), UnsupportedAggregationError),
    (dict(intent='LOOKUP',tables=['orders']), MalformedQueryIRError),
    (dict(intent='LOOKUP',tables=['orders'],select=['*']), MalformedQueryIRError),
    (dict(intent='COUNT'), MalformedQueryIRError),
    (dict(intent='COUNT',tables=['orders'],limit=0), MalformedQueryIRError),
    (dict(intent='COUNT',tables=['orders'],limit=True), MalformedQueryIRError),
    (dict(intent='COUNT',tables=['orders'],limit='10'), MalformedQueryIRError),
    (dict(intent='COUNT',tables=['orders'],window=[]), MalformedQueryIRError),
    (dict(intent='COUNT',tables=['orders'],order_by=[dict(column='status',direction='DESC; DROP TABLE orders')]), MalformedQueryIRError),
    (None, MalformedQueryIRError),
])
def test_domain_errors(planner, query, error):
    with pytest.raises(error): planner.plan(query)


@pytest.mark.parametrize('operator,value', [('IN','VIP'),('IN',[None]),('=',[1,2]),('LIKE',12),('>',None),('LIKE',None),('=',float('nan'))])
def test_malformed_filter_values(planner, operator, value):
    with pytest.raises(MalformedQueryIRError):
        planner.plan(dict(intent='COUNT',tables=['orders'],filters=[dict(column='status',operator=operator,value=value)]))


def test_grouped_sum(planner):
    plan = planner.plan(dict(intent='SUM',tables=['orders'],select=['customer_id','total_amount'],group_by=['customer_id'],order_by=[dict(column='total_amount',direction='DESC')]))
    assert plan.query.select[0].aggregation is None
    assert plan.query.select[1].aggregation == Aggregation.SUM


def test_mixed_projection_requires_group_by(planner):
    with pytest.raises(MalformedQueryIRError):
        planner.plan(dict(intent='SELECT',tables=['orders'],select=['status',dict(column='total_amount',aggregation='SUM')]))


def test_aggregate_ordering_rejects_unselected_un_grouped_column(planner):
    with pytest.raises(MalformedQueryIRError):
        planner.plan(dict(intent='COUNT',tables=['orders'],order_by=[dict(column='status')]))


def test_mutated_ir_is_revalidated(planner):
    ir = QueryIR(intent='COUNT',tables=['orders'])
    ir.limit = -1
    with pytest.raises(MalformedQueryIRError): planner.plan(ir)


def test_scalar_types_preserved(planner):
    values = [date(2026,9,19), Decimal('10.50')]
    plan = planner.plan(dict(intent='COUNT',tables=['orders'],filters=[dict(column=c,operator='>=',value=v) for c,v in zip(['order_date','total_amount'],values)]))
    assert [f.value for f in plan.query.filters] == values


def test_schema_qualified_names_disambiguate_tables():
    from src.schema.registry import SchemaRegistry
    from src.query.planner import QueryPlanner
    from src.shared.models import SchemaColumn, SchemaTable
    registry = SchemaRegistry([
        SchemaTable(name='items',schema_name=s,columns=[SchemaColumn(name='id',data_type='integer')])
        for s in ('sales','inventory')
    ])
    planner = QueryPlanner(registry)
    with pytest.raises(InvalidTableError): planner.plan(dict(intent='COUNT',tables=['items']))
    plan = planner.plan(dict(intent='LOOKUP',tables=['inventory.items'],select=['id']))
    assert plan.query.select[0].column == 'inventory.items.id'


def test_unknown_filter_group_and_order_columns_are_rejected(planner):
    for changes in [dict(filters=[dict(column='fake',operator='=',value=1)]),dict(group_by=['fake']),dict(order_by=[dict(column='fake')])]:
        with pytest.raises(InvalidColumnError):
            planner.plan(dict(intent='COUNT',tables=['orders'],**changes))


def test_numeric_type_and_projection_expression_validation(planner):
    with pytest.raises(InvalidColumnError):
        planner.plan(dict(intent='SUM',tables=['orders'],select=['total_amount + 1']))
    with pytest.raises(MalformedQueryIRError):
        planner.plan(dict(intent='COUNT',tables=['orders'],limit=1001))

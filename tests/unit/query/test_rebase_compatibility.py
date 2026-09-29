"""Integration contracts shared by the rebased schema and query features."""
import pytest

from src.permission.models import PermissionContext
from src.query.handlers.basic import BasicQueryHandler
from src.query.ir import QueryIR
from src.query.planner import FKQueryPlanner
from src.schema.linker import SchemaLinker
from src.schema.registry import SchemaRegistry
from src.schema.relationship_graph import FKRelationshipGraph
from src.shared.exceptions import AmbiguousJoinPathError, MalformedQueryIRError, PermissionDeniedError


def test_yaml_registry_serves_linker_and_builder():
    registry = SchemaRegistry()
    assert len(registry.tables) == 100
    assert SchemaLinker(registry).link_columns('doanh thu')[0].name == 'sales.orders.total_amount'
    compiled = BasicQueryHandler(registry).handle(dict(
        intent='SUM', tables=['orders', 'customers'], select=['orders.total_amount'],
        filters=[dict(column='customers.customer_type', operator='=', value='VIP')],
    ))
    assert 'LIMIT' not in compiled.sql
    assert compiled.params == {'p0': 'VIP'}
    graph = FKRelationshipGraph.from_registry(registry)
    assert graph.find_path('orders', 'customers') == [('sales.orders.customer_id', 'customer.customers.id')]


def test_legacy_planner_rejects_ambiguous_fk():
    graph = FKRelationshipGraph()
    graph.add_relationship('orders', 'buyer_id', 'customers', 'id')
    graph.add_relationship('orders', 'recipient_id', 'customers', 'id')
    with pytest.raises(AmbiguousJoinPathError):
        FKQueryPlanner(graph).plan(QueryIR(tables=['orders', 'customers']), PermissionContext(allowed_tables=['*']))


def test_injected_filter_owner_is_authorized_before_legacy_planning():
    graph = FKRelationshipGraph()
    graph.add_relationship('orders', 'customer_id', 'customers', 'id')
    permissions = PermissionContext(allowed_tables=['orders'], row_filters=[dict(column='customers.id', value=1)])
    with pytest.raises(PermissionDeniedError):
        FKQueryPlanner(graph).plan(QueryIR(tables=['orders']), permissions)


def test_forged_legacy_join_is_not_silently_ignored(registry):
    with pytest.raises(MalformedQueryIRError):
        BasicQueryHandler(registry).handle(dict(
            intent='COUNT', tables=['orders','customers'],
            joins=[('orders.id','customers.id')],
        ))

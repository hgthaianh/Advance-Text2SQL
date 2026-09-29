import pytest

from src.permission.models import PermissionContext
from src.query.ir import QueryFilter, QueryIR
from src.query.join_resolver import FKJoinResolver
from src.query.planner import FKQueryPlanner
from src.schema.relationship_graph import FKRelationshipGraph
from src.shared.enums import FilterOperator, Intent
from src.shared.exceptions import PermissionDeniedError


@pytest.fixture
def ecommerce_planner() -> FKQueryPlanner:
    graph = FKRelationshipGraph()
    graph.add_relationship("products", "category_id", "categories", "id")
    graph.add_relationship("order_items", "product_id", "products", "id")
    graph.add_relationship("order_items", "order_id", "orders", "id")
    graph.add_relationship("orders", "customer_id", "customers", "id")

    resolver = FKJoinResolver()
    return FKQueryPlanner(graph=graph, resolver=resolver)


def test_planner_resolves_joins_and_tables(ecommerce_planner: FKQueryPlanner):
    query = QueryIR(
        intent=Intent.SUM,
        select=["categories.name", "customers.name"],
        tables=["categories", "customers"],
    )
    permissions = PermissionContext(allowed_tables=["*"])

    planned_ir = ecommerce_planner.plan(query, permissions)

    assert "categories" in planned_ir.tables
    assert "products" in planned_ir.tables
    assert "order_items" in planned_ir.tables
    assert "orders" in planned_ir.tables
    assert "customers" in planned_ir.tables
    assert len(planned_ir.joins) == 4


def test_planner_denies_unallowed_table(ecommerce_planner: FKQueryPlanner):
    query = QueryIR(
        intent=Intent.LIST,
        tables=["secret_table"],
    )
    permissions = PermissionContext(allowed_tables=["orders", "customers"])

    with pytest.raises(PermissionDeniedError, match="Access denied to table 'secret_table'"):
        ecommerce_planner.plan(query, permissions)


def test_planner_denies_empty_allowlist(ecommerce_planner: FKQueryPlanner):
    query = QueryIR(
        intent=Intent.LIST,
        tables=["orders"],
    )
    permissions = PermissionContext(allowed_tables=[])

    with pytest.raises(PermissionDeniedError, match="Empty allowlist denies table access"):
        ecommerce_planner.plan(query, permissions)


def test_planner_injects_row_filters(ecommerce_planner: FKQueryPlanner):
    query = QueryIR(
        intent=Intent.LIST,
        tables=["orders"],
        select=["orders.id", "orders.total_amount"],
    )
    row_filter = QueryFilter(
        column="orders.branch_id",
        operator=FilterOperator.EQ,
        value="BRANCH_001",
    )
    permissions = PermissionContext(
        allowed_tables=["orders"],
        row_filters=[row_filter],
    )

    planned_ir = ecommerce_planner.plan(query, permissions)

    assert row_filter in planned_ir.filters


def test_planner_denies_unauthorized_intermediate_table(ecommerce_planner: FKQueryPlanner):
    query = QueryIR(
        intent=Intent.LIST,
        tables=["categories", "customers"],
    )
    # categories and customers are allowed, but intermediate table 'order_items' is NOT in allowed_tables
    permissions = PermissionContext(allowed_tables=["categories", "products", "orders", "customers"])

    with pytest.raises(PermissionDeniedError, match="Access denied to intermediate table 'order_items'"):
        ecommerce_planner.plan(query, permissions)


def test_planner_handles_no_path_disconnected_graph(ecommerce_planner: FKQueryPlanner):
    # Add an isolated table to graph
    ecommerce_planner.graph.add_relationship("isolated_a", "id", "isolated_b", "id")

    query = QueryIR(
        intent=Intent.LIST,
        tables=["orders", "isolated_a"],
    )
    permissions = PermissionContext(allowed_tables=["*"])

    from src.shared.exceptions import JoinPathNotFoundError
    # A disconnected plan must not reach a builder as a potential Cartesian join.
    with pytest.raises(JoinPathNotFoundError):
        ecommerce_planner.plan(query, permissions)

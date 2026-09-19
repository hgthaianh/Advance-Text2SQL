import pytest

from src.schema.relationship_graph import FKRelationshipGraph
from src.query.join_resolver import FKJoinResolver
from src.shared.models import SchemaTable, SchemaColumn


@pytest.fixture
def ecommerce_graph() -> FKRelationshipGraph:
    """Fixture defining a 5-table chain:
    categories <1-hop> products <2-hop> order_items <3-hop> orders <4-hop> customers <5-hop> regions
    """
    graph = FKRelationshipGraph()
    graph.add_relationship("products", "category_id", "categories", "id")
    graph.add_relationship("order_items", "product_id", "products", "id")
    graph.add_relationship("order_items", "order_id", "orders", "id")
    graph.add_relationship("orders", "customer_id", "customers", "id")
    graph.add_relationship("customers", "region_id", "regions", "id")
    return graph


def test_1_hop_join_path(ecommerce_graph: FKRelationshipGraph):
    path = ecommerce_graph.find_path("orders", "customers")
    assert path == [("orders.customer_id", "customers.id")]


def test_2_hop_join_path(ecommerce_graph: FKRelationshipGraph):
    path = ecommerce_graph.find_path("order_items", "customers")
    assert path == [
        ("order_items.order_id", "orders.id"),
        ("orders.customer_id", "customers.id"),
    ]


def test_3_hop_join_path(ecommerce_graph: FKRelationshipGraph):
    path = ecommerce_graph.find_path("products", "customers")
    assert path == [
        ("products.id", "order_items.product_id"),
        ("order_items.order_id", "orders.id"),
        ("orders.customer_id", "customers.id"),
    ]


def test_4_hop_join_path(ecommerce_graph: FKRelationshipGraph):
    path = ecommerce_graph.find_path("categories", "customers")
    assert path == [
        ("categories.id", "products.category_id"),
        ("products.id", "order_items.product_id"),
        ("order_items.order_id", "orders.id"),
        ("orders.customer_id", "customers.id"),
    ]


def test_same_table_returns_empty_path(ecommerce_graph: FKRelationshipGraph):
    assert ecommerce_graph.find_path("orders", "orders") == []


def test_unconnected_table_returns_empty_path(ecommerce_graph: FKRelationshipGraph):
    assert ecommerce_graph.find_path("orders", "non_existent_table") == []


def test_load_schema_tables():
    tables = [
        SchemaTable(
            name="orders",
            columns=[
                SchemaColumn(name="id", data_type="integer", primary_key=True),
                SchemaColumn(name="customer_id", data_type="integer", foreign_key="customers.id"),
            ],
        ),
        SchemaTable(
            name="customers",
            columns=[
                SchemaColumn(name="id", data_type="integer", primary_key=True),
            ],
        ),
    ]
    graph = FKRelationshipGraph.from_schema_tables(tables)
    path = graph.find_path("orders", "customers")
    assert path == [("orders.customer_id", "customers.id")]


def test_fk_join_resolver_with_bridge_tables(ecommerce_graph: FKRelationshipGraph):
    resolver = FKJoinResolver()
    tables, joins = resolver.resolve_with_intermediate_tables(
        ["categories", "customers"], ecommerce_graph
    )
    # Intermediate tables (products, order_items, orders) should be auto-inserted
    assert "categories" in tables
    assert "products" in tables
    assert "order_items" in tables
    assert "orders" in tables
    assert "customers" in tables
    assert len(joins) == 4


def test_fk_join_resolver_deterministic_ordering(ecommerce_graph: FKRelationshipGraph):
    resolver = FKJoinResolver()
    joins1 = resolver.resolve(["categories", "customers"], ecommerce_graph)
    joins2 = resolver.resolve(["categories", "customers"], ecommerce_graph)
    assert joins1 == joins2

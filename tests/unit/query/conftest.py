import pytest

from src.query.planner import QueryPlanner
from src.query.sql_builder import SQLBuilder
from src.schema.registry import SchemaRegistry
from src.schema.relationship_graph import RelationshipGraph
from src.shared.models import SchemaColumn, SchemaRelationship, SchemaTable


def make_registry(names, edges):
    """Small graph fixture; each edge names its actual FK column."""
    columns = {name: {'id': 'integer', 'name': 'text'} for name in names}
    for left, field, right in edges:
        columns[left][field] = 'integer'
    tables = [SchemaTable(name=n, columns=[SchemaColumn(name=c, data_type=t) for c,t in fields.items()]) for n,fields in columns.items()]
    relationships = [SchemaRelationship(source=f'public.{a}.{c}', target=f'public.{b}.id') for a,c,b in edges]
    return SchemaRegistry(tables, relationships)


@pytest.fixture
def registry():
    fields = {
        'orders': [('id', 'integer'), ('customer_id', 'integer'), ('total_amount', 'numeric(18,2)'), ('order_date', 'date'), ('status', 'text')],
        'customers': [('id', 'integer'), ('name', 'text'), ('customer_type', 'text'), ('group_id', 'integer')],
        'customer_groups': [('id', 'integer'), ('name', 'text')],
        'employees': [('id', 'integer'), ('name', 'text'), ('salary', 'numeric'), ('department_id', 'integer')],
        'departments': [('id', 'integer'), ('name', 'text')],
        'isolated': [('id', 'integer')],
    }
    tables = [SchemaTable(name=n, columns=[SchemaColumn(name=c, data_type=t) for c,t in columns]) for n,columns in fields.items()]
    relationships = [SchemaRelationship(source=a,target=b) for a,b in [
        ('orders.customer_id','customers.id'), ('customers.group_id','customer_groups.id'),
        ('employees.department_id','departments.id'),
    ]]
    return SchemaRegistry(tables, relationships)


@pytest.fixture
def graph(registry):
    return RelationshipGraph(registry)


@pytest.fixture
def planner(registry, graph):
    return QueryPlanner(registry, graph)


@pytest.fixture
def builder(registry, graph):
    return SQLBuilder(registry, graph)

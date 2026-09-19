"""Small entry point for lookup, filtering and basic aggregate queries."""

from src.query.ir import QueryIR
from src.query.models import CompiledQuery
from src.query.planner import QueryPlanner
from src.query.sql_builder import SQLBuilder
from src.schema.registry import SchemaRegistry
from src.schema.relationship_graph import RelationshipGraph


class BasicQueryHandler:
    def __init__(self, registry: SchemaRegistry, graph: RelationshipGraph | None = None, *, max_limit: int | None = None) -> None:
        self.planner = QueryPlanner(registry, graph, max_limit=max_limit)
        self.builder = SQLBuilder(registry, self.planner.graph, max_limit=self.planner.max_limit)

    def handle(self, query: QueryIR | dict) -> CompiledQuery:
        """Plan and compile IR, preserving all upstream-injected filters."""
        return self.builder.build(self.planner.plan(query))

"""Assemble joins using the shared graph; never search schema paths here."""

from src.schema.relationship_graph import RelationshipGraph
from src.shared.exceptions import AmbiguousJoinPathError, MalformedQueryIRError
from src.shared.models import SchemaRelationship


class JoinResolver:
    def resolve(self, tables: list[str], graph: RelationshipGraph) -> list[SchemaRelationship]:
        """Merge unique shortest root-to-target paths in requested-table order.

        This is a rooted shortest-path tree, not a minimum-cost/Steiner optimizer.
        Intermediate tables are preserved, and shared edges occur only once.
        """
        if not tables:
            raise MalformedQueryIRError('At least one table is required')
        canonical = list(dict.fromkeys(graph.registry.resolve_table(t) if graph.registry else t for t in tables))
        joined = {canonical[0]}
        result: list[SchemaRelationship] = []
        edges = set()
        for target in canonical[1:]:
            for edge in graph.find_relationship_path(canonical[0], target):
                key = (edge.source, edge.target)
                if key in edges:
                    continue
                left, right = edge.source.rsplit('.', 1)[0], edge.target.rsplit('.', 1)[0]
                if left not in joined or right in joined:
                    raise AmbiguousJoinPathError('FK paths require conflicting table roles')
                result.append(edge)
                edges.add(key)
                joined.add(right)
        return result


class FKJoinResolver:
    """Upstream column-pair API backed by the strict query-engine resolver."""

    def resolve(self, tables: list[str], graph: RelationshipGraph) -> list[tuple[str, str]]:
        return self.resolve_with_intermediate_tables(tables, graph)[1]

    def resolve_with_intermediate_tables(self, tables: list[str], graph: RelationshipGraph) -> tuple[list[str], list[tuple[str, str]]]:
        if not tables:
            return [], []
        edges = JoinResolver().resolve(tables, graph)
        root = graph.registry.resolve_table(tables[0]) if graph.registry else tables[0]
        all_tables = [root] + [e.target.rsplit('.', 1)[0] for e in edges]
        return all_tables, [(e.source, e.target) for e in edges]

"""In-memory foreign-key connectivity for schema candidate reranking and join path resolution."""

from collections import deque
from typing import Self

from src.schema.registry import SchemaRegistry
from src.shared.models import SchemaTable


class RelationshipGraph:
    """Undirected table-level foreign-key graph for schema linking and connectivity."""

    def __init__(self, registry: SchemaRegistry | None = None) -> None:
        self.registry = registry
        self.adjacency: dict[str, set[str]] = {}
        if registry is not None:
            self.load_registry(registry)

    def load_registry(self, registry: SchemaRegistry) -> None:
        self.registry = registry
        self.adjacency = {name: set() for name in registry.tables}
        for relationship in registry.relationships:
            source = relationship.source.rsplit(".", 1)[0]
            target = relationship.target.rsplit(".", 1)[0]
            if source not in self.adjacency:
                self.adjacency[source] = set()
            if target not in self.adjacency:
                self.adjacency[target] = set()
            self.adjacency[source].add(target)
            self.adjacency[target].add(source)

    def find_path(self, source: str, target: str) -> list[tuple[str, str]]:
        """Return a deterministic shortest undirected table path [("table1", "table2"), ...], or an empty list."""
        if self.registry is not None:
            try:
                source, target = self.registry.resolve_table(source), self.registry.resolve_table(target)
            except ValueError:
                return []
        if source not in self.adjacency or target not in self.adjacency:
            return []
        queue = deque([(source, [])])
        visited = {source}
        while queue:
            current, path = queue.popleft()
            if current == target:
                return path
            for neighbor in sorted(self.adjacency[current] - visited):
                visited.add(neighbor)
                queue.append((neighbor, path + [(current, neighbor)]))
        return []


class FKRelationshipGraph(RelationshipGraph):
    """Foreign key relationship graph supporting 1-4 hop shortest column-level join paths with deterministic tie-breaking."""

    def __init__(
        self,
        schema_tables: list[SchemaTable] | None = None,
        registry: SchemaRegistry | None = None,
    ) -> None:
        super().__init__(registry=registry)
        # Table -> list of edges: {"target": str, "src_col": str, "target_col": str}
        self._adj: dict[str, list[dict[str, str]]] = {}
        if registry is not None:
            self.load_registry(registry)
        if schema_tables:
            self.load_schema_tables(schema_tables)

    def load_registry(self, registry: SchemaRegistry) -> None:
        super().load_registry(registry)
        for rel in registry.relationships:
            src_parts = rel.source.rsplit(".", 1)
            tgt_parts = rel.target.rsplit(".", 1)
            if len(src_parts) == 2 and len(tgt_parts) == 2:
                src_table, src_col = src_parts
                tgt_table, tgt_col = tgt_parts
                self.add_relationship(src_table, src_col, tgt_table, tgt_col)

    def add_relationship(self, from_table: str, from_col: str, to_table: str, to_col: str) -> None:
        """Add an undirected foreign key edge between two tables."""
        if from_table not in self._adj:
            self._adj[from_table] = []
        if to_table not in self._adj:
            self._adj[to_table] = []

        # Avoid duplicate edge definitions
        edge_forward = {"target": to_table, "src_col": f"{from_table}.{from_col}", "target_col": f"{to_table}.{to_col}"}
        if edge_forward not in self._adj[from_table]:
            self._adj[from_table].append(edge_forward)

        edge_backward = {"target": from_table, "src_col": f"{to_table}.{to_col}", "target_col": f"{from_table}.{from_col}"}
        if edge_backward not in self._adj[to_table]:
            self._adj[to_table].append(edge_backward)

    def load_schema_tables(self, schema_tables: list[SchemaTable]) -> None:
        """Extract foreign key relationships from schema table definitions."""
        for table in schema_tables:
            if table.name not in self._adj:
                self._adj[table.name] = []

            for col in table.columns:
                if col.foreign_key:
                    # Format can be "target_table.target_col" or "schema.target_table.target_col"
                    parts = col.foreign_key.split(".")
                    if len(parts) >= 2:
                        target_table = parts[-2]
                        target_col = parts[-1]
                        self.add_relationship(table.name, col.name, target_table, target_col)

    @classmethod
    def from_schema_tables(cls, schema_tables: list[SchemaTable]) -> Self:
        return cls(schema_tables=schema_tables)

    @classmethod
    def from_registry(cls, registry: SchemaRegistry) -> Self:
        return cls(registry=registry)

    def find_path(self, source: str, target: str) -> list[tuple[str, str]]:
        """Find the shortest join-path between source and target table.

        Returns a list of join condition pairs [("table_a.col_a", "table_b.col_b"), ...].
        """
        resolved_source = source
        resolved_target = target
        if self.registry is not None:
            try:
                resolved_source = self.registry.resolve_table(source)
                resolved_target = self.registry.resolve_table(target)
            except ValueError:
                pass

        if resolved_source == resolved_target:
            return []

        if resolved_source not in self._adj or resolved_target not in self._adj:
            return []

        queue: deque[tuple[str, list[tuple[str, str]], list[str]]] = deque([(resolved_source, [], [resolved_source])])
        visited: set[str] = {resolved_source}

        while queue:
            curr_table, path_conditions, visited_tables = queue.popleft()

            # Sort neighbor edges for deterministic ordering
            edges = sorted(
                self._adj.get(curr_table, []),
                key=lambda e: (e["target"], e["src_col"], e["target_col"])
            )

            for edge in edges:
                nxt_table = edge["target"]
                join_cond = (edge["src_col"], edge["target_col"])
                new_conditions = path_conditions + [join_cond]

                if nxt_table == resolved_target:
                    return new_conditions

                if nxt_table not in visited:
                    visited.add(nxt_table)
                    queue.append((nxt_table, new_conditions, visited_tables + [nxt_table]))

        return []

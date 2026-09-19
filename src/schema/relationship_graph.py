from abc import ABC, abstractmethod
from collections import deque
from typing import Self

from src.shared.models import SchemaTable


class RelationshipGraph(ABC):
    @abstractmethod
    def find_path(self, source: str, target: str) -> list[tuple[str, str]]:
        """Find shortest join-path between source and target tables."""
        raise NotImplementedError


class FKRelationshipGraph(RelationshipGraph):
    """Foreign key relationship graph supporting 1-4 hop shortest join paths with deterministic tie-breaking."""

    def __init__(self, schema_tables: list[SchemaTable] | None = None) -> None:
        # Table -> list of edges: {"target": str, "src_col": str, "target_col": str}
        self._adj: dict[str, list[dict[str, str]]] = {}
        if schema_tables:
            self.load_schema_tables(schema_tables)

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
        return cls(schema_tables)

    def find_path(self, source: str, target: str) -> list[tuple[str, str]]:
        """Find the shortest join-path between source and target table.

        Returns a list of join condition pairs [("table_a.col_a", "table_b.col_b"), ...].
        """
        if source == target:
            return []

        if source not in self._adj or target not in self._adj:
            return []

        queue: deque[tuple[str, list[tuple[str, str]], list[str]]] = deque([(source, [], [source])])
        visited: set[str] = {source}

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

                if nxt_table == target:
                    return new_conditions

                if nxt_table not in visited:
                    visited.add(nxt_table)
                    queue.append((nxt_table, new_conditions, visited_tables + [nxt_table]))

        return []


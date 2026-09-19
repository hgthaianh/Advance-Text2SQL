from abc import ABC, abstractmethod

from src.schema.relationship_graph import RelationshipGraph


class JoinResolver(ABC):
    @abstractmethod
    def resolve(self, tables: list[str], graph: RelationshipGraph) -> list[tuple[str, str]]:
        """Resolve valid join path conditions connecting the input tables using relationship graph."""
        raise NotImplementedError


class FKJoinResolver(JoinResolver):
    """Deterministic foreign key join resolver supporting 1-4 hop paths with bridge table resolution."""

    def resolve(self, tables: list[str], graph: RelationshipGraph) -> list[tuple[str, str]]:
        """Resolve join path conditions for candidate tables."""
        all_tables, joins = self.resolve_with_intermediate_tables(tables, graph)
        return joins

    def resolve_with_intermediate_tables(
        self, tables: list[str], graph: RelationshipGraph
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """Resolve join path conditions and return full list of required tables including intermediate bridge tables."""
        if not tables:
            return [], []

        # Deduplicate while preserving order
        unique_tables: list[str] = []
        for t in tables:
            if t and t not in unique_tables:
                unique_tables.append(t)

        if len(unique_tables) <= 1:
            return unique_tables, []

        connected_tables: list[str] = [unique_tables[0]]
        join_conditions: list[tuple[str, str]] = []
        seen_conditions: set[tuple[str, str]] = set()

        for target in unique_tables[1:]:
            if target in connected_tables:
                continue

            # Find shortest path from any already connected table to target
            best_start: str | None = None
            best_path: list[tuple[str, str]] | None = None

            for src in connected_tables:
                path = graph.find_path(src, target)
                if not path:
                    continue
                if best_path is None or len(path) < len(best_path) or (
                    len(path) == len(best_path) and str(path) < str(best_path)
                ):
                    best_path = path
                    best_start = src

            if best_path:
                for left_col, right_col in best_path:
                    cond = (left_col, right_col)
                    inv_cond = (right_col, left_col)

                    if cond not in seen_conditions and inv_cond not in seen_conditions:
                        join_conditions.append(cond)
                        seen_conditions.add(cond)

                    # Add intermediate tables
                    table_left = left_col.split(".")[0]
                    table_right = right_col.split(".")[0]
                    if table_left not in connected_tables:
                        connected_tables.append(table_left)
                    if table_right not in connected_tables:
                        connected_tables.append(table_right)
            else:
                if target not in connected_tables:
                    connected_tables.append(target)

        return connected_tables, join_conditions


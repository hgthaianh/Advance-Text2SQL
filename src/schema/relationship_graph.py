"""Foreign-key graph with unique shortest-path resolution, shared by planners."""

from collections import defaultdict, deque
from typing import Self

from src.schema.registry import SchemaRegistry
from src.shared.exceptions import AmbiguousJoinPathError, JoinPathNotFoundError
from src.shared.models import SchemaRelationship, SchemaTable


class RelationshipGraph:
    def __init__(self, registry: SchemaRegistry | None = None) -> None:
        self.registry = None
        self.adjacency: dict[str, set[str]] = {}
        self._edges: dict[str, list[SchemaRelationship]] = defaultdict(list)
        if registry is not None:
            self.load_registry(registry)

    def load_registry(self, registry: SchemaRegistry) -> None:
        self.registry = registry
        self.adjacency = {name: set() for name in registry.tables}
        self._edges = defaultdict(list)
        for relation in registry.relationships:
            a, ac = relation.source.rsplit('.', 1)
            b, bc = relation.target.rsplit('.', 1)
            self.add_relationship(a, ac, b, bc)

    def add_relationship(self, from_table: str, from_col: str, to_table: str, to_col: str) -> None:
        if self.registry is not None:
            source = self.registry.resolve_column(f'{from_table}.{from_col}')
            target = self.registry.resolve_column(f'{to_table}.{to_col}')
            from_table, from_col = source.rsplit('.', 1)
            to_table, to_col = target.rsplit('.', 1)
        for a, ac, b, bc in ((from_table, from_col, to_table, to_col), (to_table, to_col, from_table, from_col)):
            edge = SchemaRelationship(source=f'{a}.{ac}', target=f'{b}.{bc}')
            self.adjacency.setdefault(a, set()).add(b)
            if edge not in self._edges[a]:
                self._edges[a].append(edge)
                self._edges[a].sort(key=lambda e: (e.target, e.source))

    def find_relationship_path(self, source: str, target: str,
                               preferred_edges: set[frozenset[str]] | None = None) -> list[SchemaRelationship]:
        """Return the unique shortest FK path, oriented from source to target.

        Parallel foreign keys are distinct paths. Equal-length alternatives fail
        closed. Counts saturate at two, so ambiguous graphs do not explode.
        """
        if self.registry is not None:
            source, target = self.registry.resolve_table(source), self.registry.resolve_table(target)
        if source not in self.adjacency or target not in self.adjacency:
            raise JoinPathNotFoundError(f'No FK path between {source} and {target}')
        # Find the shortest-path DAG first. A hint can then select a unique
        # best path within the same distance without ever preferring a longer
        # route or falling back to traversal order.
        distance = {source: 0}
        predecessors: dict[str, list[SchemaRelationship]] = defaultdict(list)
        queue = deque([source])
        while queue:
            current = queue.popleft()
            for edge in self._edges.get(current, []):
                neighbor = edge.target.rsplit('.', 1)[0]
                next_distance = distance[current] + 1
                if neighbor not in distance:
                    distance[neighbor] = next_distance
                    queue.append(neighbor)
                if distance.get(neighbor) == next_distance:
                    predecessors[neighbor].append(edge)
        if target not in distance:
            raise JoinPathNotFoundError(f'No FK path between {source} and {target}')
        hints = preferred_edges or set()
        best_score = {source: 0}
        counts = {source: 1}
        parents: dict[str, SchemaRelationship] = {}
        for node, _ in sorted(distance.items(), key=lambda item: (item[1], item[0])):
            if node == source:
                continue
            score, count, parent = -1, 0, None
            for edge in predecessors[node]:
                previous = edge.source.rsplit('.', 1)[0]
                edge_key = frozenset((edge.source, edge.target))
                candidate_score = best_score[previous] + int(edge_key in hints)
                if candidate_score > score:
                    score, count, parent = candidate_score, counts[previous], edge
                elif candidate_score == score:
                    count = min(2, count + counts[previous])
            best_score[node], counts[node] = score, count
            if parent is not None:
                parents[node] = parent
        if counts[target] > 1:
            raise AmbiguousJoinPathError(f'Multiple shortest FK paths between {source} and {target}')
        path = []
        while target != source:
            edge = parents[target]
            path.append(edge)
            target = edge.source.rsplit('.', 1)[0]
        return list(reversed(path))

    def find_path(self, source: str, target: str) -> list[tuple[str, str]]:
        """Preserve the table-pair API; use find_relationship_path for FK columns."""
        try:
            return [(r.source.rsplit('.', 1)[0], r.target.rsplit('.', 1)[0]) for r in self.find_relationship_path(source, target)]
        except JoinPathNotFoundError:
            return []


class FKRelationshipGraph(RelationshipGraph):
    """Compatibility API returning column-pair paths, using the same strict BFS."""

    def __init__(self, schema_tables: list[SchemaTable] | None = None, registry: SchemaRegistry | None = None) -> None:
        super().__init__(registry)
        if schema_tables:
            self.load_schema_tables(schema_tables)

    def load_schema_tables(self, schema_tables: list[SchemaTable]) -> None:
        registry = SchemaRegistry(schema_tables)
        self.load_registry(registry)
        self._short_output = all(t.schema_name == 'public' for t in schema_tables)

    @classmethod
    def from_schema_tables(cls, schema_tables: list[SchemaTable]) -> Self:
        return cls(schema_tables=schema_tables)

    @classmethod
    def from_registry(cls, registry: SchemaRegistry) -> Self:
        return cls(registry=registry)

    def find_path(self, source: str, target: str) -> list[tuple[str, str]]:
        try:
            path = self.find_relationship_path(source, target)
        except JoinPathNotFoundError:
            return []
        if getattr(self, '_short_output', False):
            return [(e.source.split('.', 1)[1], e.target.split('.', 1)[1]) for e in path]
        return [(e.source, e.target) for e in path]

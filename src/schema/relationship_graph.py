"""Foreign-key graph with unique semantic-cost path resolution, shared by planners."""

from collections import defaultdict
from heapq import heappop, heappush
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
            self.add_relationship(a, ac, b, bc, composite_keys=relation.composite_keys)

    def add_relationship(self, from_table: str, from_col: str, to_table: str, to_col: str,
                         composite_keys: list[tuple[str, str]] | None = None) -> None:
        if self.registry is not None:
            source = self.registry.resolve_column(f'{from_table}.{from_col}')
            target = self.registry.resolve_column(f'{to_table}.{to_col}')
            from_table, from_col = source.rsplit('.', 1)
            to_table, to_col = target.rsplit('.', 1)
        comps = list(composite_keys) if composite_keys else []
        reverse_comps = [(t, s) for s, t in comps]
        for (a, ac, b, bc), ck in (((from_table, from_col, to_table, to_col), comps),
                                   ((to_table, to_col, from_table, from_col), reverse_comps)):
            edge = SchemaRelationship(source=f'{a}.{ac}', target=f'{b}.{bc}', composite_keys=ck)
            self.adjacency.setdefault(a, set()).add(b)
            if edge not in self._edges[a]:
                self._edges[a].append(edge)
                self._edges[a].sort(key=lambda e: (e.target, e.source))

    def find_relationship_path(self, source: str, target: str,
                               preferred_edges: set[frozenset[str]] | None = None) -> list[SchemaRelationship]:
        """Return the unique lowest-cost FK path, oriented source to target.

        Normal edges cost 10, explicit semantic-role hints cost 6, and generic
        tenant-scope edges cost 14. This preserves shortest-path behavior in the
        absence of evidence while allowing a fully evidenced business path to
        beat an unrelated scope shortcut. Equal-cost alternatives fail closed.
        """
        if self.registry is not None:
            source, target = self.registry.resolve_table(source), self.registry.resolve_table(target)
        if source not in self.adjacency or target not in self.adjacency:
            raise JoinPathNotFoundError(f'No FK path between {source} and {target}')
        hints = preferred_edges or set()
        distance = {source: 0}
        predecessors: dict[str, list[SchemaRelationship]] = defaultdict(list)
        queue: list[tuple[int, str]] = [(0, source)]
        while queue:
            current_distance, current = heappop(queue)
            if current_distance != distance[current]:
                continue
            for edge in self._edges.get(current, []):
                neighbor = edge.target.rsplit('.', 1)[0]
                edge_key = frozenset((edge.source, edge.target))
                edge_cost = 6 if edge_key in hints else 10
                if '.tenant_id' in edge.source or '.tenant_id' in edge.target:
                    edge_cost += 4
                next_distance = current_distance + edge_cost
                if next_distance < distance.get(neighbor, 10**9):
                    distance[neighbor] = next_distance
                    predecessors[neighbor] = [edge]
                    heappush(queue, (next_distance, neighbor))
                elif distance.get(neighbor) == next_distance:
                    predecessors[neighbor].append(edge)
        if target not in distance:
            raise JoinPathNotFoundError(f'No FK path between {source} and {target}')
        counts = {source: 1}
        parents: dict[str, SchemaRelationship] = {}
        for node, _ in sorted(distance.items(), key=lambda item: (item[1], item[0])):
            if node == source:
                continue
            count, parent = 0, None
            for edge in predecessors[node]:
                previous = edge.source.rsplit('.', 1)[0]
                count = min(2, count + counts[previous])
                if parent is None:
                    parent = edge
            counts[node] = count
            if parent is not None:
                parents[node] = parent
        if counts[target] > 1:
            candidate_paths: list[list[SchemaRelationship]] = []

            def _collect_paths(curr: str, current_path: list[SchemaRelationship]) -> None:
                if curr == source:
                    candidate_paths.append(list(reversed(current_path)))
                    return
                if len(candidate_paths) >= 4:
                    return
                for edge in predecessors[curr]:
                    prev_node = edge.source.rsplit('.', 1)[0]
                    _collect_paths(prev_node, [*current_path, edge])

            _collect_paths(target, [])
            candidate_strs = []
            for cp in candidate_paths:
                chain = [source] + [e.target.rsplit('.', 1)[0] for e in cp]
                candidate_strs.append(' -> '.join(chain))
            details = f" at semantic cost {distance[target]}: {'; '.join(candidate_strs)}" if candidate_strs else ""
            raise AmbiguousJoinPathError(
                f"Multiple lowest-cost FK paths between {source} and {target}{details}. "
                "Please specify clarifying join hints or intermediate tables.",
                candidate_paths=[
                    [source, *[edge.target.rsplit('.', 1)[0] for edge in candidate_path]]
                    for candidate_path in candidate_paths
                ],
            )
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

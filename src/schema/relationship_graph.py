"""In-memory foreign-key connectivity for schema candidate reranking."""

from collections import deque

from src.schema.registry import SchemaRegistry


class RelationshipGraph:
    def __init__(self, registry: SchemaRegistry) -> None:
        self.registry = registry
        self.adjacency: dict[str, set[str]] = {name: set() for name in registry.tables}
        for relationship in registry.relationships:
            source, target = relationship.source.rsplit(".", 1)[0], relationship.target.rsplit(".", 1)[0]
            self.adjacency[source].add(target)
            self.adjacency[target].add(source)

    def find_path(self, source: str, target: str) -> list[tuple[str, str]]:
        """Return a deterministic shortest undirected table path, or an empty list."""
        source, target = self.registry.resolve_table(source), self.registry.resolve_table(target)
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

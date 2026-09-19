"""Load and validate the schema specification once, without database access."""

from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from src.shared.models import SchemaColumn, SchemaRelationship, SchemaTable

ROOT = Path(__file__).resolve().parents[2]


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict:
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise ValueError(f"Duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Read safe YAML while rejecting duplicate keys instead of silently replacing them."""
    with Path(path).open(encoding="utf-8-sig") as stream:
        data = yaml.load(stream, Loader=_UniqueLoader)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping: {path}")
    return data


class SchemaRegistry:
    """Canonical identifiers are schema.table and schema.table.column.

    Unqualified references are accepted only when they identify a unique table.
    Construct once at application startup and reuse the registry and linker.
    """

    def __init__(self, path: str | Path = ROOT / "schema/schema_spec.yaml") -> None:
        data = load_yaml(path)
        self.tables: dict[str, SchemaTable] = {}
        self.columns: dict[str, SchemaColumn] = {}
        self._short_names: dict[str, list[str]] = defaultdict(list)
        if not isinstance(data.get("tables"), list) or not data["tables"]:
            raise ValueError("Schema must contain a nonempty tables list")
        for entry in data["tables"]:
            table = SchemaTable.model_validate(entry)
            name = f"{table.schema_name}.{table.name}"
            if name in self.tables:
                raise ValueError(f"Duplicate table: {name}")
            if not table.columns:
                raise ValueError(f"Table has no columns: {name}")
            self.tables[name] = table
            self._short_names[table.name].append(name)
            for column in table.columns:
                key = f"{name}.{column.name}"
                if key in self.columns:
                    raise ValueError(f"Duplicate column: {key}")
                self.columns[key] = column
        self.relationships = [SchemaRelationship.model_validate(r) for r in data.get("relationships", [])]
        for relationship in self.relationships:
            if relationship.source not in self.columns or relationship.target not in self.columns:
                raise ValueError(f"Unknown relationship endpoint: {relationship}")

    def resolve_table(self, name: str) -> str:
        """Resolve a qualified or unique short name, rejecting unknown/ambiguous names."""
        if not isinstance(name, str):
            raise ValueError("Table reference must be a string")
        if name in self.tables:
            return name
        matches = self._short_names.get(name, [])
        if len(matches) != 1:
            raise ValueError(f"Unknown or ambiguous table: {name}")
        return matches[0]

    def resolve_column(self, name: str) -> str:
        if not isinstance(name, str) or "." not in name:
            raise ValueError(f"Column must include its table: {name}")
        table, column = name.rsplit(".", 1)
        key = f"{self.resolve_table(table)}.{column}"
        if key not in self.columns:
            raise ValueError(f"Unknown column: {name}")
        return key

    def get_table(self, name: str) -> SchemaTable | None:
        try:
            return self.tables[self.resolve_table(name)]
        except ValueError:
            return None

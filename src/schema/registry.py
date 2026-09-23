"""Validated in-memory metadata; no database access or permission decisions."""

import re
from pathlib import Path
from typing import Any
import yaml
from collections.abc import Iterable

from src.shared.exceptions import InvalidColumnError, InvalidTableError
from src.shared.models import SchemaColumn, SchemaRelationship, SchemaTable

_IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')


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
    """Inject metadata from the application's schema loader at startup.

    Canonical keys are schema.table and schema.table.column. Short table names
    work only when unique. Defaults to the schema YAML; callers may instead inject tables and relationships.
    """

    def __init__(self, tables: Iterable[SchemaTable] | str | Path | None = None,
                 relationships: Iterable[SchemaRelationship] = (), *,
                 path: str | Path | None = None) -> None:
        if path is not None and tables is not None:
            raise ValueError('Specify tables or path, not both')
        if tables is None or isinstance(tables, (str, Path)):
            data = load_yaml(path or tables or ROOT / 'schema/schema_spec.yaml')
            if not isinstance(data.get('tables'), list) or not data['tables']:
                raise ValueError('Schema must contain a nonempty tables list')
            tables = [SchemaTable.model_validate(t) for t in data['tables']]
            relationships = [SchemaRelationship.model_validate(r) for r in data.get('relationships', [])]
        self.tables: dict[str, SchemaTable] = {}
        self.columns: dict[str, SchemaColumn] = {}
        for original in tables:
            table = SchemaTable.model_validate(original.model_dump())
            if not all(_IDENTIFIER.fullmatch(n) for n in (table.schema_name, table.name)):
                raise InvalidTableError('Invalid metadata table identifier')
            name = f'{table.schema_name}.{table.name}'
            if name in self.tables or not table.columns:
                raise InvalidTableError(f'Duplicate or empty table: {name}')
            self.tables[name] = table
            for column in table.columns:
                key = f'{name}.{column.name}'
                if not _IDENTIFIER.fullmatch(column.name) or key in self.columns:
                    raise InvalidColumnError(f'Invalid or duplicate metadata column: {key}')
                self.columns[key] = column
        self.relationships: list[SchemaRelationship] = []
        seen = set()
        inline = [SchemaRelationship(source=f'{name}.{c.name}', target=c.foreign_key)
                  for name, table in self.tables.items() for c in table.columns if c.foreign_key]
        for relation in [*relationships, *inline]:
            source = self.resolve_column(relation.source)
            target = self.resolve_column(relation.target)
            resolved_composite = [
                (self.resolve_column(s), self.resolve_column(t))
                for s, t in getattr(relation, 'composite_keys', [])
            ]
            key = tuple(sorted((source, target)))
            if key not in seen:
                self.relationships.append(SchemaRelationship(
                    source=source,
                    target=target,
                    composite_keys=resolved_composite,
                    is_outer=getattr(relation, 'is_outer', False),
                    target_role=getattr(relation, 'target_role', None),
                ))
                seen.add(key)

    def resolve_table(self, name: str) -> str:
        if not isinstance(name, str):
            raise InvalidTableError('Table reference must be a string')
        if name in self.tables:
            return name
        matches = [key for key, table in self.tables.items() if table.name == name]
        if len(matches) != 1:
            raise InvalidTableError(f'Unknown or ambiguous table: {name}')
        return matches[0]

    def get_table(self, name: str) -> SchemaTable | None:
        try:
            return self.tables[self.resolve_table(name)]
        except InvalidTableError:
            return None

    def resolve_column(self, name: str, tables: Iterable[str] = ()) -> str:
        """Resolve qualified references or a unique column among context tables."""
        if not isinstance(name, str):
            raise InvalidColumnError('Column reference must be a string')
        if '.' in name:
            table, column = name.rsplit('.', 1)
            try:
                key = f'{self.resolve_table(table)}.{column}'
            except InvalidTableError as exc:
                raise InvalidColumnError(f'Unknown table for column: {name}') from exc
            if key not in self.columns:
                raise InvalidColumnError(f'Unknown column: {name}')
            return key
        matches = [f'{self.resolve_table(t)}.{name}' for t in tables if f'{self.resolve_table(t)}.{name}' in self.columns]
        matches = list(dict.fromkeys(matches))
        if len(matches) != 1:
            raise InvalidColumnError(f'Unknown or ambiguous column: {name}')
        return matches[0]

"""Bootstrap metadata from the bundled DDL; never executes SQL.

This deliberately supports the one-column-per-line format of 01_schema.sql,
not arbitrary PostgreSQL DDL. Review changes before replacing curated metadata.
Run: python -m scripts.build_schema_spec --output schema/schema_spec.yaml
"""

import argparse
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def extract_spec(ddl: str) -> dict:
    """Extract column types, nullability, primary keys and foreign keys."""
    tables, relationships = [], []
    for schema, name, body in re.findall(r"CREATE TABLE (\w+)\.(\w+) \(\n(.*?)\n\);", ddl, re.S):
        columns = []
        for line in body.splitlines():
            match = re.match(r"    (\w+) ([A-Z][A-Z0-9]*(?:\([^)]*\))?(?:\[\])?)(.*)", line)
            if not match or match[1] in {"CHECK", "UNIQUE", "PRIMARY", "FOREIGN", "CONSTRAINT"}:
                continue
            column, data_type, constraints = match.groups()
            columns.append(dict(name=column, data_type=data_type.lower(),
                                nullable="NOT NULL" not in constraints and "PRIMARY KEY" not in constraints,
                                primary_key="PRIMARY KEY" in constraints))
            target = re.search(r"REFERENCES (\w+\.\w+)\((\w+)\)", constraints)
            if target:
                relationships.append(dict(source=f"{schema}.{name}.{column}", target=f"{target[1]}.{target[2]}"))
        if not columns:
            raise ValueError(f"No columns extracted for {schema}.{name}")
        tables.append(dict(name=name, schema_name=schema, columns=columns))
    for table, column, target, target_column in re.findall(
        r"ALTER TABLE (\w+\.\w+) ADD CONSTRAINT \w+ FOREIGN KEY \((\w+)\) REFERENCES (\w+\.\w+)\((\w+)\)", ddl
    ):
        relationships.append(dict(source=f"{table}.{column}", target=f"{target}.{target_column}"))
    if len(tables) != len(re.findall(r"CREATE TABLE\b", ddl)):
        raise ValueError("Some CREATE TABLE statements were not parsed")
    return dict(version=1, tables=tables, relationships=relationships)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spec = extract_spec((ROOT / "dataset_v2/sql/01_schema.sql").read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects subsequently curated source-of-truth metadata.
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write("# Bootstrapped from dataset_v2/sql/01_schema.sql; runtime source of truth.\n")
        yaml.safe_dump(spec, stream, allow_unicode=True, sort_keys=False)
    print(f"Extracted {len(spec['tables'])} tables, {sum(len(t['columns']) for t in spec['tables'])} columns")


if __name__ == "__main__":
    main()

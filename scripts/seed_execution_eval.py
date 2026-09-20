"""Seed the dedicated PostgreSQL ``*_eval`` database used by execution evaluation."""

import argparse
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from src.schema.registry import ROOT, SchemaRegistry


SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS hr;
CREATE SCHEMA IF NOT EXISTS org;
CREATE SCHEMA IF NOT EXISTS customer;
CREATE SCHEMA IF NOT EXISTS sales;
CREATE TABLE IF NOT EXISTS hr.employees (
    id text PRIMARY KEY, tenant_id text NOT NULL, department_id text,
    branch_id text, team_id text, full_name text, base_salary numeric
);
CREATE TABLE IF NOT EXISTS org.branches (
    id text PRIMARY KEY, tenant_id text NOT NULL, name text NOT NULL
);
CREATE TABLE IF NOT EXISTS customer.customers (
    id text PRIMARY KEY, tenant_id text NOT NULL, customer_type text, name text
);
CREATE TABLE IF NOT EXISTS sales.orders (
    id text PRIMARY KEY, tenant_id text NOT NULL, customer_id text,
    branch_id text, order_date date, total_amount numeric
);
"""

DELETE_SQL = """
DELETE FROM sales.orders WHERE id IN ('O1', 'O2', 'O3', 'O4');
DELETE FROM customer.customers WHERE id IN ('C1', 'C2', 'C3');
DELETE FROM org.branches WHERE id IN ('B1', 'B2', 'B3');
DELETE FROM hr.employees WHERE id IN ('E1', 'E2', 'E3', 'E4');
"""

INSERTS = (
    ("INSERT INTO hr.employees VALUES (:id, :tenant, :department, :branch, :team, :name, :salary)", [
        {'id': 'E1', 'tenant': 'T1', 'department': 'D1', 'branch': 'B1', 'team': 'T1', 'name': 'An', 'salary': 100},
        {'id': 'E2', 'tenant': 'T1', 'department': 'D1', 'branch': 'B1', 'team': 'T2', 'name': 'Binh', 'salary': 200},
        {'id': 'E3', 'tenant': 'T1', 'department': 'D2', 'branch': 'B2', 'team': 'T3', 'name': 'Chi', 'salary': 300},
        {'id': 'E4', 'tenant': 'T2', 'department': 'D1', 'branch': 'B1', 'team': 'T1', 'name': 'Other Tenant', 'salary': 400},
    ]),
    ("INSERT INTO org.branches VALUES (:id, :tenant, :name)", [
        {'id': 'B1', 'tenant': 'T1', 'name': 'HCM'}, {'id': 'B2', 'tenant': 'T1', 'name': 'Ha Noi'},
        {'id': 'B3', 'tenant': 'T2', 'name': 'Other Branch'},
    ]),
    ("INSERT INTO customer.customers VALUES (:id, :tenant, :type, :name)", [
        {'id': 'C1', 'tenant': 'T1', 'type': 'VIP', 'name': 'VIP Co'}, {'id': 'C2', 'tenant': 'T1', 'type': 'NORMAL', 'name': 'Normal Co'},
        {'id': 'C3', 'tenant': 'T2', 'type': 'VIP', 'name': 'Other VIP'},
    ]),
    ("INSERT INTO sales.orders VALUES (:id, :tenant, :customer, :branch, :date, :amount)", [
        {'id': 'O1', 'tenant': 'T1', 'customer': 'C1', 'branch': 'B1', 'date': '2026-08-10', 'amount': 100},
        {'id': 'O2', 'tenant': 'T1', 'customer': 'C1', 'branch': 'B1', 'date': '2026-08-20', 'amount': 200},
        {'id': 'O3', 'tenant': 'T1', 'customer': 'C2', 'branch': 'B2', 'date': '2026-08-20', 'amount': 900},
        {'id': 'O4', 'tenant': 'T2', 'customer': 'C3', 'branch': 'B3', 'date': '2026-08-20', 'amount': 700},
    ]),
)


_CORE_COLUMNS = {
    'hr.employees': {'id', 'tenant_id', 'department_id', 'branch_id', 'team_id', 'full_name', 'base_salary'},
    'org.branches': {'id', 'tenant_id', 'code', 'name', 'status'},
    'org.departments': {'id', 'tenant_id', 'branch_id', 'code', 'name', 'status'},
    'customer.customers': {'id', 'tenant_id', 'branch_id', 'code', 'customer_type', 'name', 'status'},
    'product.products': {'id', 'tenant_id', 'code', 'name', 'base_price', 'status'},
    'sales.orders': {'id', 'tenant_id', 'customer_id', 'branch_id', 'order_date', 'status', 'total_amount'},
    'sales.order_items': {'id', 'order_id', 'product_id', 'quantity', 'line_total'},
}


def _quoted(identifier: str) -> str:
    return f'"{identifier}"'


def _column_type(name: str, data_type: str) -> str:
    """Use portable fixture types while retaining numeric/date execution semantics."""
    lowered = data_type.lower()
    if name == 'id' or name.endswith('_id'):
        return 'text'
    if any(token in lowered for token in ('numeric', 'decimal', 'integer', 'bigint', 'smallint', 'real', 'double', 'money')):
        return 'numeric'
    if 'timestamp' in lowered:
        return 'timestamp'
    if lowered == 'date':
        return 'date'
    if 'bool' in lowered:
        return 'boolean'
    return 'text'


def _default_value(table: str, name: str, data_type: str):
    if name == 'id':
        return table.replace('.', '_')
    if name == 'tenant_id':
        return 'T1'
    if name.endswith('_id'):
        return f'{name}_value'
    if name == 'status':
        return 'COMPLETED'
    if name == 'customer_type':
        return 'VIP'
    if name == 'code':
        return 'CODE-1'
    lowered = data_type.lower()
    if any(token in lowered for token in ('numeric', 'decimal', 'integer', 'bigint', 'smallint', 'real', 'double', 'money')):
        return 10
    if 'timestamp' in lowered:
        return datetime(2026, 8, 15, 12, 0, 0)
    if lowered == 'date':
        return date(2026, 8, 15)
    if 'bool' in lowered:
        return True
    return f'{table}.{name}'


def _required_columns(coverage: dict, registry: SchemaRegistry) -> dict[str, set[str]]:
    required: dict[str, set[str]] = defaultdict(set)
    for item in coverage['columns']:
        table, column = registry.resolve_column(item['name']).rsplit('.', 1)
        required[table].add(column)
    for item in coverage['tables']:
        table = registry.resolve_table(item['name'])
        required[table].add('id')
        if f'{table}.tenant_id' in registry.columns:
            required[table].add('tenant_id')
    for table, columns in _CORE_COLUMNS.items():
        required[table].update(columns)
    return required


def seed_coverage(database_url: str | URL, coverage: dict, registry: SchemaRegistry | None = None) -> None:
    """Install one deterministic, join-compatible row per benchmark-required table.

    The fixture contains only report-derived columns plus the legacy core rows
    used by result assertions. It has no production data or foreign-key
    constraints and is guarded to a dedicated ``*_eval`` database.
    """
    url = make_url(database_url)
    if not (url.database or '').endswith('_eval'):
        raise ValueError('Refusing to seed a database whose name does not end in _eval')
    registry = registry or SchemaRegistry()
    required = _required_columns(coverage, registry)
    missing = set(item['name'] for item in coverage['tables']) - set(required)
    if missing:
        raise ValueError(f'Coverage references unknown tables: {sorted(missing)}')
    rows: dict[str, dict[str, object]] = {
        table: {column: _default_value(table, column, registry.columns[f'{table}.{column}'].data_type)
                for column in sorted(columns)}
        for table, columns in required.items()
    }
    for join in coverage['joins']:
        source = registry.resolve_column(join['source'])
        target = registry.resolve_column(join['target'])
        source_table, source_column = source.rsplit('.', 1)
        target_table, target_column = target.rsplit('.', 1)
        if source_table in rows and target_table in rows:
            rows[source_table][source_column] = rows[target_table][target_column]

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            for schema in sorted({table.rsplit('.', 1)[0] for table in required}):
                connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS {_quoted(schema)}')
            for table in sorted(required):
                schema, name = table.rsplit('.', 1)
                connection.exec_driver_sql(f'DROP TABLE IF EXISTS {_quoted(schema)}.{_quoted(name)}')
                definitions = ', '.join(
                    f'{_quoted(column)} {_column_type(column, registry.columns[f"{table}.{column}"].data_type)}'
                    for column in sorted(required[table])
                )
                connection.exec_driver_sql(f'CREATE TABLE {_quoted(schema)}.{_quoted(name)} ({definitions})')
            for table, values in sorted(rows.items()):
                schema, name = table.rsplit('.', 1)
                columns = sorted(values)
                statement = text(
                    f'INSERT INTO {_quoted(schema)}.{_quoted(name)} '
                    f'({", ".join(_quoted(column) for column in columns)}) '
                    f'VALUES ({", ".join(f":{column}" for column in columns)})'
                )
                connection.execute(statement, values)
            _insert_core_rows(connection)
    finally:
        engine.dispose()


def _insert_core_rows(connection) -> None:
    """Replace generic core rows with the fixed values asserted by six eval cases."""
    core_rows = {
        'hr.employees': [
            {'id': 'E1', 'tenant_id': 'T1', 'department_id': 'D1', 'branch_id': 'B1', 'team_id': 'T1', 'full_name': 'An', 'base_salary': 100},
            {'id': 'E2', 'tenant_id': 'T1', 'department_id': 'D1', 'branch_id': 'B1', 'team_id': 'T2', 'full_name': 'Binh', 'base_salary': 200},
            {'id': 'E3', 'tenant_id': 'T1', 'department_id': 'D2', 'branch_id': 'B2', 'team_id': 'T3', 'full_name': 'Chi', 'base_salary': 300},
            {'id': 'E4', 'tenant_id': 'T2', 'department_id': 'D1', 'branch_id': 'B1', 'team_id': 'T1', 'full_name': 'Other Tenant', 'base_salary': 400},
        ],
        'org.branches': [
            {'id': 'B1', 'tenant_id': 'T1', 'code': 'B1', 'name': 'HCM', 'status': 'COMPLETED'},
            {'id': 'B2', 'tenant_id': 'T1', 'code': 'B2', 'name': 'Ha Noi', 'status': 'COMPLETED'},
            {'id': 'B3', 'tenant_id': 'T2', 'code': 'B3', 'name': 'Other Branch', 'status': 'COMPLETED'},
        ],
        'org.departments': [
            {'id': 'D1', 'tenant_id': 'T1', 'branch_id': 'B1', 'code': 'D1', 'name': 'Sales', 'status': 'COMPLETED'},
            {'id': 'D2', 'tenant_id': 'T1', 'branch_id': 'B2', 'code': 'D2', 'name': 'HR', 'status': 'COMPLETED'},
            {'id': 'D3', 'tenant_id': 'T2', 'branch_id': 'B3', 'code': 'D3', 'name': 'Other', 'status': 'COMPLETED'},
        ],
        'customer.customers': [
            {'id': 'C1', 'tenant_id': 'T1', 'branch_id': 'B1', 'code': 'C1', 'customer_type': 'VIP', 'name': 'VIP Co', 'status': 'COMPLETED'},
            {'id': 'C2', 'tenant_id': 'T1', 'branch_id': 'B2', 'code': 'C2', 'customer_type': 'NORMAL', 'name': 'Normal Co', 'status': 'COMPLETED'},
            {'id': 'C3', 'tenant_id': 'T2', 'branch_id': 'B3', 'code': 'C3', 'customer_type': 'VIP', 'name': 'Other VIP', 'status': 'COMPLETED'},
        ],
        'product.products': [
            {'id': 'P1', 'tenant_id': 'T1', 'code': 'P1', 'name': 'Widget A', 'base_price': 50, 'status': 'COMPLETED'},
            {'id': 'P2', 'tenant_id': 'T1', 'code': 'P2', 'name': 'Widget B', 'base_price': 900, 'status': 'COMPLETED'},
            {'id': 'P3', 'tenant_id': 'T2', 'code': 'P3', 'name': 'Other Widget', 'base_price': 100, 'status': 'COMPLETED'},
        ],
        'sales.orders': [
            {'id': 'O1', 'tenant_id': 'T1', 'customer_id': 'C1', 'branch_id': 'B1', 'order_date': date(2026, 8, 10), 'status': 'COMPLETED', 'total_amount': 100},
            {'id': 'O2', 'tenant_id': 'T1', 'customer_id': 'C1', 'branch_id': 'B1', 'order_date': date(2026, 8, 20), 'status': 'COMPLETED', 'total_amount': 200},
            {'id': 'O3', 'tenant_id': 'T1', 'customer_id': 'C2', 'branch_id': 'B2', 'order_date': date(2026, 8, 20), 'status': 'PENDING', 'total_amount': 900},
            {'id': 'O4', 'tenant_id': 'T2', 'customer_id': 'C3', 'branch_id': 'B3', 'order_date': date(2026, 8, 20), 'status': 'COMPLETED', 'total_amount': 700},
        ],
        'sales.order_items': [
            {'id': 'I1', 'order_id': 'O1', 'product_id': 'P1', 'quantity': 2, 'line_total': 100},
            {'id': 'I2', 'order_id': 'O2', 'product_id': 'P1', 'quantity': 3, 'line_total': 200},
            {'id': 'I3', 'order_id': 'O3', 'product_id': 'P2', 'quantity': 1, 'line_total': 900},
            {'id': 'I4', 'order_id': 'O4', 'product_id': 'P3', 'quantity': 7, 'line_total': 700},
        ],
    }
    for table, values in core_rows.items():
        schema, name = table.rsplit('.', 1)
        connection.execute(text(f'DELETE FROM {_quoted(schema)}.{_quoted(name)}'))
        columns = sorted(values[0])
        statement = text(
            f'INSERT INTO {_quoted(schema)}.{_quoted(name)} '
            f'({", ".join(_quoted(column) for column in columns)}) '
            f'VALUES ({", ".join(f":{column}" for column in columns)})'
        )
        connection.execute(statement, values)


def seed(database_url: str | URL, coverage: dict | None = None) -> None:
    """Create only the small deterministic fixture in a dedicated eval database."""
    if coverage is not None:
        seed_coverage(database_url, coverage)
        return
    url = make_url(database_url)
    if not (url.database or '').endswith('_eval'):
        raise ValueError('Refusing to seed a database whose name does not end in _eval')
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            for statement in SCHEMA_SQL.split(';'):
                if statement.strip():
                    connection.exec_driver_sql(statement)
            for statement in DELETE_SQL.split(';'):
                if statement.strip():
                    connection.exec_driver_sql(statement)
            for statement, values in INSERTS:
                connection.execute(text(statement), values)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True, help='Dedicated PostgreSQL database ending in _eval')
    parser.add_argument('--coverage-report', type=Path, help='Report produced by evaluation/analyze_execution_coverage.py')
    args = parser.parse_args()
    coverage = json.loads(args.coverage_report.read_text(encoding='utf-8')) if args.coverage_report else None
    seed(args.database_url, coverage)
    print('Execution evaluation seed installed.')


if __name__ == '__main__':
    main()

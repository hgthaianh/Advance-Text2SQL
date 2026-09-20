"""Seed the dedicated PostgreSQL ``*_eval`` database used by execution evaluation."""

import argparse

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


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


def seed(database_url: str | URL) -> None:
    """Create only the small deterministic fixture in a dedicated eval database."""
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
    args = parser.parse_args()
    seed(args.database_url)
    print('Execution evaluation seed installed.')


if __name__ == '__main__':
    main()

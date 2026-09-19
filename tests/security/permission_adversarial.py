"""Adversarial security test suite for permission robustness, scope leakage, and SQL injection prevention.

Target: Ensure 0% permission violation rate across adversarial test cases.
"""

import pytest

from src.permission.column_policy import DefaultColumnPolicy
from src.permission.models import PermissionContext
from src.permission.resolver import DefaultPermissionResolver
from src.permission.row_policy import ScopedRowPolicy
from src.query.handlers.basic import BasicQueryHandler
from src.query.ir import OrderBy, QueryFilter, QueryIR, SelectItem
from src.query.planner import FKQueryPlanner
from src.schema.registry import SchemaRegistry
from src.shared.enums import Aggregation, FilterOperator, Intent, PermissionScope
from src.shared.exceptions import PermissionDeniedError
from src.shared.models import SchemaColumn, SchemaTable, UserContext


@pytest.fixture
def enterprise_registry() -> SchemaRegistry:
    """Fixture providing a multi-tenant enterprise schema with sensitive columns."""
    return SchemaRegistry(
        tables=[
            SchemaTable(
                name="employees",
                schema_name="hr",
                columns=[
                    SchemaColumn(name="id", data_type="integer", primary_key=True),
                    SchemaColumn(name="name", data_type="text"),
                    SchemaColumn(name="department_id", data_type="text"),
                    SchemaColumn(name="branch_id", data_type="text"),
                    SchemaColumn(name="team_id", data_type="text"),
                    SchemaColumn(name="employee_id", data_type="text"),
                    SchemaColumn(name="salary", data_type="numeric"),
                    SchemaColumn(name="bank_account", data_type="text"),
                    SchemaColumn(name="ssn", data_type="text"),
                    SchemaColumn(name="tax_id", data_type="text"),
                ],
            ),
            SchemaTable(
                name="payroll_details",
                schema_name="hr",
                columns=[
                    SchemaColumn(name="id", data_type="integer", primary_key=True),
                    SchemaColumn(name="employee_id", data_type="text"),
                    SchemaColumn(name="net_salary", data_type="numeric"),
                ],
            ),
        ]
    )


# -----------------------------------------------------------------------------
# 1. COLUMN PERMISSION ADVERSARIAL TESTS (SELECT, FILTER, GROUP BY, ORDER BY)
# -----------------------------------------------------------------------------

def test_adversarial_select_sensitive_column_blocked():
    """Verify unauthorized access to salary in SELECT is blocked deterministically."""
    policy = DefaultColumnPolicy()
    query = QueryIR(
        intent=Intent.LOOKUP,
        tables=["employees"],
        select=["employees.name", "employees.salary"],
    )
    permissions = PermissionContext(allowed_tables=["*"], allowed_columns={"*": ["id", "name"]})

    with pytest.raises(PermissionDeniedError, match="Unauthorized access to sensitive column 'salary'"):
        policy.validate(query, permissions)


def test_adversarial_filter_sensitive_column_blocked():
    """Verify unauthorized access to bank_account in FILTER is blocked deterministically."""
    policy = DefaultColumnPolicy()
    query = QueryIR(
        intent=Intent.LOOKUP,
        tables=["employees"],
        select=["employees.id"],
        filters=[QueryFilter(column="employees.bank_account", operator=FilterOperator.EQ, value="12345")],
    )
    permissions = PermissionContext(allowed_tables=["*"], allowed_columns={"*": ["id", "name"]})

    with pytest.raises(PermissionDeniedError, match="Unauthorized access to sensitive column 'bank_account'"):
        policy.validate(query, permissions)


def test_adversarial_group_by_sensitive_column_blocked():
    """Verify unauthorized access to tax_id in GROUP BY is blocked deterministically."""
    policy = DefaultColumnPolicy()
    query = QueryIR(
        intent=Intent.COUNT,
        tables=["employees"],
        select=[SelectItem(column="*", aggregation=Aggregation.COUNT)],
        group_by=["employees.tax_id"],
    )
    permissions = PermissionContext(allowed_tables=["*"], allowed_columns={"*": ["id", "name"]})

    with pytest.raises(PermissionDeniedError, match="Unauthorized access to sensitive column 'tax_id'"):
        policy.validate(query, permissions)


def test_adversarial_order_by_sensitive_column_blocked():
    """Verify unauthorized access to ssn in ORDER BY is blocked deterministically."""
    policy = DefaultColumnPolicy()
    query = QueryIR(
        intent=Intent.LOOKUP,
        tables=["employees"],
        select=["employees.id"],
        order_by=[OrderBy(column="employees.ssn")],
    )
    permissions = PermissionContext(allowed_tables=["*"], allowed_columns={"*": ["id", "name"]})

    with pytest.raises(PermissionDeniedError, match="Unauthorized access to sensitive column 'ssn'"):
        policy.validate(query, permissions)


def test_adversarial_explicit_denied_column_blocked():
    """Verify explicitly forbidden columns in denied_columns are blocked."""
    policy = DefaultColumnPolicy()
    query = QueryIR(
        intent=Intent.LOOKUP,
        tables=["employees"],
        select=["employees.name"],
    )
    permissions = PermissionContext(
        allowed_tables=["*"],
        allowed_columns={"*": ["*"]},
        denied_columns=["name"],
    )

    with pytest.raises(PermissionDeniedError, match="Access denied to explicitly forbidden column 'name'"):
        policy.validate(query, permissions)


# -----------------------------------------------------------------------------
# 2. ROW-SCOPE ISOLATION & LEAKAGE PREVENTION TESTS
# -----------------------------------------------------------------------------

def test_row_scope_isolation_hr_vs_economics(enterprise_registry: SchemaRegistry):
    """Verify department scope injection isolates HR vs Economics data without leakage."""
    handler = BasicQueryHandler(enterprise_registry)
    row_policy = ScopedRowPolicy()
    resolver = DefaultPermissionResolver()

    base_query = QueryIR(
        intent=Intent.COUNT,
        tables=["employees"],
        select=[SelectItem(column="*", aggregation=Aggregation.COUNT)],
    )

    # User 1: HR Department
    user_hr = UserContext(user_id="emp_hr", role="department_manager", department_id="HR")
    perm_hr = resolver.resolve(user_hr)
    scoped_hr = row_policy.apply(base_query, perm_hr)
    compiled_hr = handler.handle(scoped_hr)

    assert 'WHERE "hr"."employees"."department_id" = :p0' in compiled_hr.sql
    assert compiled_hr.params == {"p0": "HR"}

    # User 2: Economics Department
    user_econ = UserContext(user_id="emp_econ", role="department_manager", department_id="Economics")
    perm_econ = resolver.resolve(user_econ)
    scoped_econ = row_policy.apply(base_query, perm_econ)
    compiled_econ = handler.handle(scoped_econ)

    assert 'WHERE "hr"."employees"."department_id" = :p0' in compiled_econ.sql
    assert compiled_econ.params == {"p0": "Economics"}


def test_row_scope_isolation_branch_team_self(enterprise_registry: SchemaRegistry):
    """Verify BRANCH, TEAM, and SELF scope filters are injected correctly."""
    row_policy = ScopedRowPolicy()
    handler = BasicQueryHandler(enterprise_registry)

    base_query = QueryIR(intent=Intent.COUNT, tables=["employees"])

    # Branch Scope
    user_branch = UserContext(user_id="mgr_1", role="branch_manager", branch_id="BRANCH_01")
    perm_b = PermissionContext(scope=PermissionScope.BRANCH, row_filters=row_policy.create_scope_filters(user_branch, PermissionScope.BRANCH))
    compiled_b = handler.handle(row_policy.apply(base_query, perm_b))
    assert compiled_b.params == {"p0": "BRANCH_01"}

    # Team Scope
    user_team = UserContext(user_id="lead_1", role="team_lead", team_id="TEAM_ALPHA")
    perm_t = PermissionContext(scope=PermissionScope.TEAM, row_filters=row_policy.create_scope_filters(user_team, PermissionScope.TEAM))
    compiled_t = handler.handle(row_policy.apply(base_query, perm_t))
    assert compiled_t.params == {"p0": "TEAM_ALPHA"}

    # Self Scope
    user_self = UserContext(user_id="EMP_777", role="employee")
    perm_s = PermissionContext(scope=PermissionScope.SELF, row_filters=row_policy.create_scope_filters(user_self, PermissionScope.SELF))
    compiled_s = handler.handle(row_policy.apply(base_query, perm_s))
    assert compiled_s.params == {"p0": "EMP_777"}


# -----------------------------------------------------------------------------
# 3. SQL INJECTION & ADVERSARIAL FILTER PAYLOAD PREVENTION
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("injection_payload", [
    "' OR '1'='1",
    "'; DROP TABLE employees; --",
    "' UNION SELECT salary FROM hr.employees --",
    "1; EXEC xp_cmdshell('dir') --",
])
def test_adversarial_sql_injection_payloads_are_parameterized(enterprise_registry: SchemaRegistry, injection_payload: str):
    """Verify that malicious SQL injection strings in filter values are strictly parameterized."""
    handler = BasicQueryHandler(enterprise_registry)
    query = QueryIR(
        intent=Intent.FILTER,
        tables=["employees"],
        select=["employees.name"],
        filters=[QueryFilter(column="employees.name", operator=FilterOperator.EQ, value=injection_payload)],
    )

    compiled = handler.handle(query)
    # The SQL MUST contain parameter placeholder :p0 and NOT raw SQL injection payload
    assert injection_payload not in compiled.sql
    assert ":p0" in compiled.sql
    assert compiled.params["p0"] == injection_payload


# -----------------------------------------------------------------------------
# 4. FORGED PLAN & UNAUTHORIZED TABLE ATTACK PREVENTION
# -----------------------------------------------------------------------------

def test_adversarial_unauthorized_table_access_blocked():
    """Verify accessing unauthorized tables (e.g. payroll_details) raises PermissionDeniedError."""
    planner = FKQueryPlanner()
    query = QueryIR(
        intent=Intent.LOOKUP,
        tables=["payroll_details"],
        select=["payroll_details.net_salary"],
    )
    permissions = PermissionContext(allowed_tables=["employees"])

    with pytest.raises(PermissionDeniedError, match="Access denied to table 'payroll_details'"):
        planner.plan(query, permissions)


def test_adversarial_empty_allowlist_blocks_all_access():
    """Verify empty allowlist denies all table access."""
    planner = FKQueryPlanner()
    query = QueryIR(intent=Intent.LOOKUP, tables=["employees"])
    permissions = PermissionContext(allowed_tables=[])

    with pytest.raises(PermissionDeniedError, match="Empty allowlist denies table access"):
        planner.plan(query, permissions)


# -----------------------------------------------------------------------------
# 5. AUTHORIZED LEGITIMATE QUERIES PASS CLEANLY
# -----------------------------------------------------------------------------

def test_authorized_queries_pass_without_false_positives(enterprise_registry: SchemaRegistry):
    """Verify legitimate authorized queries pass security checks without false positives."""
    policy = DefaultColumnPolicy()
    handler = BasicQueryHandler(enterprise_registry)

    # HR User accessing salary with explicit permission
    user_hr = UserContext(user_id="hr_head", role="hr_manager", department_id="HR")
    resolver = DefaultPermissionResolver()
    perm_hr = resolver.resolve(user_hr)

    query = QueryIR(
        intent=Intent.LOOKUP,
        tables=["employees"],
        select=["employees.id", "employees.name", "employees.salary"],
    )

    # Column policy validation
    policy.validate(query, perm_hr)

    # Execution compilation
    compiled = handler.handle(query)
    assert 'SELECT "hr"."employees"."id"' in compiled.sql
    assert 'SELECT "hr"."employees"."salary"' in compiled.sql or '"hr"."employees"."salary"' in compiled.sql

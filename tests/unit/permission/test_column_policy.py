import pytest

from src.permission.column_policy import DefaultColumnPolicy
from src.permission.models import PermissionContext
from src.query.ir import OrderBy, QueryFilter, QueryIR, SelectItem
from src.shared.enums import Intent
from src.shared.exceptions import PermissionDeniedError


def test_sensitive_salary_column_blocked_without_permission():
    policy = DefaultColumnPolicy()
    query = QueryIR(
        intent=Intent.LOOKUP,
        tables=["employees"],
        select=["employees.id", "employees.salary"],
    )
    permissions = PermissionContext(allowed_tables=["*"], allowed_columns={"*": ["id", "name"]})

    with pytest.raises(PermissionDeniedError, match="Unauthorized access to sensitive column 'salary'"):
        policy.validate(query, permissions)


def test_sensitive_bank_account_blocked_in_filters():
    policy = DefaultColumnPolicy()
    query = QueryIR(
        intent=Intent.LOOKUP,
        tables=["employees"],
        select=["employees.id"],
        filters=[QueryFilter(column="employees.bank_account", value="123456789")],
    )
    permissions = PermissionContext(allowed_tables=["*"])

    with pytest.raises(PermissionDeniedError, match="Unauthorized access to sensitive column 'bank_account'"):
        policy.validate(query, permissions)


def test_sensitive_ssn_blocked_in_order_by():
    policy = DefaultColumnPolicy()
    query = QueryIR(
        intent=Intent.LOOKUP,
        tables=["employees"],
        select=["employees.id"],
        order_by=[OrderBy(column="employees.ssn")],
    )
    permissions = PermissionContext(allowed_tables=["*"])

    with pytest.raises(PermissionDeniedError, match="Unauthorized access to sensitive column 'ssn'"):
        policy.validate(query, permissions)


def test_explicitly_denied_column_blocked():
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


def test_authorized_sensitive_column_passes():
    policy = DefaultColumnPolicy()
    query = QueryIR(
        intent=Intent.LOOKUP,
        tables=["employees"],
        select=[SelectItem(column="employees.salary")],
    )
    permissions = PermissionContext(
        allowed_tables=["*"],
        allowed_columns={"employees": ["id", "name", "salary"]},
    )

    # Should pass without exception
    policy.validate(query, permissions)


def test_wildcard_allowed_columns_passes():
    policy = DefaultColumnPolicy()
    query = QueryIR(
        intent=Intent.LOOKUP,
        tables=["employees"],
        select=["employees.id", "employees.name"],
    )
    permissions = PermissionContext(
        allowed_tables=["*"],
        allowed_columns={"*": ["*"]},
    )

    policy.validate(query, permissions)

import pytest

from src.permission.models import PermissionContext
from src.permission.resolver import DefaultPermissionResolver
from src.permission.row_policy import ScopedRowPolicy
from src.query.handlers.basic import BasicQueryHandler
from src.query.ir import QueryFilter, QueryIR, SelectItem
from src.schema.registry import SchemaRegistry
from src.shared.enums import Aggregation, FilterOperator, Intent, PermissionScope
from src.shared.models import SchemaColumn, SchemaTable, UserContext


@pytest.fixture
def company_registry() -> SchemaRegistry:
    return SchemaRegistry(
        tables=[
            SchemaTable(
                name="employees",
                schema_name="public",
                columns=[
                    SchemaColumn(name="id", data_type="integer", primary_key=True),
                    SchemaColumn(name="name", data_type="text"),
                    SchemaColumn(name="department_id", data_type="text"),
                    SchemaColumn(name="branch_id", data_type="text"),
                    SchemaColumn(name="team_id", data_type="text"),
                    SchemaColumn(name="employee_id", data_type="text"),
                ],
            )
        ]
    )


def test_row_policy_global_scope():
    policy = ScopedRowPolicy()
    query = QueryIR(intent=Intent.COUNT, tables=["employees"])
    permissions = PermissionContext(scope=PermissionScope.GLOBAL, row_filters=[])

    scoped_query = policy.apply(query, permissions)

    assert len(scoped_query.filters) == 0


def test_row_policy_department_scope_injection():
    policy = ScopedRowPolicy()
    query = QueryIR(intent=Intent.COUNT, tables=["employees"])

    user_hr = UserContext(user_id="u_hr", role="department_manager", department_id="HR")
    filters_hr = ScopedRowPolicy.create_scope_filters(user_hr, PermissionScope.DEPARTMENT)
    permissions_hr = PermissionContext(scope=PermissionScope.DEPARTMENT, row_filters=filters_hr)

    scoped_query = policy.apply(query, permissions_hr)

    assert len(scoped_query.filters) == 1
    assert scoped_query.filters[0].column == "department_id"
    assert scoped_query.filters[0].value == "HR"


def test_row_policy_hr_vs_economics_count_compilation(company_registry: SchemaRegistry):
    handler = BasicQueryHandler(company_registry)
    policy = ScopedRowPolicy()
    resolver = DefaultPermissionResolver()

    base_query = QueryIR(
        intent=Intent.COUNT,
        tables=["employees"],
        select=[SelectItem(column="*", aggregation=Aggregation.COUNT)],
    )

    # 1. Login scope: HR Manager (department_id = HR)
    user_hr = UserContext(user_id="user_1", role="department_manager", department_id="HR")
    perm_hr = resolver.resolve(user_hr)
    query_hr = policy.apply(base_query, perm_hr)

    compiled_hr = handler.handle(query_hr)
    assert 'WHERE "public"."employees"."department_id" = :p0' in compiled_hr.sql
    assert compiled_hr.params == {"p0": "HR"}

    # 2. Login scope: Economics Manager (department_id = Economics)
    user_econ = UserContext(user_id="user_2", role="department_manager", department_id="Economics")
    perm_econ = resolver.resolve(user_econ)
    query_econ = policy.apply(base_query, perm_econ)

    compiled_econ = handler.handle(query_econ)
    assert 'WHERE "public"."employees"."department_id" = :p0' in compiled_econ.sql
    assert compiled_econ.params == {"p0": "Economics"}


def test_row_policy_branch_team_self_scopes():
    user_branch = UserContext(user_id="u1", role="branch_manager", branch_id="B01")
    filters_b = ScopedRowPolicy.create_scope_filters(user_branch, PermissionScope.BRANCH)
    assert filters_b[0].column == "branch_id"
    assert filters_b[0].value == "B01"

    user_team = UserContext(user_id="u2", role="team_lead", team_id="T05")
    filters_t = ScopedRowPolicy.create_scope_filters(user_team, PermissionScope.TEAM)
    assert filters_t[0].column == "team_id"
    assert filters_t[0].value == "T05"

    user_self = UserContext(user_id="u3", role="employee")
    filters_s = ScopedRowPolicy.create_scope_filters(user_self, PermissionScope.SELF)
    assert filters_s[0].column == "employee_id"
    assert filters_s[0].value == "u3"

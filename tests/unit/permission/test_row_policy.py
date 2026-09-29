import pytest

from src.auth.provider import _role_from_code
from src.permission.models import PermissionContext
from src.permission.resolver import DefaultPermissionResolver
from src.permission.row_policy import ScopedRowPolicy
from src.query.handlers.basic import BasicQueryHandler
from src.query.ir import QueryFilter, QueryIR, SelectItem
from src.schema.registry import SchemaRegistry
from src.shared.enums import Aggregation, FilterOperator, Intent, PermissionScope
from src.shared.exceptions import PermissionDeniedError
from src.shared.models import SchemaColumn, SchemaRelationship, SchemaTable, UserContext


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


def test_registry_aware_self_scope_uses_employee_primary_key():
    registry = SchemaRegistry(tables=[SchemaTable(
        name='employees', schema_name='hr',
        columns=[SchemaColumn(name='id', data_type='text', primary_key=True)],
    )])
    resolver = DefaultPermissionResolver(registry)

    permissions = resolver.resolve(UserContext(user_id='login-1', employee_id='E1', role='employee'))

    assert permissions.row_filters[0].column == 'hr.employees.id'
    assert permissions.row_filters[0].value == 'E1'
    assert permissions.row_filters[0].source == 'permission'


@pytest.mark.parametrize('role, identity', [
    ('branch_manager', {}),
    ('department_manager', {}),
    ('team_lead', {}),
    ('employee', {}),
    ('unknown_role', {}),
])
def test_resolver_denies_missing_scoped_identity_and_unknown_roles(role, identity):
    resolver = DefaultPermissionResolver(SchemaRegistry(), require_tenant=True)

    with pytest.raises(PermissionDeniedError):
        resolver.resolve(UserContext(user_id='u1', role=role, tenant_id='T1', **identity))


def test_department_scope_reaches_related_employee_table_and_tenant_filters_path():
    registry = SchemaRegistry(
        tables=[
            SchemaTable(name='orders', schema_name='sales', columns=[
                SchemaColumn(name='id', data_type='integer', primary_key=True),
                SchemaColumn(name='tenant_id', data_type='text'),
                SchemaColumn(name='sales_rep_id', data_type='integer'),
            ]),
            SchemaTable(name='sales_reps', schema_name='sales', columns=[
                SchemaColumn(name='id', data_type='integer', primary_key=True),
                SchemaColumn(name='tenant_id', data_type='text'),
                SchemaColumn(name='employee_id', data_type='integer'),
            ]),
            SchemaTable(name='employees', schema_name='hr', columns=[
                SchemaColumn(name='id', data_type='integer', primary_key=True),
                SchemaColumn(name='tenant_id', data_type='text'),
                SchemaColumn(name='department_id', data_type='text'),
            ]),
        ],
        relationships=[
            SchemaRelationship(source='sales.orders.sales_rep_id', target='sales.sales_reps.id'),
            SchemaRelationship(source='sales.sales_reps.employee_id', target='hr.employees.id'),
        ],
    )
    resolver = DefaultPermissionResolver(registry, require_tenant=True)
    policy = ScopedRowPolicy(registry)
    query = QueryIR(intent=Intent.COUNT, tables=['sales.orders'])
    permissions = resolver.resolve(UserContext(
        user_id='manager', role='department_manager', department_id='D1', tenant_id='T1',
    ))

    first = policy.apply(query, permissions)
    assert {item.column for item in first.filters} == {'hr.employees.department_id', 'sales.orders.tenant_id'}

    plan = BasicQueryHandler(registry).planner.plan(first)
    final = policy.apply(first, permissions, tables=plan.tables)
    assert {item.column for item in final.filters} == {
        'hr.employees.department_id', 'sales.orders.tenant_id',
        'sales.sales_reps.tenant_id', 'hr.employees.tenant_id',
    }


def test_outer_join_permission_scope_is_compiled_in_on_clause():
    registry = SchemaRegistry(
        tables=[
            SchemaTable(name='customers', schema_name='sales', columns=[
                SchemaColumn(name='id', data_type='integer', primary_key=True),
                SchemaColumn(name='tenant_id', data_type='text'),
                SchemaColumn(name='name', data_type='text'),
            ]),
            SchemaTable(name='orders', schema_name='sales', columns=[
                SchemaColumn(name='id', data_type='integer', primary_key=True),
                SchemaColumn(name='tenant_id', data_type='text'),
                SchemaColumn(name='customer_id', data_type='integer'),
            ]),
        ],
        relationships=[SchemaRelationship(
            source='sales.orders.customer_id', target='sales.customers.id',
        )],
    )
    query = QueryIR(
        intent=Intent.LOOKUP,
        tables=['sales.customers', 'sales.orders'],
        select=['sales.customers.name'],
        outer_joins=['sales.orders'],
    )
    scoped = ScopedRowPolicy(registry).apply(query, PermissionContext(tenant_id='T1'))

    assert [filter_.column for filter_ in scoped.filters] == ['sales.customers.tenant_id']
    assert [filter_.column for filter_ in scoped.outer_join_filters['sales.orders']] == ['sales.orders.tenant_id']
    compiled = BasicQueryHandler(registry).handle(scoped)
    assert 'LEFT OUTER JOIN "sales"."orders" ON "sales"."customers"."id" = "sales"."orders"."customer_id" AND "sales"."orders"."tenant_id" = :p0' in compiled.sql
    assert 'WHERE "sales"."customers"."tenant_id" = :p1' in compiled.sql


def test_seed_roles_keep_their_domain_identity_and_table_policy():
    registry = SchemaRegistry()

    assert _role_from_code('HR_T1') == 'hr_manager'
    assert _role_from_code('FINANCE_T1') == 'finance_manager'
    assert _role_from_code('SALES_T1') == 'sales_manager'
    assert _role_from_code('SUPPORT_T1') == 'support_manager'

    permissions = DefaultPermissionResolver(registry).resolve(UserContext(
        user_id='support-user', role='support_manager', tenant_id='1', department_id='6',
    ))
    assert 'support.support_tickets' in permissions.allowed_tables
    assert 'sales.orders' not in permissions.allowed_tables
    assert 'base_salary' in permissions.denied_columns

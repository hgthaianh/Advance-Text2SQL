"""Phase-2B tests for permission state of the fixed employee-manager join."""

import pytest
from pydantic import ValidationError

from src.permission.column_policy import DefaultColumnPolicy
from src.permission.models import PermissionContext
from src.permission.row_policy import ScopedRowPolicy
from src.query.ir import ColumnRef, InstanceJoinRequest, InstancePermissionFilter, QueryFilter, QueryIR, TableInstance
from src.query.planner import QueryPlanner
from src.schema.registry import SchemaRegistry
from src.shared.enums import FilterOperator, Intent, PermissionScope
from src.shared.exceptions import PermissionDeniedError
from src.shared.models import SchemaColumn, SchemaRelationship, SchemaTable


@pytest.fixture
def employee_registry() -> SchemaRegistry:
    return SchemaRegistry(
        tables=[SchemaTable(name='employees', schema_name='hr', columns=[
            SchemaColumn(name='id', data_type='text', primary_key=True),
            SchemaColumn(name='full_name', data_type='text'),
            SchemaColumn(name='manager_id', data_type='text'),
            SchemaColumn(name='tenant_id', data_type='text'),
            SchemaColumn(name='branch_id', data_type='text'),
            SchemaColumn(name='department_id', data_type='text'),
            SchemaColumn(name='team_id', data_type='text'),
            SchemaColumn(name='base_salary', data_type='numeric'),
        ])],
        relationships=[SchemaRelationship(
            source='hr.employees.manager_id', target='hr.employees.id',
        )],
    )


def employee_manager_query() -> QueryIR:
    return QueryIR(
        intent=Intent.LOOKUP,
        tables=['hr.employees'],
        select=['hr.employees.full_name'],
        table_instances=[
            TableInstance(instance_id='employee_instance', semantic_role='employee'),
            TableInstance(instance_id='manager_instance', semantic_role='manager'),
        ],
        instance_joins=[InstanceJoinRequest(
            source_instance_id='employee_instance', source_column='manager_id',
            target_instance_id='manager_instance', target_column='id',
        )],
    )


def scoped_permissions(column: str, value: str, *, tenant: str = 'T1') -> PermissionContext:
    return PermissionContext(
        scope=PermissionScope.DEPARTMENT,
        tenant_id=tenant,
        row_filters=[QueryFilter(
            column=column, operator=FilterOperator.EQ, value=value, source='permission',
        )],
    )


def test_employee_base_and_manager_join_permissions_are_distinct(employee_registry):
    scoped = ScopedRowPolicy(employee_registry).apply(
        employee_manager_query(), scoped_permissions('department_id', 'D1'),
    )

    assert [(f.instance_id, f.column, f.value) for f in scoped.instance_base_filters] == [
        ('employee_instance', 'department_id', 'D1'),
        ('employee_instance', 'tenant_id', 'T1'),
    ]
    assert [(f.instance_id, f.column, f.value) for f in scoped.instance_join_filters['manager_instance']] == [
        ('manager_instance', 'department_id', 'D1'),
        ('manager_instance', 'tenant_id', 'T1'),
    ]
    assert not [f for f in scoped.filters if f.source == 'permission']

    plan = QueryPlanner(employee_registry).plan(scoped)
    assert plan.instance_base_filters == scoped.instance_base_filters
    assert plan.instance_join_filters == scoped.instance_join_filters
    assert all(f.instance_id == 'employee_instance' for f in plan.instance_base_filters)
    assert all(f.instance_id == 'manager_instance' for f in plan.instance_join_filters['manager_instance'])


@pytest.mark.parametrize('scope_column, value', [
    ('branch_id', 'B1'), ('department_id', 'D1'), ('team_id', 'TEAM1'),
])
def test_scope_rules_are_expanded_independently_for_both_instances(employee_registry, scope_column, value):
    scoped = ScopedRowPolicy(employee_registry).apply(
        employee_manager_query(), scoped_permissions(scope_column, value),
    )
    assert {(f.instance_id, f.column, f.value) for f in scoped.instance_base_filters} >= {
        ('employee_instance', scope_column, value),
    }
    assert {(f.instance_id, f.column, f.value) for f in scoped.instance_join_filters['manager_instance']} >= {
        ('manager_instance', scope_column, value),
    }


def test_self_scope_is_fail_closed_on_both_employee_rows(employee_registry):
    permissions = PermissionContext(
        scope=PermissionScope.SELF,
        tenant_id='T1',
        row_filters=[QueryFilter(
            column='hr.employees.id', operator=FilterOperator.EQ, value='E1', source='permission',
        )],
    )
    scoped = ScopedRowPolicy(employee_registry).apply(employee_manager_query(), permissions)

    assert {(f.instance_id, f.column, f.value) for f in scoped.instance_base_filters} >= {
        ('employee_instance', 'id', 'E1'),
    }
    # A manager is also an employee row. Applying the same existing physical
    # self policy to that independent instance prevents manager-side leakage.
    assert {(f.instance_id, f.column, f.value) for f in scoped.instance_join_filters['manager_instance']} >= {
        ('manager_instance', 'id', 'E1'),
    }


def test_instance_column_permission_maps_back_to_physical_employee_schema(employee_registry):
    query = employee_manager_query()
    policy = DefaultColumnPolicy()
    denied = PermissionContext(allowed_columns={'hr.employees': ['full_name']})

    with pytest.raises(PermissionDeniedError):
        policy.validate_instance_references(
            query, [ColumnRef(instance_id='manager_instance', column='base_salary')], denied, employee_registry,
        )
    policy.validate_instance_references(
        query, [ColumnRef(instance_id='manager_instance', column='full_name')], denied, employee_registry,
    )


def test_public_column_policy_maps_manager_projection_to_physical_table(employee_registry):
    query = employee_manager_query().model_copy(update={'select': [
        ColumnRef(instance_id='manager_instance', column='base_salary'),
    ]})
    with pytest.raises(PermissionDeniedError):
        DefaultColumnPolicy().validate(query, PermissionContext(
            allowed_columns={'hr.employees': ['full_name']},
        ))


def test_instance_policy_fails_closed_when_scope_column_is_not_on_employees(employee_registry):
    permissions = scoped_permissions('customer_id', 'C1')
    with pytest.raises(PermissionDeniedError):
        ScopedRowPolicy(employee_registry).apply(employee_manager_query(), permissions)


@pytest.mark.parametrize('extra', [
    {'physical_table': 'sales.orders'}, {'sql_alias': 'manager'},
])
def test_instance_permission_filter_rejects_client_table_and_alias_injection(extra):
    with pytest.raises(ValidationError):
        InstancePermissionFilter.model_validate({
            'instance_id': 'manager_instance', 'column': 'tenant_id', 'value': 'T1', **extra,
        })

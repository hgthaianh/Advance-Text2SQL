"""Phase-2C compilation and NULL-semantics tests for employee-manager join."""

from sqlalchemy import create_engine
import pytest

from src.permission.models import PermissionContext
from src.permission.row_policy import ScopedRowPolicy
from src.query.ir import ColumnRef, InstanceJoinRequest, QueryFilter, QueryIR, TableInstance
from src.query.planner import QueryPlanner
from src.query.sql_builder import SQLBuilder
from src.schema.registry import SchemaRegistry
from src.shared.enums import FilterOperator, Intent, PermissionScope
from src.shared.models import SchemaColumn, SchemaRelationship, SchemaTable
from src.shared.exceptions import MalformedQueryIRError


def registry() -> SchemaRegistry:
    return SchemaRegistry(
        tables=[SchemaTable(name='employees', schema_name='hr', columns=[
            SchemaColumn(name='id', data_type='text', primary_key=True),
            SchemaColumn(name='full_name', data_type='text'),
            SchemaColumn(name='manager_id', data_type='text'),
            SchemaColumn(name='tenant_id', data_type='text'),
            SchemaColumn(name='department_id', data_type='text'),
        ])],
        relationships=[SchemaRelationship(source='hr.employees.manager_id', target='hr.employees.id')],
    )


def query(*, filters=None) -> QueryIR:
    return QueryIR(
        intent=Intent.LOOKUP,
        tables=['hr.employees'],
        select=[
            ColumnRef(instance_id='employee_instance', column='full_name'),
            ColumnRef(instance_id='manager_instance', column='full_name'),
        ],
        table_instances=[
            TableInstance(instance_id='employee_instance', semantic_role='employee'),
            TableInstance(instance_id='manager_instance', semantic_role='manager'),
        ],
        instance_joins=[InstanceJoinRequest(
            source_instance_id='employee_instance', source_column='manager_id',
            target_instance_id='manager_instance', target_column='id',
        )],
        instance_filters=filters or [],
    )


def permissions() -> PermissionContext:
    return PermissionContext(
        scope=PermissionScope.DEPARTMENT,
        tenant_id='T1',
        row_filters=[QueryFilter(
            column='department_id', operator=FilterOperator.EQ, value='D1', source='permission',
        )],
    )


def compile_query():
    schema = registry()
    scoped = ScopedRowPolicy(schema).apply(query(), permissions())
    return SQLBuilder(schema).build(QueryPlanner(schema).plan(scoped))


def test_self_join_uses_fixed_internal_aliases_and_places_permissions_correctly():
    compiled = compile_query()
    sql = compiled.sql

    assert 'FROM "hr"."employees" AS "employee" LEFT OUTER JOIN "hr"."employees" AS "manager"' in sql
    assert '"employee"."manager_id" = "manager"."id"' in sql
    assert 'AS employee_full_name' in sql
    assert 'AS manager_full_name' in sql
    on, where = sql.split('\nWHERE ', 1)
    assert '"manager"."department_id" = :p0' in on
    assert '"manager"."tenant_id" = :p1' in on
    assert '"employee"."department_id" = :p2' in where
    assert '"employee"."tenant_id" = :p3' in where
    assert '"manager"."department_id"' not in where
    assert '"manager"."tenant_id"' not in where


def test_left_join_preserves_employee_when_manager_is_out_of_scope_or_missing():
    compiled = compile_query()
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS hr")
            conn.exec_driver_sql(
                'CREATE TABLE hr.employees (id TEXT, full_name TEXT, manager_id TEXT, tenant_id TEXT, department_id TEXT)'
            )
            conn.exec_driver_sql(
                "INSERT INTO hr.employees VALUES "
                "('M1', 'Allowed Manager', NULL, 'T1', 'D1'), "
                "('M2', 'Outside Manager', NULL, 'T1', 'D2'), "
                "('E1', 'Employee With Allowed Manager', 'M1', 'T1', 'D1'), "
                "('E2', 'Employee With Outside Manager', 'M2', 'T1', 'D1'), "
                "('E3', 'Employee Without Manager', NULL, 'T1', 'D1')"
            )
            rows = conn.execute(compiled.statement).fetchall()
    finally:
        engine.dispose()
    assert rows == [
        ('Allowed Manager', None),
        ('Employee With Allowed Manager', 'Allowed Manager'),
        ('Employee With Outside Manager', None),
        ('Employee Without Manager', None),
    ]


def test_instance_filters_bind_to_their_own_aliases():
    schema = registry()
    compiled = SQLBuilder(schema).build(QueryPlanner(schema).plan(ScopedRowPolicy(schema).apply(
        query(filters=[
            {'instance_id': 'employee_instance', 'column': 'department_id', 'value': 'D1'},
            {'instance_id': 'manager_instance', 'column': 'department_id', 'value': 'D1'},
        ]), permissions(),
    )))
    assert '"employee"."department_id" = :p4' in compiled.sql
    assert '"manager"."department_id" = :p5' in compiled.sql


def test_instance_compiler_rejects_mixed_legacy_projection_or_filter():
    schema = registry()
    with pytest.raises(MalformedQueryIRError, match='cannot mix logical and legacy'):
        QueryPlanner(schema).plan(query().model_copy(update={'select': [
            ColumnRef(instance_id='employee_instance', column='full_name'), 'hr.employees.full_name',
        ]}))
    with pytest.raises(MalformedQueryIRError, match='instance-qualified LOOKUP'):
        SQLBuilder(schema).build(QueryPlanner(schema).plan(query().model_copy(update={
            'filters': [QueryFilter(column='hr.employees.department_id', value='D1')],
        })))

"""Phase-1 validation coverage for inert logical table-instance models."""

import pytest
from pydantic import ValidationError

from src.query.ir import ColumnRef, QueryIR, TableInstance
from src.shared.enums import Intent
from src.shared.exceptions import InvalidColumnError, MalformedQueryIRError


def employee_manager_query() -> QueryIR:
    return QueryIR(
        intent=Intent.LOOKUP,
        tables=['hr.employees'],
        select=['hr.employees.full_name'],
        table_instances=[
            TableInstance(instance_id='employee_instance', semantic_role='employee'),
            TableInstance(instance_id='manager_instance', semantic_role='manager'),
        ],
    )


def test_distinct_logical_instances_can_share_the_employee_table():
    query = employee_manager_query()
    employee, manager = query.table_instances

    assert employee != manager
    assert employee.instance_id != manager.instance_id
    assert employee.semantic_role != manager.semantic_role
    assert employee.physical_table == manager.physical_table == 'hr.employees'


def test_instance_qualified_columns_remain_distinct_and_resolve_to_same_physical_column():
    query = employee_manager_query()
    employee_name = ColumnRef(instance_id='employee_instance', column='full_name')
    manager_name = ColumnRef(instance_id='manager_instance', column='full_name')

    assert employee_name != manager_name
    assert query.resolve_instance_column(employee_name, registry=_employee_registry()) == 'hr.employees.full_name'
    assert query.resolve_instance_column(manager_name, registry=_employee_registry()) == 'hr.employees.full_name'


def test_forged_instance_id_is_rejected_before_resolution():
    with pytest.raises(ValidationError):
        ColumnRef.model_validate({'instance_id': 'forged_instance', 'column': 'full_name'})


def test_known_instance_missing_from_query_is_rejected():
    query = QueryIR(
        intent=Intent.LOOKUP,
        tables=['hr.employees'],
        select=['hr.employees.full_name'],
        table_instances=[TableInstance(instance_id='employee_instance', semantic_role='employee')],
    )
    with pytest.raises(MalformedQueryIRError, match='Unknown logical table instance'):
        query.resolve_instance_column(
            ColumnRef(instance_id='manager_instance', column='full_name'),
            registry=_employee_registry(),
        )


def test_column_from_another_physical_table_is_rejected():
    query = employee_manager_query()
    with pytest.raises(InvalidColumnError, match='does not belong'):
        query.resolve_instance_column(
            ColumnRef(instance_id='employee_instance', column='customer_type'),
            registry=_employee_registry(),
        )


@pytest.mark.parametrize('payload', [
    {'instance_id': 'employee_instance', 'semantic_role': 'manager'},
    {'instance_id': 'employee', 'semantic_role': 'employee'},
    {'instance_id': 'manager_instance', 'semantic_role': 'subordinate'},
    {'instance_id': 'employee_instance', 'semantic_role': 'employee', 'physical_table': 'sales.orders'},
    {'instance_id': 'employee_instance', 'semantic_role': 'employee', 'sql_alias': 'employee'},
])
def test_forged_roles_and_sql_alias_inputs_are_rejected(payload):
    with pytest.raises(ValidationError):
        TableInstance.model_validate(payload)


def test_duplicate_instance_ids_are_rejected():
    with pytest.raises(ValidationError, match='must be unique'):
        QueryIR(
            intent=Intent.LOOKUP,
            tables=['hr.employees'],
            select=['hr.employees.full_name'],
            table_instances=[
                TableInstance(instance_id='employee_instance', semantic_role='employee'),
                TableInstance(instance_id='employee_instance', semantic_role='employee'),
            ],
        )


def test_legacy_physical_table_query_has_no_logical_instances_and_remains_valid():
    query = QueryIR(
        intent=Intent.LOOKUP,
        tables=['hr.employees'],
        select=['hr.employees.full_name'],
    )
    assert query.table_instances == []


def _employee_registry():
    from src.schema.registry import SchemaRegistry
    from src.shared.models import SchemaColumn, SchemaTable

    return SchemaRegistry(tables=[SchemaTable(
        name='employees', schema_name='hr', columns=[
            SchemaColumn(name='id', data_type='integer', primary_key=True),
            SchemaColumn(name='full_name', data_type='text'),
            SchemaColumn(name='manager_id', data_type='integer'),
        ],
    )])

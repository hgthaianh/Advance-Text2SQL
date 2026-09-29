"""Phase-2A planner coverage for the fixed employee-manager logical join."""

import pytest
from pydantic import ValidationError

from src.query.ir import InstanceJoinRequest, QueryIR, TableInstance
from src.query.models import InstanceJoin
from src.query.planner import QueryPlanner
from src.query.sql_builder import SQLBuilder
from src.schema.registry import SchemaRegistry
from src.shared.enums import Intent
from src.shared.exceptions import InvalidColumnError, MalformedQueryIRError
from src.shared.models import SchemaColumn, SchemaRelationship, SchemaTable


@pytest.fixture
def employee_registry() -> SchemaRegistry:
    return SchemaRegistry(
        tables=[SchemaTable(name='employees', schema_name='hr', columns=[
            SchemaColumn(name='id', data_type='integer', primary_key=True),
            SchemaColumn(name='full_name', data_type='text'),
            SchemaColumn(name='manager_id', data_type='integer'),
            SchemaColumn(name='department_id', data_type='integer'),
        ])],
        relationships=[SchemaRelationship(
            source='hr.employees.manager_id', target='hr.employees.id',
        )],
    )


def self_join_query(**overrides) -> QueryIR:
    payload = {
        'intent': Intent.LOOKUP,
        'tables': ['hr.employees'],
        'select': ['hr.employees.full_name'],
        'table_instances': [
            TableInstance(instance_id='employee_instance', semantic_role='employee'),
            TableInstance(instance_id='manager_instance', semantic_role='manager'),
        ],
        'instance_joins': [InstanceJoinRequest(
            source_instance_id='employee_instance', source_column='manager_id',
            target_instance_id='manager_instance', target_column='id', join_type='left',
        )],
    }
    payload.update(overrides)
    return QueryIR(**payload)


def test_planner_creates_validated_employee_manager_instance_join(employee_registry):
    plan = QueryPlanner(employee_registry).plan(self_join_query())

    assert len(plan.instance_joins) == 1
    join = plan.instance_joins[0]
    assert isinstance(join, InstanceJoin)
    assert (join.source_instance_id, join.source_column) == ('employee_instance', 'manager_id')
    assert (join.target_instance_id, join.target_column) == ('manager_instance', 'id')
    assert join.join_type == 'left'
    assert join.relationship.source == 'hr.employees.manager_id'
    assert join.relationship.target == 'hr.employees.id'


@pytest.mark.parametrize('join_payload', [
    dict(source_instance_id='employee_instance', source_column='id',
         target_instance_id='manager_instance', target_column='id'),
    dict(source_instance_id='manager_instance', source_column='manager_id',
         target_instance_id='employee_instance', target_column='id'),
    dict(source_instance_id='employee_instance', source_column='id',
         target_instance_id='manager_instance', target_column='department_id'),
])
def test_planner_rejects_any_other_self_join_shape(employee_registry, join_payload):
    query = self_join_query(instance_joins=[InstanceJoinRequest(**join_payload)])
    with pytest.raises(MalformedQueryIRError, match='Only employee_instance.manager_id'):
        QueryPlanner(employee_registry).plan(query)


def test_planner_rejects_invalid_instance_column(employee_registry):
    query = self_join_query(instance_joins=[InstanceJoinRequest(
        source_instance_id='employee_instance', source_column='not_a_column',
        target_instance_id='manager_instance', target_column='id',
    )])
    with pytest.raises(InvalidColumnError, match='does not belong'):
        QueryPlanner(employee_registry).plan(query)


def test_planner_rejects_known_but_unregistered_instance(employee_registry):
    query = self_join_query(table_instances=[
        TableInstance(instance_id='employee_instance', semantic_role='employee'),
    ])
    with pytest.raises(MalformedQueryIRError, match='unknown table instance'):
        QueryPlanner(employee_registry).plan(query)


def test_planner_requires_the_registered_employee_manager_fk(employee_registry):
    registry_without_self_fk = SchemaRegistry(tables=list(employee_registry.tables.values()))
    with pytest.raises(MalformedQueryIRError, match='foreign key is required'):
        QueryPlanner(registry_without_self_fk).plan(self_join_query())


def test_forged_instance_and_alias_input_are_rejected_at_ir_boundary(employee_registry):
    payload = self_join_query().model_dump()
    payload['instance_joins'][0]['source_instance_id'] = 'unknown_instance'
    with pytest.raises(MalformedQueryIRError):
        QueryPlanner(employee_registry).plan(payload)

    with pytest.raises(ValidationError):
        InstanceJoinRequest.model_validate({
            'source_instance_id': 'employee_instance', 'source_column': 'manager_id',
            'target_instance_id': 'manager_instance', 'target_column': 'id', 'sql_alias': 'manager',
        })


def test_duplicate_instance_join_is_rejected_at_ir_boundary():
    join = InstanceJoinRequest(
        source_instance_id='employee_instance', source_column='manager_id',
        target_instance_id='manager_instance', target_column='id',
    )
    with pytest.raises(ValidationError, match='must be unique'):
        self_join_query(instance_joins=[join, join])


def test_legacy_query_plan_still_has_no_instance_joins(employee_registry):
    query = QueryIR(intent=Intent.LOOKUP, tables=['hr.employees'], select=['hr.employees.full_name'])
    plan = QueryPlanner(employee_registry).plan(query)
    assert plan.instance_joins == []


def test_instance_join_without_instance_qualified_projection_fails_closed(employee_registry):
    plan = QueryPlanner(employee_registry).plan(self_join_query())
    with pytest.raises(MalformedQueryIRError, match='requires instance-qualified LOOKUP'):
        SQLBuilder(employee_registry).build(plan)

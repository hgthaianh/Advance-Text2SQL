"""Public semantic evidence for the only supported role-aware self join."""

from datetime import date

import pytest

from src.nlp.entity_extractor import DeterministicEntityExtractor
from src.nlp.robustness import NLPRobustnessProcessor
from src.query.ir import ColumnRef
from src.query.ir_builder import QueryIRBuilder
from src.schema.linker import SchemaLinker
from src.schema.registry import SchemaRegistry
from src.shared.exceptions import AmbiguousQueryError


def build(question: str):
    registry = SchemaRegistry()
    preprocessed = NLPRobustnessProcessor().process_query(question, date(2026, 9, 20))
    return QueryIRBuilder(registry).build(
        preprocessed, SchemaLinker(registry).link(preprocessed),
        DeterministicEntityExtractor().extract(preprocessed.normalized_text),
    )


@pytest.mark.parametrize('question', [
    'List each employee and their manager',
    'List each employee with their manager',
    'employees and their managers',
    'List all employees and their managers, including employees without managers',
])
def test_explicit_employee_manager_evidence_builds_the_fixed_logical_query(question):
    query = build(question)

    assert [(item.instance_id, item.column) for item in query.select if isinstance(item, ColumnRef)] == [
        ('employee_instance', 'full_name'), ('manager_instance', 'full_name'),
    ]
    assert [(item.instance_id, item.semantic_role) for item in query.table_instances] == [
        ('employee_instance', 'employee'), ('manager_instance', 'manager'),
    ]
    assert query.instance_joins[0].model_dump() == {
        'source_instance_id': 'employee_instance', 'source_column': 'manager_id',
        'target_instance_id': 'manager_instance', 'target_column': 'id', 'join_type': 'left',
    }


@pytest.mark.parametrize('question', [
    'manager', 'List managers', 'employee manager relationship', 'list branch manager',
])
def test_incomplete_or_unrelated_manager_language_is_ambiguous_not_self_join(question):
    with pytest.raises(AmbiguousQueryError, match='explicit employee-manager'):
        build(question)

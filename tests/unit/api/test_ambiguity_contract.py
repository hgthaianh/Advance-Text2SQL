import json

from src.api.errors import error_response
from src.permission.models import PermissionContext
from src.pipeline.orchestrator import Text2SQLPipeline
from src.shared.exceptions import AmbiguousJoinPathError
from src.shared.models import QueryResult


def test_ambiguity_response_keeps_the_existing_contract_and_adds_safe_guidance():
    error = AmbiguousJoinPathError(
        'internal diagnostic',
        candidate_paths=[['sales.orders', 'sales.sales_reps', 'hr.employees']],
    )
    ambiguity = Text2SQLPipeline._safe_ambiguity(error, PermissionContext(allowed_tables=['*']))

    response = error_response(QueryResult(
        success=False, query_id='query-1', error_code='AMBIGUOUS_QUERY',
        message='The query could not be completed.', ambiguity=ambiguity,
    ))

    assert response.status_code == 422
    assert json.loads(response.body) == {
        'query_id': 'query-1',
        'error_code': 'AMBIGUOUS_QUERY',
        'message': 'The query could not be completed.',
        'ambiguity': {
            'candidate_paths': [['sales.orders', 'sales.sales_reps', 'hr.employees']],
            'hints': [
                'Specify the business relationship that should be used.',
                'Add an intermediate table or relationship to clarify the request.',
            ],
        },
    }


def test_ambiguity_paths_are_hidden_when_any_table_is_not_authorized():
    error = AmbiguousJoinPathError(
        'internal diagnostic',
        candidate_paths=[['sales.orders', 'sales.sales_reps', 'hr.employees']],
    )

    assert Text2SQLPipeline._safe_ambiguity(error, PermissionContext(allowed_tables=['sales.orders'])) is None

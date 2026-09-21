import pytest

from src.api.errors import error_response
from src.shared.models import QueryResult


@pytest.mark.parametrize(('error_code', 'status_code'), [
    ('PERMISSION_DENIED', 403), ('MISSING_ENTITY', 422), ('AMBIGUOUS_QUERY', 422),
    ('SQL_VALIDATION_ERROR', 422), ('SQL_EXECUTION_ERROR', 503),
])
def test_controlled_pipeline_errors_have_a_stable_public_contract(error_code, status_code):
    response = error_response(QueryResult(
        success=False, query_id='query-1', error_code=error_code, message='safe public message',
        sql='select secret', params={'email': 'person@example.com'},
    ))

    assert response.status_code == status_code
    assert response.body == b'{"query_id":"query-1","error_code":"' + error_code.encode() + b'","message":"safe public message"}'


def test_error_contract_rejects_successful_results():
    with pytest.raises(ValueError):
        error_response(QueryResult(success=True))

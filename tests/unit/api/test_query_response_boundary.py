from fastapi.testclient import TestClient
import pytest

from src.api.dependencies import get_authentication_provider
from src.auth.models import AuthenticatedIdentity
from src.auth.provider import MockAuthenticationProvider
from src.main import app
from src.shared.models import QueryResult


@pytest.mark.parametrize('role, identity_fields', [
    ('department_manager', {'department_id': 'D1'}),
    ('employee', {'employee_id': 'E1'}),
])
def test_query_response_never_exposes_sql_or_params_to_business_users(role, identity_fields) -> None:
    from src.api.routes import query as query_module

    class SuccessfulPipeline:
        def execute(self, _question, _user):
            return QueryResult(
                success=True, query_id='safe-query', columns=['total'], rows=[{'total': 1}],
                row_count=1, scalar=1, sql='SELECT confidential', params={'p0': 'secret'},
                natural_language_response='Có 1 kết quả.',
            )

    original_pipeline = query_module.pipeline
    query_module.pipeline = SuccessfulPipeline()
    app.dependency_overrides[get_authentication_provider] = lambda: MockAuthenticationProvider(
        AuthenticatedIdentity(user_id='business-user', role=role, tenant_id='T1', **identity_fields)
    )
    try:
        with TestClient(app) as client:
            response = client.post('/query', json={'question': 'bao nhiêu?'})
        assert response.status_code == 200
        body = response.json()
        assert body['natural_language_response'] == 'Có 1 kết quả.'
        assert 'sql' not in body
        assert 'params' not in body
        assert 'confidential' not in response.text
        assert 'secret' not in response.text
    finally:
        query_module.pipeline = original_pipeline
        app.dependency_overrides.pop(get_authentication_provider, None)

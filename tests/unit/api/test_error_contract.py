import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from src.api.dependencies import get_authentication_provider
from src.api.errors import error_response
from src.api.routes import health
from src.auth.models import LoginRequest
from src.auth.provider import DefaultAuthenticationProvider, MockAuthenticationProvider
from src.main import app
from src.shared.exceptions import ServiceUnavailableError
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


def test_authentication_database_failure_is_a_safe_503():
    class UnavailableEngine:
        def begin(self):
            raise OperationalError('connect', {}, RuntimeError('database unavailable'))

    provider = DefaultAuthenticationProvider(engine=UnavailableEngine())
    with pytest.raises(ServiceUnavailableError):
        provider.login(LoginRequest(username='user', password='password'))


def test_login_service_failure_uses_the_stable_503_contract(monkeypatch):
    provider = MockAuthenticationProvider()

    def unavailable(_: LoginRequest):
        raise ServiceUnavailableError('database unavailable')

    monkeypatch.setattr(provider, 'login', unavailable)
    monkeypatch.setitem(app.dependency_overrides, get_authentication_provider, lambda: provider)
    with TestClient(app) as client:
        response = client.post('/auth/login', json={'username': 'user', 'password': 'password'})

    assert response.status_code == 503
    assert response.json()['error_code'] == 'SERVICE_UNAVAILABLE'


def test_readiness_database_failure_has_the_stable_503_contract(monkeypatch):
    class UnavailableEngine:
        def connect(self):
            raise OperationalError('connect', {}, RuntimeError('database unavailable'))

    monkeypatch.setattr(health, 'get_engine', lambda: UnavailableEngine())
    with TestClient(app) as client:
        response = client.get('/health/ready')

    assert response.status_code == 503
    assert response.json() == {
        'query_id': None,
        'error_code': 'SERVICE_UNAVAILABLE',
        'message': 'The service is temporarily unavailable. Please try again later.',
    }

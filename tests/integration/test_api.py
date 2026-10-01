from fastapi.testclient import TestClient
from starlette.requests import Request

from src.api.dependencies import get_authenticated_user, get_authentication_provider
from src.auth.models import AuthenticatedIdentity
from src.auth.provider import MockAuthenticationProvider
from src.main import app
from src.shared.models import QueryResult


def test_app_starts():
    with TestClient(app) as client:
        assert client.get("/openapi.json").status_code == 200


def test_health():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_query_uses_real_pipeline_not_mock():
    app.dependency_overrides[get_authentication_provider] = lambda: MockAuthenticationProvider(
        AuthenticatedIdentity(user_id='demo', role='viewer', tenant_id='T1')
    )
    try:
        with TestClient(app) as client:
            response = client.post("/query", json={"question": "List employees"}, headers={
                'X-User-Id': 'demo', 'X-Role': 'viewer', 'X-Tenant-Id': 'T1',
            })
        assert response.status_code in {422, 503}
        assert response.json()['error_code'] in {'UNSUPPORTED_QUERY', 'SQL_EXECUTION_ERROR'}
    finally:
        app.dependency_overrides.pop(get_authentication_provider, None)


def test_query_rejects_blank_question():
    app.dependency_overrides[get_authentication_provider] = lambda: MockAuthenticationProvider(
        AuthenticatedIdentity(user_id='demo', role='viewer', tenant_id='T1')
    )
    try:
        with TestClient(app) as client:
            response = client.post("/query", json={"question": "   "}, headers={
                'X-User-Id': 'demo', 'X-Role': 'viewer', 'X-Tenant-Id': 'T1',
            })
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_authentication_provider, None)


def test_query_success_does_not_expose_sql_or_params(monkeypatch):
    app.dependency_overrides[get_authentication_provider] = lambda: MockAuthenticationProvider(
        AuthenticatedIdentity(user_id='demo', role='viewer', tenant_id='T1')
    )
    monkeypatch.setattr(
        'src.api.routes.query.pipeline.execute',
        lambda question, user: QueryResult(
            success=True,
            scalar=1,
            sql='SELECT secret FROM private_table WHERE tenant_id = :tenant_id',
            params={'tenant_id': 'T1'},
        ),
    )
    try:
        with TestClient(app) as client:
            response = client.post('/query', json={'question': 'count records'})
        assert response.status_code == 200
        assert 'sql' not in response.json()
        assert 'params' not in response.json()
        assert 'is_mock' not in response.json()
    finally:
        app.dependency_overrides.pop(get_authentication_provider, None)


def test_query_passes_previous_question_only_when_provided(monkeypatch):
    app.dependency_overrides[get_authentication_provider] = lambda: MockAuthenticationProvider(
        AuthenticatedIdentity(user_id='demo', role='viewer', tenant_id='T1')
    )
    captured = []

    def execute(question, user, *, previous_question):
        captured.append((question, previous_question))
        return QueryResult(success=True, scalar=1)

    monkeypatch.setattr('src.api.routes.query.pipeline.execute', execute)
    try:
        with TestClient(app) as client:
            response = client.post('/query', json={
                'question': 'chi tiết về số liệu đó', 'previous_question': 'doanh thu 6 tháng gần nhất',
            })
        assert response.status_code == 200
        assert captured == [('chi tiết về số liệu đó', 'doanh thu 6 tháng gần nhất')]
    finally:
        app.dependency_overrides.pop(get_authentication_provider, None)


def test_query_rejects_request_body_identity_and_requires_auth_context():
    app.dependency_overrides.pop(get_authentication_provider, None)
    with TestClient(app) as client:
        response = client.post('/query', json={
            'question': 'List employees', 'user': {'user_id': 'attacker', 'role': 'admin', 'tenant_id': 'T2'},
        })
    assert response.status_code == 401


def test_auth_dependency_preserves_team_identity():
    identity = AuthenticatedIdentity(
        user_id='team-lead', role='team_lead', tenant_id='T1', team_id='TEAM-1',
    )
    user = identity.to_user_context()
    assert user.team_id == 'TEAM-1'

    scope = {"type": "http", "state": {"identity": identity}}
    request = Request(scope)
    user_from_req = get_authenticated_user(request)
    assert user_from_req.team_id == 'TEAM-1'

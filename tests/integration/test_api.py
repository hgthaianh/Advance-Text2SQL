from fastapi.testclient import TestClient
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
    with TestClient(app) as client:
        response = client.post("/query", json={"question": "List employees"}, headers={
            'X-User-Id': 'demo', 'X-Role': 'viewer', 'X-Tenant-Id': 'T1',
        })
    assert response.status_code in {422, 503}
    assert response.json()['error_code'] in {'UNSUPPORTED_QUERY', 'SQL_EXECUTION_ERROR'}


def test_query_rejects_blank_question():
    with TestClient(app) as client:
        response = client.post("/query", json={"question": "   "}, headers={
            'X-User-Id': 'demo', 'X-Role': 'viewer', 'X-Tenant-Id': 'T1',
        })
    assert response.status_code == 422


def test_query_rejects_request_body_identity_and_requires_auth_context():
    with TestClient(app) as client:
        response = client.post('/query', json={
            'question': 'List employees', 'user': {'user_id': 'attacker', 'role': 'admin', 'tenant_id': 'T2'},
        })
    assert response.status_code == 401

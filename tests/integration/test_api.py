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
        response = client.post("/query", json={
            "question": "List employees", "user": {"user_id": "demo", "role": "viewer"},
        })
    assert response.status_code == 200
    result = QueryResult.model_validate(response.json())
    assert result.is_mock is False
    assert result.error_code is not None


def test_query_rejects_blank_question():
    with TestClient(app) as client:
        response = client.post("/query", json={
            "question": "   ", "user": {"user_id": "demo", "role": "viewer"},
        })
    assert response.status_code == 422

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from src.api.dependencies import get_authentication_provider
from src.auth.models import AuthenticatedIdentity
from src.auth.provider import MockAuthenticationProvider
from src.main import app
from src.pipeline.observability import PersistedPipelineObserver, PipelineEvent


def test_admin_history_is_authorized_and_excludes_bound_values() -> None:
    from src.api.routes import observability as observability_module

    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    PersistedPipelineObserver(engine).record(PipelineEvent(
        query_id='trace-1', outcome='success', stage='execution', duration_ms=12,
        question='nhân viên có mã E1 là ai?',
        sql='SELECT name FROM employees WHERE id = :p0', row_count=1, truncated=False,
    ))
    original_get_engine = observability_module.get_engine
    observability_module.get_engine = lambda: engine
    try:
        for role, expected_status in [('admin', 200), ('department_manager', 403), ('employee', 403)]:
            app.dependency_overrides[get_authentication_provider] = lambda role=role: MockAuthenticationProvider(
                AuthenticatedIdentity(user_id=role, role=role, tenant_id='T1', department_id='D1', employee_id='E1')
            )
            with TestClient(app) as client:
                response = client.get('/observability/history/trace-1')
            assert response.status_code == expected_status
            if expected_status == 200:
                body = response.json()
                assert body['question'] == 'nhân viên có mã E1 là ai?'
                assert body['sql'] == 'SELECT name FROM employees WHERE id = :p0'
                assert body['row_count'] == 1
                assert 'params' not in body
                assert 'employee_id' not in body
    finally:
        observability_module.get_engine = original_get_engine
        app.dependency_overrides.pop(get_authentication_provider, None)

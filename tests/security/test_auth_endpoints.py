"""Focused tests for /auth/login, /auth/refresh, and /auth/me endpoints."""

from datetime import timedelta
from typing import Any, cast
import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_authentication_provider
from src.auth.models import AuthenticatedIdentity
from src.auth.provider import (
    MockAuthenticationProvider,
)
from src.auth.tokens import (
    clear_revoked_tokens,
    create_access_token,
    create_refresh_token,
    revoke_token,
)
from src.main import app
from src.shared.models import UserContext


@pytest.fixture(autouse=True)
def cleanup_overrides_and_revocations():
    """Ensure clean test environment before and after each test."""
    clear_revoked_tokens()
    credentials = {
        "admin": ("test-admin-password", AuthenticatedIdentity(
            user_id="admin", username="admin", role="admin", tenant_id="1",
        )),
        "user0001": ("test-viewer-password", AuthenticatedIdentity(
            user_id="user0001", username="user0001", role="viewer", tenant_id="1",
            employee_id="1", department_id="1", branch_id="1", team_id="1",
        )),
    }
    app.dependency_overrides[get_authentication_provider] = lambda: MockAuthenticationProvider(credentials=credentials)
    yield
    clear_revoked_tokens()
    app.dependency_overrides.pop(get_authentication_provider, None)


@pytest.fixture
def admin_credentials() -> tuple[str, str]:
    return "admin", "test-admin-password"


@pytest.fixture
def viewer_credentials() -> tuple[str, str]:
    return "user0001", "test-viewer-password"


# -----------------------------------------------------------------------------
# 1. POST /auth/login Tests
# -----------------------------------------------------------------------------

def test_successful_login_returns_access_and_refresh_tokens(admin_credentials):
    username, password = admin_credentials
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={"username": username, "password": password},
        )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0
    assert password not in response.text


def test_invalid_login_wrong_password_returns_safe_401(admin_credentials):
    username, _ = admin_credentials
    invalid_password = "wrong_password"
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={"username": username, "password": invalid_password},
        )
    assert response.status_code == 401
    data = response.json()
    assert data["error_code"] == "INVALID_CREDENTIALS"
    assert "Invalid" in data["message"]
    assert invalid_password not in response.text
    assert "Traceback" not in response.text


def test_invalid_login_unknown_user_returns_safe_401():
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={"username": "non_existent_user_999", "password": "any_password"},
        )
    assert response.status_code == 401
    data = response.json()
    assert data["error_code"] == "INVALID_CREDENTIALS"
    assert "non_existent" not in data["message"]


def test_login_rejects_extra_fields_in_payload(admin_credentials):
    username, password = admin_credentials
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={
                "username": username,
                "password": password,
                "role": "superadmin",
            },
        )
    assert response.status_code == 422


# -----------------------------------------------------------------------------
# 2. POST /auth/refresh Tests
# -----------------------------------------------------------------------------

def test_successful_refresh_issues_new_access_token(admin_credentials):
    username, password = admin_credentials
    with TestClient(app) as client:
        login_res = client.post(
            "/auth/login",
            json={"username": username, "password": password},
        )
        refresh_token = login_res.json()["refresh_token"]

        refresh_res = client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token},
        )
    assert refresh_res.status_code == 200
    data = refresh_res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0


def test_refresh_with_invalid_token_returns_safe_401():
    with TestClient(app) as client:
        response = client.post(
            "/auth/refresh",
            json={"refresh_token": "totally.invalid.token"},
        )
    assert response.status_code == 401
    data = response.json()
    assert data["error_code"] == "INVALID_REFRESH_TOKEN"


def test_refresh_with_expired_token_returns_safe_401():
    identity = AuthenticatedIdentity(user_id="u1", role="admin", tenant_id="1")
    expired_token = create_refresh_token(identity, expires_delta=timedelta(seconds=-10))

    with TestClient(app) as client:
        response = client.post(
            "/auth/refresh",
            json={"refresh_token": expired_token},
        )
    assert response.status_code == 401
    data = response.json()
    assert data["error_code"] == "INVALID_REFRESH_TOKEN"


def test_refresh_rejects_access_token_as_refresh_token():
    identity = AuthenticatedIdentity(user_id="u1", role="admin", tenant_id="1")
    access_token, _ = create_access_token(identity)

    with TestClient(app) as client:
        response = client.post(
            "/auth/refresh",
            json={"refresh_token": access_token},
        )
    assert response.status_code == 401
    data = response.json()
    assert data["error_code"] == "INVALID_REFRESH_TOKEN"


def test_refresh_rejects_revoked_token():
    identity = AuthenticatedIdentity(user_id="u1", role="admin", tenant_id="1")
    refresh_token = create_refresh_token(identity)
    revoke_token(refresh_token)

    with TestClient(app) as client:
        response = client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token},
        )
    assert response.status_code == 401
    data = response.json()
    assert data["error_code"] == "INVALID_REFRESH_TOKEN"


def test_refresh_token_cannot_be_replayed(admin_credentials):
    username, password = admin_credentials
    with TestClient(app) as client:
        token = client.post("/auth/login", json={"username": username, "password": password}).json()["refresh_token"]
        assert client.post("/auth/refresh", json={"refresh_token": token}).status_code == 200
        assert client.post("/auth/refresh", json={"refresh_token": token}).status_code == 401


def test_logout_revokes_the_current_refresh_token(admin_credentials):
    username, password = admin_credentials
    with TestClient(app) as client:
        tokens = client.post("/auth/login", json={"username": username, "password": password}).json()
        response = client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]},
                               headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert response.status_code == 204
        assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_refresh_preserves_immutable_identity(viewer_credentials):
    """Verify refresh cannot mutate any of the user's 7 identity fields."""
    username, password = viewer_credentials
    with TestClient(app) as client:
        login_res = client.post(
            "/auth/login",
            json={"username": username, "password": password},
        )
        refresh_token = login_res.json()["refresh_token"]

        # Attempt to refresh while injecting headers or extra context
        refresh_res = client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token},
            headers={"X-User-Id": "attacker", "X-Role": "admin"},
        )
        new_access_token = refresh_res.json()["access_token"]

        # Call /auth/me with the refreshed token
        me_res = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {new_access_token}"},
        )

    assert me_res.status_code == 200
    identity = me_res.json()
    assert identity["user_id"] == "user0001"
    assert identity["username"] == "user0001"
    assert identity["role"] == "viewer"
    assert identity["tenant_id"] == "1"
    assert identity["employee_id"] == "1"
    assert identity["department_id"] == "1"
    assert identity["branch_id"] == "1"
    assert identity["team_id"] == "1"


# -----------------------------------------------------------------------------
# 3. GET /auth/me Tests
# -----------------------------------------------------------------------------

def test_authenticated_auth_me_returns_identity(admin_credentials):
    username, password = admin_credentials
    with TestClient(app) as client:
        login_res = client.post(
            "/auth/login",
            json={"username": username, "password": password},
        )
        token = login_res.json()["access_token"]

        response = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "admin"
    assert data["username"] == "admin"
    assert data["role"] == "admin"
    assert data["tenant_id"] == "1"


def test_unauthenticated_auth_me_fails_closed():
    with TestClient(app) as client:
        response = client.get("/auth/me")
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTHENTICATION_REQUIRED"


def test_auth_me_rejects_invalid_token():
    with TestClient(app) as client:
        response = client.get(
            "/auth/me",
            headers={"Authorization": "Bearer invalid.token.value"},
        )
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTHENTICATION_REQUIRED"


def test_auth_me_rejects_expired_token():
    identity = AuthenticatedIdentity(user_id="u1", role="admin", tenant_id="1")
    expired_token, _ = create_access_token(identity, expires_delta=timedelta(seconds=-10))

    with TestClient(app) as client:
        response = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTHENTICATION_REQUIRED"


def test_auth_me_rejects_refresh_token_used_as_bearer_token():
    identity = AuthenticatedIdentity(user_id="u1", role="admin", tenant_id="1")
    refresh_token = create_refresh_token(identity)

    with TestClient(app) as client:
        response = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {refresh_token}"},
        )
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTHENTICATION_REQUIRED"


# -----------------------------------------------------------------------------
# 4. Identity Consistency & Header Spoofing Protection
# -----------------------------------------------------------------------------

def test_identity_consistency_between_auth_me_and_query(viewer_credentials):
    """Verify that an access token resolves to the exact same identity in /auth/me and /query."""
    username, password = viewer_credentials
    with TestClient(app) as client:
        login_res = client.post(
            "/auth/login",
            json={"username": username, "password": password},
        )
        token = login_res.json()["access_token"]

        me_res = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_res.status_code == 200
        me_identity = me_res.json()

        captured_user: list[UserContext] = []
        from src.api.routes import query as query_module
        original_pipeline = query_module.pipeline

        class CapturePipeline:
            def execute(self, question: str, user_context: UserContext, reference_date=None):
                captured_user.append(user_context)
                return original_pipeline.execute(question, user_context, reference_date=reference_date)

        query_module.pipeline = cast(Any, CapturePipeline())
        try:
            client.post(
                "/query",
                json={"question": "co bao nhieu nhan vien"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert len(captured_user) == 1
            query_identity = captured_user[0]

            assert query_identity.user_id == me_identity["user_id"]
            assert query_identity.username == me_identity["username"]
            assert query_identity.role == me_identity["role"]
            assert query_identity.tenant_id == me_identity["tenant_id"]
            assert query_identity.employee_id == me_identity["employee_id"]
            assert query_identity.department_id == me_identity["department_id"]
            assert query_identity.branch_id == me_identity["branch_id"]
            assert query_identity.team_id == me_identity["team_id"]
        finally:
            query_module.pipeline = original_pipeline


@pytest.mark.parametrize("header_name, spoofed_value, field_name, expected_value", [
    ("X-User-Id", "attacker_user", "user_id", "legit_user"),
    ("X-Role", "admin", "role", "viewer"),
    ("X-Tenant-Id", "TENANT_EVIL", "tenant_id", "TENANT_SAFE"),
    ("X-Employee-Id", "EMP_ATTACKER", "employee_id", "EMP_1"),
    ("X-Department-Id", "DEPT_ATTACKER", "department_id", "DEPT_1"),
    ("X-Branch-Id", "BRANCH_ATTACKER", "branch_id", "BRANCH_1"),
    ("X-Team-Id", "TEAM_ATTACKER", "team_id", "TEAM_1"),
])
def test_all_seven_headers_spoofing_attempts_on_auth_me(
    header_name: str, spoofed_value: str, field_name: str, expected_value: str,
):
    """Verify that none of the seven X-* headers can override /auth/me identity."""
    identity = AuthenticatedIdentity(
        user_id="legit_user",
        role="viewer",
        tenant_id="TENANT_SAFE",
        employee_id="EMP_1",
        department_id="DEPT_1",
        branch_id="BRANCH_1",
        team_id="TEAM_1",
    )
    token, _ = create_access_token(identity)

    with TestClient(app) as client:
        response = client.get(
            "/auth/me",
            headers={
                "Authorization": f"Bearer {token}",
                header_name: spoofed_value,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data[field_name] == expected_value, (
        f"Header spoofing vulnerability: {header_name}={spoofed_value} altered {field_name} to {data[field_name]}"
    )


def test_token_user_identity_mismatch_and_tenant_department_spoofing():
    """Verify caller cannot supply conflicting headers to alter tenant/department or user identity."""
    identity = AuthenticatedIdentity(
        user_id="sales_mgr",
        role="department_manager",
        tenant_id="T1",
        department_id="D1",
    )
    token, _ = create_access_token(identity)

    with TestClient(app) as client:
        response = client.get(
            "/auth/me",
            headers={
                "Authorization": f"Bearer {token}",
                "X-User-Id": "attacker",
                "X-Role": "admin",
                "X-Tenant-Id": "T_MALICIOUS",
                "X-Department-Id": "D2_FORBIDDEN",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "sales_mgr"
    assert data["role"] == "department_manager"
    assert data["tenant_id"] == "T1"
    assert data["department_id"] == "D1"


def test_mock_authentication_provider_login_and_refresh():
    """Verify MockAuthenticationProvider works with dependency overrides."""
    mock_identity = AuthenticatedIdentity(
        user_id="custom_mock",
        role="analyst",
        tenant_id="T99",
    )
    mock_username = "test_user"
    mock_password = "test_mock_password"
    mock_provider = MockAuthenticationProvider(
        identity=mock_identity,
        credentials={mock_username: (mock_password, mock_identity)},
    )
    app.dependency_overrides[get_authentication_provider] = lambda: mock_provider

    with TestClient(app) as client:
        login_res = client.post(
            "/auth/login",
            json={"username": mock_username, "password": mock_password},
        )
        assert login_res.status_code == 200
        tokens = login_res.json()
        assert "access_token" in tokens

        refresh_res = client.post(
            "/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert refresh_res.status_code == 200


def test_openapi_schema_contains_auth_endpoints():
    """Verify OpenAPI document registers the three auth endpoints with documentation."""
    with TestClient(app) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    assert "/auth/login" in paths
    assert "post" in paths["/auth/login"]
    assert "/auth/refresh" in paths
    assert "post" in paths["/auth/refresh"]
    assert "/auth/me" in paths
    assert "get" in paths["/auth/me"]
    assert "/auth/logout" in paths
    assert "post" in paths["/auth/logout"]

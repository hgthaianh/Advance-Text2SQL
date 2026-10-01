"""Comprehensive security tests for Authentication Boundary and Provider Abstraction.

Verifies:
1. Authentication success and correct UserContext construction.
2. Production fail-closed behavior (HTTP 401 AUTHENTICATION_REQUIRED).
3. Header spoofing protection across all seven identity fields.
4. Request body spoofing prevention.
5. Critical security scenarios: attacker headers cannot override authenticated identity.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.api.dependencies import get_authentication_provider
from src.auth.models import AuthenticatedIdentity
from src.auth.provider import MockAuthenticationProvider
from src.main import app
from src.shared.exceptions import AuthenticationError
from src.shared.models import UserContext


# -----------------------------------------------------------------------------
# A. Authentication Success & Field Mapping
# -----------------------------------------------------------------------------

def test_authenticated_identity_maps_all_seven_fields_to_user_context():
    """Verify that all seven identity fields map correctly to UserContext."""
    identity = AuthenticatedIdentity(
        user_id="user_100",
        username="user0100",
        role="team_lead",
        tenant_id="TENANT_A",
        employee_id="EMP_100",
        department_id="DEPT_SALES",
        branch_id="BRANCH_HCM",
        team_id="TEAM_ALPHA",
    )
    user_context = identity.to_user_context()

    assert isinstance(user_context, UserContext)
    assert user_context.user_id == "user_100"
    assert user_context.username == "user0100"
    assert user_context.role == "team_lead"
    assert user_context.tenant_id == "TENANT_A"
    assert user_context.employee_id == "EMP_100"
    assert user_context.department_id == "DEPT_SALES"
    assert user_context.branch_id == "BRANCH_HCM"
    assert user_context.team_id == "TEAM_ALPHA"


def test_authenticated_identity_to_headers_generates_canonical_seven_headers():
    """Verify that to_headers() outputs the exact canonical seven X-* headers."""
    identity = AuthenticatedIdentity(
        user_id="user_100",
        role="department_manager",
        tenant_id="T1",
        employee_id="E100",
        department_id="D1",
        branch_id="B1",
        team_id="T1",
    )
    headers = identity.to_headers()

    assert headers == {
        "X-User-Id": "user_100",
        "X-Role": "department_manager",
        "X-Tenant-Id": "T1",
        "X-Employee-Id": "E100",
        "X-Department-Id": "D1",
        "X-Branch-Id": "B1",
        "X-Team-Id": "T1",
    }


def test_authenticated_identity_rejects_empty_required_fields():
    """Verify strict validation on required fields (user_id, role, tenant_id)."""
    with pytest.raises(Exception):
        AuthenticatedIdentity(user_id="", role="admin", tenant_id="T1")

    with pytest.raises(Exception):
        AuthenticatedIdentity(user_id="u1", role="", tenant_id="T1")

    with pytest.raises(Exception):
        AuthenticatedIdentity(user_id="u1", role="admin", tenant_id="")


# -----------------------------------------------------------------------------
# B. Authentication Failure & Fail-Closed Behavior
# -----------------------------------------------------------------------------

def test_production_fails_closed_when_no_identity_established():
    """Verify that protected route /query rejects unauthenticated requests with HTTP 401."""
    # Ensure no test overrides are active
    app.dependency_overrides.pop(get_authentication_provider, None)

    with TestClient(app) as client:
        # Unauthenticated request without an auth provider providing identity
        response = client.post("/query", json={"question": "List employees"})

    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTHENTICATION_REQUIRED"


def test_public_endpoints_bypass_authentication():
    """Verify that public endpoints (/health, /openapi.json) remain accessible without authentication."""
    app.dependency_overrides.pop(get_authentication_provider, None)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_authentication_error_returns_safe_response_without_traceback():
    """Verify authentication errors return safe error schema with zero internal leakage."""
    class FailingProvider:
        def authenticate(self, _request: Request):
            raise AuthenticationError("Internal database connection failed with sensitive password=secret")

    app.dependency_overrides[get_authentication_provider] = lambda: FailingProvider()
    try:
        with TestClient(app) as client:
            response = client.post("/query", json={"question": "List employees"})
        assert response.status_code == 401
        data = response.json()
        assert data["error_code"] == "AUTHENTICATION_REQUIRED"
        assert "password" not in response.text
        assert "secret" not in response.text
    finally:
        app.dependency_overrides.pop(get_authentication_provider, None)


# -----------------------------------------------------------------------------
# C. Header Spoofing Protection Across All 7 Fields
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("spoofed_header, spoofed_value, field_name, expected_value", [
    ("X-User-Id", "attacker", "user_id", "legit_user"),
    ("X-Role", "admin", "role", "viewer"),
    ("X-Tenant-Id", "TENANT_EVIL", "tenant_id", "TENANT_SAFE"),
    ("X-Employee-Id", "EMP_ATTACKER", "employee_id", "EMP_LEGIT"),
    ("X-Department-Id", "DEPT_FORBIDDEN", "department_id", "DEPT_ALLOWED"),
    ("X-Branch-Id", "BRANCH_UNAUTHORIZED", "branch_id", "BRANCH_AUTHORIZED"),
    ("X-Team-Id", "TEAM_MALICIOUS", "team_id", "TEAM_SAFE"),
])
def test_client_headers_cannot_override_authenticated_identity(
    spoofed_header: str, spoofed_value: str, field_name: str, expected_value: str,
):
    """Verify that none of the seven X-* headers supplied by an untrusted client
    can override the identity established by the AuthenticationProvider.
    """
    trusted_identity = AuthenticatedIdentity(
        user_id="legit_user",
        role="viewer",
        tenant_id="TENANT_SAFE",
        employee_id="EMP_LEGIT",
        department_id="DEPT_ALLOWED",
        branch_id="BRANCH_AUTHORIZED",
        team_id="TEAM_SAFE",
    )

    app.dependency_overrides[get_authentication_provider] = lambda: MockAuthenticationProvider(trusted_identity)
    captured_context: list[UserContext] = []

    from src.api.routes import query as query_module
    original_pipeline = query_module.pipeline

    class CapturePipeline:
        def execute(self, question: str, user_context: UserContext, reference_date=None):
            captured_context.append(user_context)
            return original_pipeline.execute(question, user_context, reference_date=reference_date)

    query_module.pipeline = CapturePipeline()

    try:
        with TestClient(app) as client:
            client.post(
                "/query",
                json={"question": "co bao nhieu nhan vien"},
                headers={spoofed_header: spoofed_value},
            )

        assert len(captured_context) == 1, "Pipeline was not called"
        actual_value = getattr(captured_context[0], field_name)
        assert actual_value == expected_value, (
            f"SPOOFING VULNERABILITY: Header {spoofed_header}={spoofed_value} "
            f"overrode authenticated {field_name} (got {actual_value}, expected {expected_value})"
        )
    finally:
        query_module.pipeline = original_pipeline
        app.dependency_overrides.pop(get_authentication_provider, None)


# -----------------------------------------------------------------------------
# D. Request Body Spoofing Protection
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"question": "co bao nhieu nhan vien", "user": {"user_id": "attacker", "role": "admin"}},
    {"question": "co bao nhieu nhan vien", "role": "admin"},
    {"question": "co bao nhieu nhan vien", "department_id": "D1"},
])
def test_request_body_cannot_inject_identity(payload: dict):
    """Verify that JSON request body extra fields are forbidden and cannot inject identity."""
    trusted_identity = AuthenticatedIdentity(user_id="legit_user", role="viewer", tenant_id="T1")
    app.dependency_overrides[get_authentication_provider] = lambda: MockAuthenticationProvider(trusted_identity)

    try:
        with TestClient(app) as client:
            response = client.post("/query", json=payload)
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_authentication_provider, None)


# -----------------------------------------------------------------------------
# E. Critical Security Scenarios: End-to-End Scope Isolation Under Attack
# -----------------------------------------------------------------------------

def test_critical_scenario_hr_identity_immune_to_admin_and_sales_headers():
    """Scenario: Caller is authenticated as HR Manager (dept D2).
    Attacker supplies headers claiming to be admin and Sales department (D1).
    Guarantee: Authenticated identity remains HR Manager (D2); attacker headers are ignored.
    """
    hr_identity = AuthenticatedIdentity(
        user_id="hr_mgr_user",
        role="department_manager",
        tenant_id="T1",
        department_id="D2",
    )

    app.dependency_overrides[get_authentication_provider] = lambda: MockAuthenticationProvider(hr_identity)
    captured_context: list[UserContext] = []

    from src.api.routes import query as query_module
    original_pipeline = query_module.pipeline

    class CapturePipeline:
        def execute(self, question: str, user_context: UserContext, reference_date=None):
            captured_context.append(user_context)
            return original_pipeline.execute(question, user_context, reference_date=reference_date)

    query_module.pipeline = CapturePipeline()

    try:
        with TestClient(app) as client:
            client.post(
                "/query",
                json={"question": "co bao nhieu nhan vien"},
                headers={
                    "X-User-Id": "attacker",
                    "X-Role": "admin",
                    "X-Tenant-Id": "T2",
                    "X-Department-Id": "D1",
                },
            )

        assert len(captured_context) == 1
        resolved_user = captured_context[0]

        assert resolved_user.user_id == "hr_mgr_user"
        assert resolved_user.role == "department_manager"
        assert resolved_user.tenant_id == "T1"
        assert resolved_user.department_id == "D2"
    finally:
        query_module.pipeline = original_pipeline
        app.dependency_overrides.pop(get_authentication_provider, None)


def test_critical_scenario_sales_identity_immune_to_hr_department_header():
    """Scenario: Caller is authenticated as Sales Manager (dept D1).
    Attacker supplies header X-Department-Id: D2 (HR).
    Guarantee: Authenticated identity remains Sales Manager (dept D1).
    """
    sales_identity = AuthenticatedIdentity(
        user_id="sales_mgr_user",
        role="department_manager",
        tenant_id="T1",
        department_id="D1",
    )

    app.dependency_overrides[get_authentication_provider] = lambda: MockAuthenticationProvider(sales_identity)
    captured_context: list[UserContext] = []

    from src.api.routes import query as query_module
    original_pipeline = query_module.pipeline

    class CapturePipeline:
        def execute(self, question: str, user_context: UserContext, reference_date=None):
            captured_context.append(user_context)
            return original_pipeline.execute(question, user_context, reference_date=reference_date)

    query_module.pipeline = CapturePipeline()

    try:
        with TestClient(app) as client:
            client.post(
                "/query",
                json={"question": "co bao nhieu nhan vien"},
                headers={"X-Department-Id": "D2"},
            )

        assert len(captured_context) == 1
        resolved_user = captured_context[0]

        assert resolved_user.user_id == "sales_mgr_user"
        assert resolved_user.department_id == "D1"
    finally:
        query_module.pipeline = original_pipeline
        app.dependency_overrides.pop(get_authentication_provider, None)

"""Authentication domain models."""

from pydantic import BaseModel, ConfigDict, Field

from src.shared.models import UserContext


class AuthenticatedIdentity(BaseModel):
    """Immutable trusted identity produced by an AuthenticationProvider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str = Field(min_length=1)
    username: str | None = None
    role: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    employee_id: str | None = None
    department_id: str | None = None
    branch_id: str | None = None
    team_id: str | None = None

    def to_user_context(self) -> UserContext:
        """Convert trusted identity into authorization UserContext."""
        return UserContext(
            user_id=self.user_id,
            username=self.username,
            role=self.role,
            tenant_id=self.tenant_id,
            employee_id=self.employee_id,
            department_id=self.department_id,
            branch_id=self.branch_id,
            team_id=self.team_id,
        )

    def to_headers(self) -> dict[str, str]:
        """Serialize trusted identity to canonical seven integration headers."""
        headers = {
            "X-User-Id": self.user_id,
            "X-Role": self.role,
            "X-Tenant-Id": self.tenant_id,
        }
        if self.employee_id is not None:
            headers["X-Employee-Id"] = self.employee_id
        if self.department_id is not None:
            headers["X-Department-Id"] = self.department_id
        if self.branch_id is not None:
            headers["X-Branch-Id"] = self.branch_id
        if self.team_id is not None:
            headers["X-Team-Id"] = self.team_id
        return headers


class LoginRequest(BaseModel):
    """Payload for POST /auth/login."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=100, description="Username or user identifier")
    password: str = Field(min_length=1, max_length=200, description="User password")


class RefreshRequest(BaseModel):
    """Payload for POST /auth/refresh."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1, description="Valid refresh token")


class LogoutRequest(BaseModel):
    """Payload for POST /auth/logout."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1, description="Refresh token for the current session")


class TokenResponse(BaseModel):
    """Token response returned upon successful login or token refresh."""

    access_token: str = Field(description="Bearer access token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(description="Access token expiration window in seconds")
    refresh_token: str | None = Field(default=None, description="Bearer refresh token")


class IdentityResponse(BaseModel):
    """Public identity payload returned by GET /auth/me."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1)
    username: str | None = None
    role: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    employee_id: str | None = None
    department_id: str | None = None
    branch_id: str | None = None
    team_id: str | None = None

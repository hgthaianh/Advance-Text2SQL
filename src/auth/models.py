"""Authentication domain models."""

from pydantic import BaseModel, ConfigDict, Field

from src.shared.models import UserContext


class AuthenticatedIdentity(BaseModel):
    """Immutable trusted identity produced by an AuthenticationProvider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str = Field(min_length=1)
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

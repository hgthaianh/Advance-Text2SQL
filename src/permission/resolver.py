from abc import ABC, abstractmethod

from src.permission.models import PermissionContext
from src.permission.row_policy import ScopedRowPolicy
from src.shared.enums import PermissionScope
from src.shared.models import UserContext


class PermissionResolver(ABC):
    @abstractmethod
    def resolve(self, user: UserContext) -> PermissionContext:
        """Resolve UserContext into PermissionContext with table allowlists and row scope filters."""
        raise NotImplementedError


class DefaultPermissionResolver(PermissionResolver):
    """Default implementation resolving UserContext to PermissionContext according to role and scope."""

    def resolve(self, user: UserContext) -> PermissionContext:
        role = user.role.lower()

        # Admin / Global superuser access
        if role in ("admin", "global", "superuser"):
            return PermissionContext(
                allowed_tables=["*"],
                allowed_columns={"*": ["*"]},
                row_filters=[],
                scope=PermissionScope.GLOBAL,
            )

        # Determine scope from role or user context hierarchy
        if role in ("branch_manager", "branch_admin") or (user.branch_id and not user.department_id):
            scope = PermissionScope.BRANCH
        elif role in ("department_manager", "hr_manager", "hr") or user.department_id:
            scope = PermissionScope.DEPARTMENT
        elif role in ("team_lead", "supervisor") or user.team_id:
            scope = PermissionScope.TEAM
        elif role in ("employee", "self"):
            scope = PermissionScope.SELF
        else:
            scope = PermissionScope.GLOBAL

        scope_filters = ScopedRowPolicy.create_scope_filters(user, scope)

        # Column allowlists by role (HR/Payroll can view sensitive salary/PII data)
        if role in ("hr", "hr_manager", "payroll_admin", "admin"):
            allowed_cols = {"*": ["*"]}
        else:
            # General users have standard columns allowed; sensitive columns remain restricted
            allowed_cols = {"*": ["id", "name", "department_id", "branch_id", "team_id", "title", "role", "email"]}

        return PermissionContext(
            allowed_tables=["*"],
            allowed_columns=allowed_cols,
            row_filters=scope_filters,
            scope=scope,
        )


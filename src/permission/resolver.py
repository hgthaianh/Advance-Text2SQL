from abc import ABC, abstractmethod

from src.permission.models import PermissionContext
from src.permission.row_policy import ScopedRowPolicy
from src.schema.registry import SchemaRegistry
from src.shared.enums import PermissionScope
from src.shared.exceptions import PermissionDeniedError
from src.shared.models import UserContext


class PermissionResolver(ABC):
    @abstractmethod
    def resolve(self, user: UserContext) -> PermissionContext:
        """Resolve UserContext into PermissionContext with table allowlists and row scope filters."""
        raise NotImplementedError


class DefaultPermissionResolver(PermissionResolver):
    """Default implementation resolving UserContext to PermissionContext according to role and scope."""

    def __init__(self, registry: SchemaRegistry | None = None, *, require_tenant: bool = False) -> None:
        self.registry = registry
        self.require_tenant = require_tenant

    def _self_scope_column(self) -> str:
        """Resolve the employee primary key from metadata, never from user text."""
        if self.registry is None:
            return 'employee_id'  # Backward-compatible standalone policy behavior.
        tables = [name for name, table in self.registry.tables.items() if table.name == 'employees']
        if len(tables) != 1:
            raise PermissionDeniedError('SELF scope requires exactly one employees table')
        keys = [f'{tables[0]}.{column.name}' for column in self.registry.tables[tables[0]].columns if column.primary_key]
        if len(keys) != 1:
            raise PermissionDeniedError('SELF scope requires an employee primary key')
        return keys[0]

    def resolve(self, user: UserContext) -> PermissionContext:
        role = user.role.lower()
        if self.require_tenant and not user.tenant_id:
            raise PermissionDeniedError('A tenant identity is required')

        # Admin / Global superuser access
        if role in ("admin", "global", "superuser"):
            return PermissionContext(
                allowed_tables=["*"],
                allowed_columns={"*": ["*"]},
                row_filters=[],
                scope=PermissionScope.GLOBAL,
                tenant_id=user.tenant_id,
            )

        # Scope comes from a recognized role, never optional request context.
        # A missing scoped identity must deny rather than widening to tenant data.
        if role in ("branch_manager", "branch_admin"):
            scope = PermissionScope.BRANCH
            required_identity = user.branch_id
            identity_name = 'branch'
        elif role in ("department_manager", "hr_manager", "hr"):
            scope = PermissionScope.DEPARTMENT
            required_identity = user.department_id
            identity_name = 'department'
        elif role in ("team_lead", "supervisor"):
            scope = PermissionScope.TEAM
            required_identity = user.team_id
            identity_name = 'team'
        elif role in ("employee", "self"):
            scope = PermissionScope.SELF
            required_identity = user.employee_id
            identity_name = 'employee'
        elif role in ('viewer', 'analyst', 'read_only', 'payroll_admin'):
            scope = PermissionScope.GLOBAL
            required_identity = True
            identity_name = ''
        else:
            raise PermissionDeniedError('Unrecognized role')
        if not required_identity:
            raise PermissionDeniedError(f'{identity_name.capitalize()} identity is required for {scope.value} scope')

        scope_filters = ScopedRowPolicy.create_scope_filters(
            user, scope, self_column=self._self_scope_column() if scope == PermissionScope.SELF else 'employee_id',
        )

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
            tenant_id=user.tenant_id,
        )


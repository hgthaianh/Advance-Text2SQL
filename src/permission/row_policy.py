from abc import ABC, abstractmethod

from src.permission.models import PermissionContext
from src.query.ir import QueryFilter, QueryIR
from src.shared.enums import FilterOperator, PermissionScope
from src.shared.models import UserContext


class RowPolicy(ABC):
    @abstractmethod
    def apply(self, query: QueryIR, permissions: PermissionContext) -> QueryIR:
        """Inject row-scope permissions into QueryIR before SQL compilation."""
        raise NotImplementedError


class ScopedRowPolicy(RowPolicy):
    """Injects GLOBAL, BRANCH, DEPARTMENT, TEAM, and SELF row scope filters into QueryIR."""

    def apply(self, query: QueryIR, permissions: PermissionContext) -> QueryIR:
        """Inject row permission filters from PermissionContext into query.filters before compilation."""
        if not permissions.row_filters:
            return query

        merged_filters = list(query.filters)
        for rf in permissions.row_filters:
            if rf not in merged_filters:
                merged_filters.append(rf.model_copy())

        return query.model_copy(update={"filters": merged_filters})

    @staticmethod
    def create_scope_filters(user: UserContext, scope: PermissionScope | str) -> list[QueryFilter]:
        """Generate row filters matching user identity and assigned scope."""
        scope_str = str(scope).upper()

        if scope_str == PermissionScope.GLOBAL.value:
            return []

        filters: list[QueryFilter] = []

        if scope_str == PermissionScope.BRANCH.value:
            if user.branch_id:
                filters.append(QueryFilter(column="branch_id", operator=FilterOperator.EQ, value=user.branch_id, source="permission"))
        elif scope_str == PermissionScope.DEPARTMENT.value:
            if user.department_id:
                filters.append(QueryFilter(column="department_id", operator=FilterOperator.EQ, value=user.department_id, source="permission"))
        elif scope_str == PermissionScope.TEAM.value:
            if user.team_id:
                filters.append(QueryFilter(column="team_id", operator=FilterOperator.EQ, value=user.team_id, source="permission"))
        elif scope_str == PermissionScope.SELF.value:
            if user.user_id:
                filters.append(QueryFilter(column="employee_id", operator=FilterOperator.EQ, value=user.user_id, source="permission"))

        return filters


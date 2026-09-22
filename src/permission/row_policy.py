from abc import ABC, abstractmethod

from src.permission.models import PermissionContext
from src.query.ir import QueryFilter, QueryIR
from src.schema.registry import SchemaRegistry
from src.schema.relationship_graph import RelationshipGraph
from src.shared.enums import FilterOperator, PermissionScope
from src.shared.exceptions import AmbiguousJoinPathError, JoinPathNotFoundError, PermissionDeniedError
from src.shared.models import UserContext


class RowPolicy(ABC):
    @abstractmethod
    def apply(self, query: QueryIR, permissions: PermissionContext, *, tables: list[str] | None = None) -> QueryIR:
        """Inject row-scope permissions into QueryIR before SQL compilation."""
        raise NotImplementedError


class ScopedRowPolicy(RowPolicy):
    """Injects GLOBAL, BRANCH, DEPARTMENT, TEAM, and SELF row scope filters into QueryIR."""

    def __init__(self, registry: SchemaRegistry | None = None, graph: RelationshipGraph | None = None) -> None:
        self.registry = registry
        self.graph = graph or (RelationshipGraph(registry) if registry else None)

    def apply(self, query: QueryIR, permissions: PermissionContext, *, tables: list[str] | None = None) -> QueryIR:
        """Inject row permission filters from PermissionContext into query.filters before compilation."""
        if not permissions.row_filters and not permissions.tenant_id:
            return query

        merged_filters = list(query.filters)
        for rf in permissions.row_filters:
            for resolved in self._resolve_filter(rf, query, tables):
                if resolved not in merged_filters:
                    merged_filters.append(resolved)

        if permissions.tenant_id and self.registry:
            for table in self._context_tables(query, tables):
                column = f'{table}.tenant_id'
                if column in self.registry.columns:
                    val = int(permissions.tenant_id) if isinstance(permissions.tenant_id, str) and permissions.tenant_id.isdigit() else permissions.tenant_id
                    tenant_filter = QueryFilter(
                        column=column, operator=FilterOperator.EQ, value=val, source='permission',
                    )
                    if tenant_filter not in merged_filters:
                        merged_filters.append(tenant_filter)

        return query.model_copy(update={"filters": merged_filters})

    def _context_tables(self, query: QueryIR, tables: list[str] | None) -> list[str]:
        if not self.registry:
            return list(tables or query.tables)
        references = list(tables or query.tables)
        for item in [*query.select, *query.group_by, *query.order_by]:
            column = item if isinstance(item, str) else item.column
            if column and '.' in column and column != '*':
                references.append(column.rsplit('.', 1)[0])
        for item in [*query.filters, *query.having]:
            if '.' in item.column:
                references.append(item.column.rsplit('.', 1)[0])
        return list(dict.fromkeys(self.registry.resolve_table(table) for table in references))

    def _resolve_filter(self, filter_: QueryFilter, query: QueryIR, tables: list[str] | None) -> list[QueryFilter]:
        if not self.registry or filter_.source != 'permission' or '.' in filter_.column:
            return [filter_.model_copy()]
        context = self._context_tables(query, tables)
        direct = [f'{table}.{filter_.column}' for table in context if f'{table}.{filter_.column}' in self.registry.columns]
        if direct:
            return [filter_.model_copy(update={'column': column}) for column in direct]

        root = context[0] if context else None
        if not root or not self.graph:
            raise PermissionDeniedError(f'Cannot apply scope column {filter_.column}')
        choices: list[tuple[int, str]] = []
        for table in self.registry.tables:
            column = f'{table}.{filter_.column}'
            if column not in self.registry.columns:
                continue
            try:
                distance = len(self.graph.find_relationship_path(root, table))
            except (JoinPathNotFoundError, AmbiguousJoinPathError):
                continue
            choices.append((distance, column))
        if not choices:
            raise PermissionDeniedError(f'Cannot apply scope column {filter_.column}')
        minimum = min(distance for distance, _ in choices)
        nearest = [column for distance, column in choices if distance == minimum]
        if len(nearest) != 1:
            raise PermissionDeniedError(f'Ambiguous scope column {filter_.column}')
        return [filter_.model_copy(update={'column': nearest[0]})]

    @staticmethod
    def create_scope_filters(user: UserContext, scope: PermissionScope | str, *, self_column: str = "employee_id") -> list[QueryFilter]:
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
            identity = user.employee_id or user.user_id
            if identity:
                filters.append(QueryFilter(column=self_column, operator=FilterOperator.EQ, value=identity, source="permission"))

        return filters


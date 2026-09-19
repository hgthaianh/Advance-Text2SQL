from abc import ABC, abstractmethod

from src.permission.models import PermissionContext
from src.query.ir import QueryIR
from src.query.join_resolver import FKJoinResolver, JoinResolver
from src.schema.relationship_graph import FKRelationshipGraph, RelationshipGraph
from src.shared.exceptions import PermissionDeniedError


class QueryPlanner(ABC):
    @abstractmethod
    def plan(self, query: QueryIR, permissions: PermissionContext) -> QueryIR:
        """Plan a QueryIR query by validating permissions and resolving join paths."""
        raise NotImplementedError


class FKQueryPlanner(QueryPlanner):
    """Query planner that resolves foreign key joins, injects row permissions, and checks table access."""

    def __init__(
        self,
        graph: RelationshipGraph | None = None,
        resolver: JoinResolver | None = None,
    ) -> None:
        self.graph = graph or FKRelationshipGraph()
        self.resolver = resolver or FKJoinResolver()

    def plan(self, query: QueryIR, permissions: PermissionContext) -> QueryIR:
        """Validate permissions and generate a complete execution plan with resolved joins."""
        # 1. Gather all requested table names from query fields
        requested_tables: list[str] = list(query.tables)

        for col_str in query.select + query.group_by:
            if "." in col_str:
                table_name = col_str.split(".")[0]
                if table_name not in requested_tables:
                    requested_tables.append(table_name)

        for qf in query.filters:
            if "." in qf.column:
                table_name = qf.column.split(".")[0]
                if table_name not in requested_tables:
                    requested_tables.append(table_name)

        for ob in query.order_by:
            if "." in ob.column:
                table_name = ob.column.split(".")[0]
                if table_name not in requested_tables:
                    requested_tables.append(table_name)

        # 2. Check permission allowlist
        allowed_tables = permissions.allowed_tables
        is_all_allowed = "*" in allowed_tables

        if requested_tables:
            if not allowed_tables and not is_all_allowed:
                raise PermissionDeniedError("Empty allowlist denies table access.")

            if not is_all_allowed:
                for tbl in requested_tables:
                    if tbl not in allowed_tables:
                        raise PermissionDeniedError(f"Access denied to table '{tbl}'.")

        # 3. Resolve join paths and intermediate tables
        if hasattr(self.resolver, "resolve_with_intermediate_tables"):
            all_tables, joins = self.resolver.resolve_with_intermediate_tables(requested_tables, self.graph)
        else:
            all_tables = list(requested_tables)
            joins = self.resolver.resolve(requested_tables, self.graph)

        # Check permission for intermediate tables if applicable
        if not is_all_allowed and allowed_tables:
            for tbl in all_tables:
                if tbl not in allowed_tables:
                    raise PermissionDeniedError(f"Access denied to intermediate table '{tbl}'.")

        # 4. Inject row filters from permission context
        merged_filters = list(query.filters)
        for rf in permissions.row_filters:
            if rf not in merged_filters:
                merged_filters.append(rf)

        # 5. Return updated QueryIR copy
        return query.model_copy(
            update={
                "tables": all_tables,
                "joins": joins,
                "filters": merged_filters,
            }
        )


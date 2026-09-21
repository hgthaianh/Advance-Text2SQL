"""Validate and canonicalize QueryIR without NLP or permission decisions."""

from pydantic import ValidationError

from src.permission.models import PermissionContext
from src.shared.exceptions import PermissionDeniedError
from src.schema.relationship_graph import FKRelationshipGraph
from src.query.join_resolver import FKJoinResolver

from src.config import get_settings
from src.query.ir import OrderBy, QueryFilter, QueryIR, SelectItem
from src.query.join_resolver import JoinResolver
from src.query.models import QueryPlan
from src.schema.registry import SchemaRegistry
from src.schema.relationship_graph import RelationshipGraph
from src.shared.enums import Aggregation, FilterOperator, Intent
from src.shared.models import SchemaRelationship
from src.shared.exceptions import (
    FanoutRiskError, MalformedQueryIRError, MissingAggregationColumnError, UnsupportedIntentError,
)

_LOOKUP = {Intent.LOOKUP, Intent.SELECT, Intent.LIST, Intent.DETAIL, Intent.FILTER, Intent.TOP_N}
_AGGREGATES = {Intent.COUNT: Aggregation.COUNT, Intent.SUM: Aggregation.SUM,
               Intent.AVG: Aggregation.AVG, Intent.AVERAGE: Aggregation.AVG}
_NUMERIC_TYPES = {'smallint', 'integer', 'int', 'bigint', 'numeric', 'decimal', 'real',
                  'float', 'double precision', 'money', 'int2', 'int4', 'int8', 'float4', 'float8'}


class QueryPlanner:
    def __init__(self, registry: SchemaRegistry, graph: RelationshipGraph | None = None, *, max_limit: int | None = None) -> None:
        self.registry = registry
        self.max_limit = get_settings().query_max_limit if max_limit is None else max_limit
        if type(self.max_limit) is not int or not 1 <= self.max_limit <= 1000:
            raise MalformedQueryIRError('max_limit must be an integer between 1 and 1000')
        self.graph = graph if graph is not None else RelationshipGraph(registry)
        if self.graph.registry is not registry:
            raise MalformedQueryIRError('Planner and graph must use the same registry')
        self.join_resolver = JoinResolver()

    def _validate_aggregate(self, column: str, aggregation: Aggregation) -> None:
        if column == '*':
            if aggregation != Aggregation.COUNT:
                raise MissingAggregationColumnError('SUM/AVG require a column')
            return
        if aggregation in (Aggregation.SUM, Aggregation.AVG):
            data_type = self.registry.columns[column].data_type.lower().split('(')[0].strip()
            if data_type not in _NUMERIC_TYPES:
                raise MalformedQueryIRError(f'Non-numeric aggregation column: {column}')

    @staticmethod
    def _validate_filter(condition: QueryFilter) -> None:
        if condition.operator == FilterOperator.IN:
            if not isinstance(condition.value, list) or any(v is None for v in condition.value):
                raise MalformedQueryIRError('IN requires a list of non-null scalar values')
        elif isinstance(condition.value, list):
            raise MalformedQueryIRError('Only IN accepts a list value')
        elif condition.value is None and condition.operator not in (FilterOperator.EQ, FilterOperator.NE):
            raise MalformedQueryIRError('NULL is supported only with = or !=')
        elif condition.operator == FilterOperator.LIKE and not isinstance(condition.value, str):
            raise MalformedQueryIRError('LIKE requires a string')

    def _validate_fanout_risk(self, selected: list[SelectItem], joins: list) -> None:
        """Reject a SUM/AVG after any one-to-many expansion in its join tree.

        COUNT(*) deliberately retains documented SQL row semantics. An explicit
        aggregate of a parent table column is unsafe once a joined FK child can
        repeat that parent row. The IR has no DISTINCT or pre-aggregation
        construct yet, so the only result-preserving behavior is to fail.
        """
        aggregate_owners = {
            item.column.rsplit('.', 1)[0]
            for item in selected
            if item.aggregation in (Aggregation.SUM, Aggregation.AVG)
            and item.column not in (None, '*')
        }
        if not aggregate_owners:
            return
        physical = {
            frozenset((relation.source, relation.target)): relation
            for relation in self.registry.relationships
        }
        adjacency: dict[str, list[tuple[str, SchemaRelationship]]] = {}
        for join in joins:
            relation = physical.get(frozenset((join.source, join.target)))
            if relation is None:
                continue
            child, parent = relation.source.rsplit('.', 1)[0], relation.target.rsplit('.', 1)[0]
            adjacency.setdefault(child, []).append((parent, relation))
            adjacency.setdefault(parent, []).append((child, relation))
        for owner in aggregate_owners:
            seen, queue = {owner}, [owner]
            while queue:
                current = queue.pop()
                for neighbor, relation in adjacency.get(current, []):
                    if neighbor in seen:
                        continue
                    child, parent = relation.source.rsplit('.', 1)[0], relation.target.rsplit('.', 1)[0]
                    if current == parent and neighbor == child:
                        raise FanoutRiskError(
                            f'Aggregate on {owner} may be duplicated by child table {child}; '
                            'use a pre-aggregate or a future COUNT(DISTINCT ...) query shape'
                        )
                    seen.add(neighbor)
                    queue.append(neighbor)

    def plan(self, query: QueryIR | dict) -> QueryPlan:
        """Resolve identifiers, aggregate semantics, filters and unique FK paths.

        Qualified column references can add owner tables (including injected
        filters). Bare columns resolve only against explicitly requested tables.
        Permission filters must already be present in query.filters.
        """
        if not isinstance(query, (QueryIR, dict)):
            raise MalformedQueryIRError('Expected QueryIR or a query mapping')
        try:
            # Revalidate model instances too: callers can mutate or model_construct.
            validated = QueryIR.model_validate(query.model_dump() if isinstance(query, QueryIR) else query)
        except ValidationError as exc:
            raise MalformedQueryIRError(str(exc)) from exc
        if validated.intent not in _LOOKUP | _AGGREGATES.keys():
            raise UnsupportedIntentError(f'Unsupported planning intent: {validated.intent}')
        requested = list(dict.fromkeys(self.registry.resolve_table(t) for t in validated.tables))
        if not requested:
            raise MalformedQueryIRError('An explicit base table is required')
        tables = list(requested)

        def resolve(column: str) -> str:
            name = self.registry.resolve_column(column, requested)
            owner = name.rsplit('.', 1)[0]
            if owner not in tables:
                tables.append(owner)
            return name

        groups = list(dict.fromkeys(resolve(c) for c in validated.group_by))
        expected_aggregation = _AGGREGATES.get(validated.intent)
        raw_select = validated.select
        if not raw_select:
            if expected_aggregation == Aggregation.COUNT:
                raw_select = [SelectItem(column='*', aggregation=Aggregation.COUNT)]
            elif expected_aggregation:
                raise MissingAggregationColumnError('SUM/AVG require a selected numeric column')
            else:
                raise MalformedQueryIRError('LOOKUP/FILTER require explicit selected columns')
        selected = []
        for raw in raw_select:
            item = SelectItem(column=raw) if isinstance(raw, str) else raw.model_copy()
            if item.column not in (None, '*'):
                item.column = resolve(item.column)
            if item.aggregation is None and item.column not in groups:
                item.aggregation = expected_aggregation
            if expected_aggregation and item.aggregation not in (None, expected_aggregation):
                raise MalformedQueryIRError('Select aggregation conflicts with query intent')
            if not item.column or item.column == '*':
                if item.aggregation == Aggregation.COUNT:
                    item.column = '*'
                elif item.aggregation:
                    raise MissingAggregationColumnError('SUM/AVG require a column')
                else:
                    raise MalformedQueryIRError('Wildcard projection is supported only as COUNT(*)')
            if item.aggregation:
                self._validate_aggregate(item.column, item.aggregation)
            selected.append(item)
        aggregate_query = any(s.aggregation for s in selected)
        if expected_aggregation and not aggregate_query:
            raise MissingAggregationColumnError('An aggregate projection is required')
        if aggregate_query and any(s.aggregation is None and s.column not in groups for s in selected):
            raise MalformedQueryIRError('Non-aggregate projections must appear in group_by')
        if not aggregate_query and groups:
            raise MalformedQueryIRError('group_by requires an aggregate query')
        filters = []
        for original in validated.filters:
            condition = original.model_copy(update={'column': resolve(original.column)})
            self._validate_filter(condition)
            filters.append(condition)
        having = []
        for original in validated.having:
            if not aggregate_query or original.operator == FilterOperator.LIKE:
                raise MalformedQueryIRError('HAVING requires aggregate comparisons without LIKE')
            column = '*' if original.column == '*' else resolve(original.column)
            self._validate_aggregate(column, original.aggregation)
            condition = original.model_copy(update={'column': column})
            self._validate_filter(condition)
            having.append(condition)
        order = []
        for item in validated.order_by:
            column = '*' if item.column == '*' and item.aggregation else resolve(item.column)
            aggregation = item.aggregation
            if aggregation:
                if not aggregate_query:
                    raise MalformedQueryIRError('Aggregate ordering requires an aggregate query')
                self._validate_aggregate(column, aggregation)
            elif aggregate_query and column not in groups:
                matching = [s for s in selected if s.column == column and s.aggregation]
                if len(matching) != 1:
                    raise MalformedQueryIRError('Aggregate ordering requires a group key or one aggregate projection')
                aggregation = matching[0].aggregation
            order.append(item.model_copy(update={'column': column, 'aggregation': aggregation}))
        top_query = validated.top_k is not None or validated.intent == Intent.TOP_N
        if top_query:
            if not order or (aggregate_query and not groups):
                raise MalformedQueryIRError('TOP-K requires ORDER BY and grouped or row results')
            # All group keys break aggregate ties without inventing a business key.
            ordered_groups = {o.column for o in order if o.aggregation is None}
            order.extend(OrderBy(column=c) for c in groups if c not in ordered_groups)
        if not aggregate_query or top_query:
            effective_limit = validated.top_k if validated.top_k is not None else validated.limit
            if effective_limit > self.max_limit:
                raise MalformedQueryIRError(f'LIMIT exceeds configured maximum {self.max_limit}')
        normalized = validated.model_copy(update={
            'tables': tables, 'select': selected, 'filters': filters, 'having': having,
            'group_by': groups, 'order_by': order,
        })
        relationship_keys = {frozenset((relation.source, relation.target)) for relation in self.registry.relationships}
        hints = {
            frozenset((self.registry.resolve_column(source), self.registry.resolve_column(target)))
            for source, target in validated.join_hints
        }
        if not hints <= relationship_keys:
            raise MalformedQueryIRError('Join hints must name schema foreign-key endpoints')
        joins = self.join_resolver.resolve(tables, self.graph, hints)
        if validated.joins:
            supplied = {frozenset((self.registry.resolve_column(a), self.registry.resolve_column(b)))
                        for a, b in validated.joins}
            expected = {frozenset((j.source, j.target)) for j in joins}
            if supplied != expected:
                raise MalformedQueryIRError('Supplied joins differ from validated FK paths')
        self._validate_fanout_risk(selected, joins)
        physical_tables = [tables[0]] + [j.target.rsplit('.', 1)[0] for j in joins]
        return QueryPlan(query=normalized, tables=physical_tables, joins=joins)




class FKQueryPlanner:
    """Legacy permission adapter preserved from dev; QueryPlanner remains policy-free."""

    def __init__(
        self,
        graph: RelationshipGraph | FKRelationshipGraph | None = None,
        resolver: JoinResolver | FKJoinResolver | None = None,
    ) -> None:
        self.graph = graph or FKRelationshipGraph()
        self.resolver = resolver or FKJoinResolver()

    def plan(self, query: QueryIR, permissions: PermissionContext) -> QueryIR:
        """Validate permissions and generate a complete execution plan with resolved joins."""
        # Include injected filters before discovering tables and checking access.
        query = query.model_copy(deep=True)
        for row_filter in permissions.row_filters:
            if row_filter not in query.filters:
                query.filters.append(row_filter)
        # 1. Gather all requested table names from query fields
        requested_tables: list[str] = list(query.tables)

        for projection in query.select + query.group_by:
            col_str = projection if isinstance(projection, str) else projection.column
            if not col_str or col_str == '*':
                continue
            if "." in col_str:
                table_name = col_str.rsplit(".", 1)[0]
                if table_name not in requested_tables:
                    requested_tables.append(table_name)

        for qf in query.filters + query.having:
            if "." in qf.column:
                table_name = qf.column.rsplit(".", 1)[0]
                if table_name not in requested_tables:
                    requested_tables.append(table_name)

        for ob in query.order_by:
            if "." in ob.column:
                table_name = ob.column.rsplit(".", 1)[0]
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


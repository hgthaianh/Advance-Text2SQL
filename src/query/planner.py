"""Validate and canonicalize QueryIR without NLP or permission decisions."""

from pydantic import ValidationError

from src.permission.models import PermissionContext
from src.shared.exceptions import PermissionDeniedError
from src.schema.relationship_graph import FKRelationshipGraph
from src.query.join_resolver import FKJoinResolver

from src.config import get_settings
from src.query.ir import (
    ColumnRef, InstancePermissionFilter, InstanceQueryFilter, OrderBy,
    QueryFilter, QueryIR, SelectItem,
)
from src.query.join_resolver import JoinResolver
from src.query.models import InstanceJoin, QueryPlan
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

    def _validate_fanout_risk(self, query: QueryIR, selected: list[SelectItem], joins: list) -> str | None:
        """Protect aggregates and select the narrow safe pre-aggregation shape.

        COUNT(*) deliberately retains documented SQL row semantics. An explicit
        aggregate of a parent table column is unsafe once a joined FK child can
        repeat that parent row. For COUNT(parent.col), DISTINCT is applied to eliminate
        fan-out duplicates. SUM/AVG cannot be safely converted to DISTINCT and fail closed.
        """
        aggregate_items = [
            item
            for item in selected
            if item.aggregation in (Aggregation.SUM, Aggregation.AVG, Aggregation.COUNT)
            and item.column not in (None, '*')
        ]
        if not aggregate_items:
            return None
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

        def _has_child_fanout(owner: str) -> tuple[bool, str | None]:
            seen, queue = {owner}, [owner]
            while queue:
                current = queue.pop()
                for neighbor, relation in adjacency.get(current, []):
                    if neighbor in seen:
                        continue
                    child, parent = relation.source.rsplit('.', 1)[0], relation.target.rsplit('.', 1)[0]
                    if current == parent and neighbor == child:
                        return True, child
                    seen.add(neighbor)
                    queue.append(neighbor)
            return False, None

        pre_aggregate_owner = None
        for item in aggregate_items:
            owner = item.column.rsplit('.', 1)[0]
            has_fanout, child = _has_child_fanout(owner)
            if has_fanout:
                if item.aggregation == Aggregation.COUNT:
                    item.distinct = True
                elif self._can_pre_aggregate(query, selected, joins, item):
                    pre_aggregate_owner = owner
                else:
                    raise FanoutRiskError(
                        f'Aggregate on {owner} may be duplicated by child table {child}; '
                        'use a pre-aggregate or a future COUNT(DISTINCT ...) query shape'
                    )
        return pre_aggregate_owner

    def _can_pre_aggregate(self, query: QueryIR, selected: list[SelectItem], joins: list,
                           item: SelectItem) -> bool:
        """Allow a scalar or owner/dimension-grouped parent-grain SUM/AVG.

        A dimension is safe only when it is the owner itself or lies on a
        many-to-one path from the owner.  A child dimension would create an
        ambiguous allocation of the owner's value, so it remains fail-closed.
        """
        if item.aggregation not in (Aggregation.SUM, Aggregation.AVG) or item.distinct:
            return False
        aggregate_items = [selected_item for selected_item in selected if selected_item.aggregation]
        if len(aggregate_items) != 1 or aggregate_items[0] is not item:
            return False
        if query.having or query.order_by or query.top_k is not None or query.intent == Intent.TOP_N:
            return False
        if any(getattr(join, 'is_outer', False) for join in joins):
            return False
        owner = item.column.rsplit('.', 1)[0]
        if not any(column.primary_key for column in self.registry.tables[owner].columns):
            return False
        group_columns = [selected_item.column for selected_item in selected if not selected_item.aggregation]
        # ``selected`` is canonicalized above while QueryIR may still carry
        # short references, so use the already-validated projection shape.
        if len(group_columns) != len(query.group_by):
            return False
        return all(self._is_owner_grain_dimension(owner, column.rsplit('.', 1)[0], joins)
                   for column in group_columns)

    def _is_owner_grain_dimension(self, owner: str, dimension: str, joins: list) -> bool:
        """Return whether a dimension has at most one value for each owner row."""
        if owner == dimension:
            return True
        adjacency: dict[str, list[str]] = {}
        for join in joins:
            relation = next((candidate for candidate in self.registry.relationships
                             if {candidate.source, candidate.target} == {join.source, join.target}), None)
            if relation is None:
                continue
            child = relation.source.rsplit('.', 1)[0]
            parent = relation.target.rsplit('.', 1)[0]
            adjacency.setdefault(child, []).append(parent)
        seen, queue = {owner}, [owner]
        while queue:
            current = queue.pop(0)
            for parent in adjacency.get(current, []):
                if parent == dimension:
                    return True
                if parent not in seen:
                    seen.add(parent)
                    queue.append(parent)
        return False

    def _validate_instance_joins(self, query: QueryIR) -> list[InstanceJoin]:
        """Validate only the fixed employee_instance -> manager_instance self-FK.

        This deliberately bypasses neither RelationshipGraph nor its legacy
        physical-table de-duplication.  The self relationship is identified by
        explicit logical-instance evidence and then verified against registry
        metadata; no role or shortest-path inference is performed.
        """
        if not query.instance_joins:
            return []
        instances = {instance.instance_id: instance for instance in query.table_instances}
        expected_source = 'employee_instance'
        expected_target = 'manager_instance'
        expected_source_column = 'manager_id'
        expected_target_column = 'id'
        relationship = next((
            relation for relation in self.registry.relationships
            if relation.source == 'hr.employees.manager_id' and relation.target == 'hr.employees.id'
        ), None)
        if relationship is None:
            raise MalformedQueryIRError('Registered employee-manager foreign key is required')

        validated: list[InstanceJoin] = []
        for requested in query.instance_joins:
            source = instances.get(requested.source_instance_id)
            target = instances.get(requested.target_instance_id)
            if source is None or target is None:
                raise MalformedQueryIRError('Logical instance join references an unknown table instance')
            if source.instance_id == target.instance_id:
                raise MalformedQueryIRError('Logical instance join requires distinct source and target instances')
            if source.physical_table != 'hr.employees' or target.physical_table != 'hr.employees':
                raise MalformedQueryIRError('Logical employee-manager instances must resolve to hr.employees')
            # Resolve before checking the allowlisted FK shape so malformed or
            # forged columns cannot be mistaken for relationship evidence.
            query.resolve_instance_column(
                ColumnRef(instance_id=source.instance_id, column=requested.source_column), self.registry,
            )
            query.resolve_instance_column(
                ColumnRef(instance_id=target.instance_id, column=requested.target_column), self.registry,
            )
            if (
                requested.source_instance_id != expected_source
                or requested.target_instance_id != expected_target
                or requested.source_column != expected_source_column
                or requested.target_column != expected_target_column
                or requested.join_type != 'left'
            ):
                raise MalformedQueryIRError('Only employee_instance.manager_id -> manager_instance.id LEFT join is supported')
            validated.append(InstanceJoin(
                source_instance_id=expected_source,
                source_column=expected_source_column,
                target_instance_id=expected_target,
                target_column=expected_target_column,
                join_type='left',
                relationship=relationship,
            ))
        return validated

    def _validate_instance_permission_filters(
        self, query: QueryIR,
    ) -> tuple[list[InstancePermissionFilter], dict[str, list[InstancePermissionFilter]]]:
        """Validate policy-created filter placement without assigning SQL aliases.

        The fixed logical join has one base employee and one optional manager.
        Therefore manager filters are never accepted in the base collection.
        This is the representation Phase 2C will compile into LEFT JOIN ON.
        """
        if not query.instance_joins:
            if query.instance_base_filters or query.instance_join_filters:
                raise MalformedQueryIRError('Logical instance permission filters require an instance join')
            return [], {}

        def validate_filter(filter_: InstancePermissionFilter, expected_instance: str) -> None:
            if filter_.source != 'permission' or filter_.instance_id != expected_instance:
                raise MalformedQueryIRError('Logical permission filter has an invalid instance or source')
            query.resolve_instance_column(
                ColumnRef(instance_id=filter_.instance_id, column=filter_.column), self.registry,
            )
            self._validate_filter(QueryFilter(
                column=filter_.column, operator=filter_.operator, value=filter_.value, source='permission',
            ))

        for filter_ in query.instance_base_filters:
            validate_filter(filter_, 'employee_instance')
        if set(query.instance_join_filters) - {'manager_instance'}:
            raise MalformedQueryIRError('Logical join permission filters may target only manager_instance')
        join_filters = query.instance_join_filters.get('manager_instance', [])
        for filter_ in join_filters:
            validate_filter(filter_, 'manager_instance')
        return list(query.instance_base_filters), {'manager_instance': list(join_filters)}

    def _validate_instance_filters(self, query: QueryIR) -> list[InstanceQueryFilter]:
        if not query.instance_filters:
            return []
        if not query.instance_joins:
            raise MalformedQueryIRError('Logical instance filters require an instance join')
        validated: list[InstanceQueryFilter] = []
        for filter_ in query.instance_filters:
            if filter_.source != 'user':
                raise MalformedQueryIRError('Logical instance user filter has an invalid source')
            query.resolve_instance_column(
                ColumnRef(instance_id=filter_.instance_id, column=filter_.column), self.registry,
            )
            self._validate_filter(QueryFilter(
                column=filter_.column, operator=filter_.operator, value=filter_.value, source='user',
            ))
            if filter_ not in validated:
                validated.append(filter_)
        return validated

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
        instance_joins = self._validate_instance_joins(validated)
        instance_base_filters, instance_join_filters = self._validate_instance_permission_filters(validated)
        instance_filters = self._validate_instance_filters(validated)
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
        time_group = validated.time_group
        if time_group is not None:
            time_group = time_group.model_copy(update={'column': resolve(time_group.column)})
        expected_aggregation = _AGGREGATES.get(validated.intent)
        raw_select = validated.select
        if instance_joins:
            # Phase 2C compiles only the explicitly instance-qualified lookup
            # shape. Refuse mixed legacy fields rather than silently omitting a
            # physical projection/filter or inventing aggregate semantics.
            if any(isinstance(item, ColumnRef) for item in raw_select) and any(
                not isinstance(item, ColumnRef) for item in raw_select
            ):
                raise MalformedQueryIRError(
                    'Instance-aware projections cannot mix logical and legacy columns'
                )
        if not raw_select:
            if expected_aggregation == Aggregation.COUNT:
                raw_select = [SelectItem(column='*', aggregation=Aggregation.COUNT)]
            elif expected_aggregation:
                raise MissingAggregationColumnError('SUM/AVG require a selected numeric column')
            else:
                raise MalformedQueryIRError('LOOKUP/FILTER require explicit selected columns')
        selected = []
        instance_select: list[ColumnRef] = []
        for raw in raw_select:
            if isinstance(raw, ColumnRef):
                item = SelectItem(column=validated.resolve_instance_column(raw, self.registry))
                instance_select.append(raw)
            else:
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
        if time_group is not None and not aggregate_query:
            raise MalformedQueryIRError('Time grouping requires an aggregate projection')
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
        if validated.outer_joins:
            outer_set = {self.registry.resolve_table(t) for t in validated.outer_joins}
            destinations = {join.target.rsplit('.', 1)[0] for join in joins}
            if not outer_set <= destinations:
                raise MalformedQueryIRError(
                    'Outer joined tables must be non-root destinations of the validated join path'
                )
            joins = [
                j.model_copy(update={'is_outer': True}) if j.target.rsplit('.', 1)[0] in outer_set else j
                for j in joins
            ]
        else:
            outer_set = set()
        outer_join_filters: dict[str, list[QueryFilter]] = {}
        for table, conditions in validated.outer_join_filters.items():
            canonical_table = self.registry.resolve_table(table)
            if canonical_table not in outer_set:
                raise MalformedQueryIRError('Outer join filters require a validated outer-joined table')
            normalized_conditions = []
            for original in conditions:
                if original.source != 'permission':
                    raise MalformedQueryIRError('Only permission filters may be scoped to an outer join')
                condition = original.model_copy(update={'column': resolve(original.column)})
                if condition.column.rsplit('.', 1)[0] != canonical_table:
                    raise MalformedQueryIRError('Outer join filter must belong to its destination table')
                self._validate_filter(condition)
                normalized_conditions.append(condition)
            outer_join_filters[canonical_table] = normalized_conditions
        pre_aggregate_owner = self._validate_fanout_risk(validated, selected, joins)
        normalized = validated.model_copy(update={
            'tables': tables,
            # Keep logical projections for the instance compiler. Legacy
            # queries retain the canonical SelectItem representation.
            'select': validated.select if instance_joins else selected, 'filters': filters, 'having': having,
            'group_by': groups, 'order_by': order,
            'time_group': time_group,
            'outer_joins': sorted(outer_set), 'outer_join_filters': outer_join_filters,
        })
        physical_tables = [tables[0]] + [j.target.rsplit('.', 1)[0] for j in joins]
        return QueryPlan(
            query=normalized, tables=physical_tables, joins=joins,
            instance_joins=instance_joins,
            instance_base_filters=instance_base_filters,
            instance_join_filters=instance_join_filters,
            instance_select=instance_select,
            instance_filters=instance_filters,
            pre_aggregate_owner=pre_aggregate_owner,
        )




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


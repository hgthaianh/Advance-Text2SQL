"""Compile a validated plan to parameterized PostgreSQL using SQLAlchemy Core."""

from sqlalchemy import and_, bindparam, column, false, func, select, table
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import quoted_name
from sqlalchemy.sql.elements import BindParameter, ColumnElement

from src.query.ir import ColumnRef, InstancePermissionFilter, InstanceQueryFilter, QueryFilter, Scalar, SelectItem
from src.query.models import CompiledQuery, QueryPlan
from src.query.planner import QueryPlanner
from src.schema.registry import SchemaRegistry
from src.schema.relationship_graph import RelationshipGraph
from src.shared.enums import Aggregation, FilterOperator, Intent, SortDirection
from src.shared.exceptions import MalformedQueryIRError


class SQLBuilder:
    def __init__(self, registry: SchemaRegistry, graph: RelationshipGraph | None = None, *, max_limit: int | None = None) -> None:
        self.registry = registry
        self.planner = QueryPlanner(registry, graph, max_limit=max_limit)

    def build(self, plan: QueryPlan) -> CompiledQuery:
        """Validate the public plan boundary and bind every user-supplied value.

        Replanning rejects altered identifiers, aggregates and forged FK joins.
        It performs no DB access and never consults permission decisions.
        """
        if not isinstance(plan, QueryPlan):
            raise MalformedQueryIRError('SQLBuilder expects a QueryPlan')
        canonical = self.planner.plan(plan.query)
        if (plan.tables != canonical.tables or plan.joins != canonical.joins
                or plan.instance_joins != canonical.instance_joins
                or plan.instance_base_filters != canonical.instance_base_filters
                or plan.instance_join_filters != canonical.instance_join_filters
                or plan.instance_select != canonical.instance_select
                or plan.instance_filters != canonical.instance_filters):
            raise MalformedQueryIRError('Plan tables/joins differ from validated schema paths')
        if canonical.instance_joins:
            if canonical.pre_aggregate_owner:
                raise MalformedQueryIRError('Pre-aggregation with instance-aware joins is not supported')
            return self._compile_instance_plan(canonical)
        return self._compile_plan(canonical)

    def _compile_instance_plan(self, plan: QueryPlan) -> CompiledQuery:
        """Compile only the planner-validated employee-to-manager LEFT join.

        Alias names are constants selected from validated instance IDs.  This
        path never accepts SQL aliases, physical tables, or relationships from
        a caller and leaves the legacy compiler untouched.
        """
        if len(plan.instance_joins) != 1:
            raise MalformedQueryIRError('Exactly one validated instance join is required')
        if (
            any(not isinstance(item, ColumnRef) for item in plan.query.select)
            or plan.query.filters or plan.query.having or plan.query.group_by
            or plan.query.order_by or plan.query.top_k is not None
            or plan.query.intent != Intent.LOOKUP
        ):
            raise MalformedQueryIRError(
                'Instance-aware SQL requires instance-qualified LOOKUP projections and filters only'
            )
        join = plan.instance_joins[0]
        if (
            join.source_instance_id != 'employee_instance'
            or join.target_instance_id != 'manager_instance'
            or join.source_column != 'manager_id'
            or join.target_column != 'id'
            or join.join_type != 'left'
            or join.relationship.source != 'hr.employees.manager_id'
            or join.relationship.target != 'hr.employees.id'
        ):
            raise MalformedQueryIRError('Unsupported validated instance join')
        instances = {instance.instance_id: instance for instance in plan.query.table_instances}
        if set(instances) != {'employee_instance', 'manager_instance'}:
            raise MalformedQueryIRError('Validated employee-manager instances are required')
        if any(instance.physical_table != 'hr.employees' for instance in instances.values()):
            raise MalformedQueryIRError('Logical instances have an invalid physical table')

        metadata = self.registry.tables.get('hr.employees')
        if metadata is None:
            raise MalformedQueryIRError('Employee table metadata is required')
        base = table(
            quoted_name(metadata.name, True),
            *(column(quoted_name(c.name, True)) for c in metadata.columns),
            schema=quoted_name(metadata.schema_name, True),
        )
        aliases = {
            'employee_instance': base.alias(quoted_name('employee', True)),
            'manager_instance': base.alias(quoted_name('manager', True)),
        }

        def instance_column(instance_id: str, field: str) -> ColumnElement:
            if instance_id not in aliases:
                raise MalformedQueryIRError('Unknown validated logical instance')
            physical = plan.query.resolve_instance_column(
                ColumnRef(instance_id=instance_id, column=field), self.registry,
            )
            return aliases[instance_id].c[physical.rsplit('.', 1)[1]]

        counter = 0

        def parameter(value: Scalar) -> BindParameter:
            nonlocal counter
            result = bindparam(f'p{counter}', value=value)
            counter += 1
            return result

        comparisons = {
            FilterOperator.EQ: lambda c, v: c == v,
            FilterOperator.NE: lambda c, v: c != v,
            FilterOperator.GT: lambda c, v: c > v,
            FilterOperator.GTE: lambda c, v: c >= v,
            FilterOperator.LT: lambda c, v: c < v,
            FilterOperator.LTE: lambda c, v: c <= v,
            FilterOperator.LIKE: lambda c, v: c.like(v),
        }

        def predicate(filter_: InstancePermissionFilter | InstanceQueryFilter) -> ColumnElement:
            expression = instance_column(filter_.instance_id, filter_.column)
            if filter_.operator == FilterOperator.IN:
                if not isinstance(filter_.value, list):
                    raise MalformedQueryIRError('Validated IN filter requires a list')
                return expression.in_([parameter(value) for value in filter_.value]) if filter_.value else false()
            if filter_.value is None:
                return expression.is_(None) if filter_.operator == FilterOperator.EQ else expression.is_not(None)
            if isinstance(filter_.value, list):
                raise MalformedQueryIRError('Only IN accepts a list')
            return comparisons[filter_.operator](expression, parameter(filter_.value))

        on_conditions = [
            instance_column(join.source_instance_id, join.source_column)
            == instance_column(join.target_instance_id, join.target_column),
        ]
        for filter_ in plan.instance_join_filters.get('manager_instance', []):
            if filter_.instance_id != 'manager_instance' or filter_.source != 'permission':
                raise MalformedQueryIRError('Invalid manager join-scoped permission filter')
            on_conditions.append(predicate(filter_))
        from_clause = aliases['employee_instance'].join(
            aliases['manager_instance'], and_(*on_conditions), isouter=True,
        )

        if not plan.instance_select:
            raise MalformedQueryIRError('Instance-aware queries require instance-qualified projections')
        projections = [
            instance_column(reference.instance_id, reference.column).label(
                f'{reference.instance_id.removesuffix("_instance")}_{reference.column}'
            )
            for reference in plan.instance_select
        ]
        statement = select(*projections).select_from(from_clause)
        for filter_ in plan.instance_base_filters:
            if filter_.instance_id != 'employee_instance' or filter_.source != 'permission':
                raise MalformedQueryIRError('Invalid employee base permission filter')
            statement = statement.where(predicate(filter_))
        for filter_ in plan.instance_filters:
            # User filters retain normal WHERE semantics. A manager-side user
            # filter intentionally filters result rows; only permission scope
            # is protected from changing LEFT JOIN semantics via ON placement.
            statement = statement.where(predicate(filter_))
        statement = statement.limit(bindparam('limit', value=plan.query.limit))
        compiled = statement.compile(dialect=postgresql.dialect(paramstyle='named'))
        return CompiledQuery(sql=str(compiled), params=dict(compiled.params), statement=statement)

    def _compile_plan(self, plan: QueryPlan) -> CompiledQuery:
        query = plan.query
        sql_tables = {}
        for name in plan.tables:
            metadata = self.registry.tables[name]
            sql_tables[name] = table(
                quoted_name(metadata.name, True),
                *(column(quoted_name(c.name, True)) for c in metadata.columns),
                schema=quoted_name(metadata.schema_name, True),
            )

        def sql_column(name: str) -> ColumnElement:
            owner, field = name.rsplit('.', 1)
            return sql_tables[owner].c[field]

        aggregation_functions = {Aggregation.COUNT: func.count, Aggregation.SUM: func.sum, Aggregation.AVG: func.avg}
        def sql_expression(name: str, aggregation: Aggregation | None = None, distinct: bool = False) -> ColumnElement:
            if name == '*':
                return func.count()
            expression = sql_column(name)
            if not aggregation:
                return expression
            fn = aggregation_functions[aggregation]
            return fn(expression.distinct()) if distinct else fn(expression)

        projections = [
            sql_expression(
                item if isinstance(item, str) else (item.column or '*'),
                item.aggregation if isinstance(item, SelectItem) else None,
                item.distinct if isinstance(item, SelectItem) else False,
            )
            for item in query.select
        ]
        join_specs = []
        for edge in plan.joins:
            destination = edge.target.rsplit('.', 1)[0]
            target_table = sql_tables[destination]
            is_outer = getattr(edge, 'is_outer', False)
            if getattr(edge, 'target_role', None):
                role = edge.target_role
                if role not in sql_tables:
                    sql_tables[role] = sql_tables[destination].alias(quoted_name(role, True))
                target_table = sql_tables[role]
                target_col = target_table.c[edge.target.rsplit('.', 1)[1]]
                source_col = sql_column(edge.source)
                condition = (source_col == target_col)
            elif getattr(edge, 'composite_keys', None):
                condition = and_(*(sql_column(s) == sql_column(t) for s, t in edge.composite_keys))
            else:
                condition = (sql_column(edge.source) == sql_column(edge.target))
            join_specs.append((destination, target_table, condition, is_outer))
        counter = 0

        def _cast_value_if_needed(val: Scalar, col_name: str | None) -> Scalar:
            if col_name and self.registry and col_name in self.registry.columns:
                dt = self.registry.columns[col_name].data_type.lower()
                if any(t in dt for t in ('int', 'bigint', 'smallint')) and isinstance(val, str) and val.isdigit():
                    return int(val)
            return val

        def parameter(value: Scalar, col_name: str | None = None) -> BindParameter:
            nonlocal counter
            typed_val = _cast_value_if_needed(value, col_name)
            result = bindparam(f'p{counter}', value=typed_val)
            counter += 1
            return result

        comparisons = {
            FilterOperator.EQ: lambda c, v: c == v,
            FilterOperator.NE: lambda c, v: c != v,
            FilterOperator.GT: lambda c, v: c > v,
            FilterOperator.GTE: lambda c, v: c >= v,
            FilterOperator.LT: lambda c, v: c < v,
            FilterOperator.LTE: lambda c, v: c <= v,
            FilterOperator.LIKE: lambda c, v: c.like(v),
        }
        def predicate(condition: QueryFilter, expression: ColumnElement) -> ColumnElement:
            col_name = condition.column
            if condition.operator == FilterOperator.IN:
                if isinstance(condition.value, list):
                    return expression.in_([parameter(v, col_name) for v in condition.value]) if condition.value else false()
                return false()
            if condition.value is None:
                return expression.is_(None) if condition.operator == FilterOperator.EQ else expression.is_not(None)
            val: Scalar = condition.value if not isinstance(condition.value, list) else None
            return comparisons[condition.operator](expression, parameter(val, col_name))

        from_clause = sql_tables[plan.tables[0]]
        for destination, target_table, condition, is_outer in join_specs:
            if is_outer:
                conditions = [condition]
                conditions.extend(
                    predicate(filter_, sql_column(filter_.column))
                    for filter_ in query.outer_join_filters.get(destination, [])
                )
                condition = and_(*conditions)
            from_clause = from_clause.join(target_table, condition, isouter=is_outer)

        if plan.pre_aggregate_owner:
            # Keep every validated join and every user/permission filter in the
            # inner query. Grouping at the aggregate owner's PK grain removes
            # only one-to-many duplicates before the outer SUM/AVG.
            aggregate_items = [item for item in query.select
                               if isinstance(item, SelectItem) and item.aggregation]
            if len(aggregate_items) != 1 or not aggregate_items[0].column:
                raise MalformedQueryIRError('Invalid pre-aggregation plan')
            item = aggregate_items[0]
            primary_keys = [
                sql_column(f'{plan.pre_aggregate_owner}.{column.name}').label(f'_preagg_key_{index}')
                for index, column in enumerate(self.registry.tables[plan.pre_aggregate_owner].columns)
                if column.primary_key
            ]
            group_columns = [
                sql_column(group).label(f'_preagg_group_{index}')
                for index, group in enumerate(query.group_by)
            ]
            value = sql_column(item.column).label('_preagg_value')
            inner = select(*primary_keys, *group_columns, value).select_from(from_clause)
            for condition in query.filters:
                inner = inner.where(predicate(condition, sql_column(condition.column)))
            inner = inner.group_by(*primary_keys, *group_columns, value)
            derived = inner.subquery('pre_aggregated')
            aggregate = aggregation_functions[item.aggregation](derived.c._preagg_value)
            if group_columns:
                outer_groups = [getattr(derived.c, f'_preagg_group_{index}')
                                for index in range(len(group_columns))]
                statement = select(*outer_groups, aggregate).select_from(derived).group_by(*outer_groups)
            else:
                statement = select(aggregate).select_from(derived)
        else:
            statement = select(*projections).select_from(from_clause)
            for condition in query.filters:
                statement = statement.where(predicate(condition, sql_column(condition.column)))
            if query.group_by:
                statement = statement.group_by(*(sql_column(c) for c in query.group_by))
            for condition in query.having:
                statement = statement.having(predicate(condition, sql_expression(condition.column, condition.aggregation)))
            for item in query.order_by:
                expression = sql_expression(item.column, item.aggregation)
                statement = statement.order_by(expression.desc() if item.direction == SortDirection.DESC else expression.asc())
        # Explicit aggregate TOP-K limits groups only, after aggregation/HAVING.
        has_aggregation = any(isinstance(item, SelectItem) and item.aggregation for item in query.select)
        if not has_aggregation or query.top_k is not None or query.intent == Intent.TOP_N:
            statement = statement.limit(bindparam('limit', value=query.top_k if query.top_k is not None else query.limit))
        compiled = statement.compile(dialect=postgresql.dialect(paramstyle='named'))
        return CompiledQuery(sql=str(compiled), params=dict(compiled.params), statement=statement)

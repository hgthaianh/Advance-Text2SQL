"""Compile a validated plan to parameterized PostgreSQL using SQLAlchemy Core."""

from sqlalchemy import and_, bindparam, column, false, func, select, table
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import quoted_name
from sqlalchemy.sql.elements import BindParameter, ColumnElement

from src.query.ir import QueryFilter, Scalar, SelectItem
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
        if plan.tables != canonical.tables or plan.joins != canonical.joins:
            raise MalformedQueryIRError('Plan tables/joins differ from validated schema paths')
        return self._compile_plan(canonical)

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
        from_clause = sql_tables[plan.tables[0]]
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
            from_clause = from_clause.join(target_table, condition, isouter=is_outer)
        statement = select(*projections).select_from(from_clause)
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

"""Compile a validated plan to parameterized PostgreSQL using SQLAlchemy Core."""

from sqlalchemy import bindparam, column, false, func, select, table
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import quoted_name
from sqlalchemy.sql.elements import BindParameter, ColumnElement

from src.query.ir import QueryFilter, Scalar
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
        query = canonical.query
        sql_tables = {}
        for name in canonical.tables:
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
        def sql_expression(name: str, aggregation: Aggregation | None = None) -> ColumnElement:
            if name == '*':
                return func.count()
            expression = sql_column(name)
            return aggregation_functions[aggregation](expression) if aggregation else expression

        projections = [sql_expression(item.column, item.aggregation) for item in query.select]
        from_clause = sql_tables[canonical.tables[0]]
        for edge in canonical.joins:
            destination = edge.target.rsplit('.', 1)[0]
            from_clause = from_clause.join(sql_tables[destination], sql_column(edge.source) == sql_column(edge.target))
        statement = select(*projections).select_from(from_clause)
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
        def predicate(condition: QueryFilter, expression: ColumnElement) -> ColumnElement:
            if condition.operator == FilterOperator.IN:
                return expression.in_([parameter(v) for v in condition.value]) if condition.value else false()
            if condition.value is None:
                return expression.is_(None) if condition.operator == FilterOperator.EQ else expression.is_not(None)
            return comparisons[condition.operator](expression, parameter(condition.value))

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
        if not any(item.aggregation for item in query.select) or query.top_k is not None or query.intent == Intent.TOP_N:
            statement = statement.limit(bindparam('limit', value=query.top_k if query.top_k is not None else query.limit))
        compiled = statement.compile(dialect=postgresql.dialect(paramstyle='named'))
        return CompiledQuery(sql=str(compiled), params=dict(compiled.params), statement=statement)

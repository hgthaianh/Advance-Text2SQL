"""Compile a validated plan to parameterized PostgreSQL using SQLAlchemy Core."""

from sqlalchemy import bindparam, column, false, func, select, table
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import quoted_name
from sqlalchemy.sql.elements import BindParameter, ColumnElement

from src.query.ir import Scalar
from src.query.models import CompiledQuery, QueryPlan
from src.query.planner import QueryPlanner
from src.schema.registry import SchemaRegistry
from src.schema.relationship_graph import RelationshipGraph
from src.shared.enums import Aggregation, FilterOperator, SortDirection
from src.shared.exceptions import MalformedQueryIRError


class SQLBuilder:
    def __init__(self, registry: SchemaRegistry, graph: RelationshipGraph | None = None) -> None:
        self.registry = registry
        self.planner = QueryPlanner(registry, graph)

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
        projections = []
        aggregates = {}
        for item in query.select:
            if item.column == '*':
                expression = func.count()
            else:
                expression = sql_column(item.column)
                if item.aggregation:
                    expression = aggregation_functions[item.aggregation](expression)
            projections.append(expression)
            if item.aggregation:
                aggregates[item.column] = expression
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
        for condition in query.filters:
            expression = sql_column(condition.column)
            if condition.operator == FilterOperator.IN:
                predicate = expression.in_([parameter(v) for v in condition.value]) if condition.value else false()
            elif condition.value is None:
                predicate = expression.is_(None) if condition.operator == FilterOperator.EQ else expression.is_not(None)
            else:
                predicate = comparisons[condition.operator](expression, parameter(condition.value))
            statement = statement.where(predicate)
        if query.group_by:
            statement = statement.group_by(*(sql_column(c) for c in query.group_by))
        for item in query.order_by:
            expression = sql_column(item.column) if item.column in query.group_by or item.column not in aggregates else aggregates[item.column]
            statement = statement.order_by(expression.desc() if item.direction == SortDirection.DESC else expression.asc())
        # Aggregate projections consume the full filtered/joined input. The v1
        # row limit applies only to nonaggregate results, including SELECT aliases.
        if not aggregates:
            statement = statement.limit(bindparam('limit', value=query.limit))
        compiled = statement.compile(dialect=postgresql.dialect(paramstyle='named'))
        return CompiledQuery(sql=str(compiled), params=dict(compiled.params), statement=statement)

"""Grouped queries share the basic pipeline; comparisons compile two plans."""

from pydantic import ValidationError

from src.query.handlers.basic import BasicQueryHandler
from src.query.ir import PeriodComparison, QueryFilter
from src.query.models import CompiledPeriodQuery
from src.shared.enums import Intent
from src.shared.exceptions import MalformedQueryIRError


class ComplexQueryHandler(BasicQueryHandler):
    """Compile structured aggregates, HAVING and TOP-K without executing SQL."""

    def compare(self, comparison: PeriodComparison | dict) -> list[CompiledPeriodQuery]:
        """Apply two half-open intervals to the same ungrouped aggregate IR.

        All existing filters, including permission filters, are retained in both
        plans. Computing differences/ratios belongs to the result consumer.
        """
        if not isinstance(comparison, (PeriodComparison, dict)):
            raise MalformedQueryIRError('Expected PeriodComparison or a mapping')
        try:
            request = PeriodComparison.model_validate(
                comparison.model_dump() if isinstance(comparison, PeriodComparison) else comparison
            )
        except ValidationError as exc:
            raise MalformedQueryIRError(str(exc)) from exc
        query = self.planner.plan(request.query).query
        if (not any(item.aggregation for item in query.select) or query.group_by
                or query.having or query.order_by or query.top_k is not None or query.intent == Intent.TOP_N):
            raise MalformedQueryIRError('Period comparison supports ungrouped aggregates without HAVING/ordering/TOP-K')
        registry = self.planner.registry
        column = registry.resolve_column(request.date_column, query.tables)
        data_type = registry.columns[column].data_type.lower().split('(')[0].strip()
        if data_type not in {'date', 'timestamp', 'timestamptz', 'timestamp without time zone', 'timestamp with time zone'}:
            raise MalformedQueryIRError('Comparison date_column must be a date or timestamp')
        results = []
        for period in request.periods:
            period_query = query.model_copy(deep=True)
            period_query.filters.extend([
                QueryFilter(column=column, operator='>=', value=period.start),
                QueryFilter(column=column, operator='<', value=period.end),
            ])
            results.append(CompiledPeriodQuery(period=period, query=self.handle(period_query)))
        return results

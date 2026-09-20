"""Deterministically turn observed NLP and schema evidence into validated QueryIR."""

from datetime import date
from decimal import Decimal
import re

from src.nlp.entity_extractor import ExtractedEntity
from src.query.ir import HavingCondition, OrderBy, QueryFilter, QueryIR, SelectItem
from src.schema.registry import SchemaRegistry
from src.shared.enums import Aggregation, FilterOperator, Intent, SortDirection
from src.shared.exceptions import AmbiguousQueryError, MissingEntityError, UnsupportedQueryError
from src.shared.models import PreprocessedQuery, SchemaLinkResult


_AGGREGATE_INTENTS = {Intent.REVENUE: Intent.SUM, Intent.AVERAGE: Intent.AVG}
_NUMERIC_NAMES = ('total_amount', 'amount', 'line_total', 'quantity', 'base_salary', 'price', 'cost')
_DATE_NAMES = ('order_date', 'invoice_date', 'payment_date', 'created_at', 'date')


class QueryIRBuilder:
    """Conservative builder which rejects ambiguity instead of inventing SQL."""

    def __init__(self, registry: SchemaRegistry) -> None:
        self.registry = registry

    def build(self, preprocessed: PreprocessedQuery, schema_links: SchemaLinkResult,
              entities: list[ExtractedEntity]) -> QueryIR:
        intent = _AGGREGATE_INTENTS.get(preprocessed.intent_result.intent, preprocessed.intent_result.intent)
        tables = self._tables(schema_links)
        if intent == Intent.TOP_N:
            # The linker marks transactional tables implied by "best-selling"
            # as related business concepts. They are necessary to aggregate a
            # product dimension, but remain below direct mention confidence.
            tables = list(dict.fromkeys(tables + [
                candidate.name.rsplit('.', 1)[0] for candidate in schema_links.columns if candidate.score >= 0.80
            ]))
        if intent in {Intent.UNKNOWN, Intent.DETAIL, Intent.LIST, Intent.SELECT, Intent.FILTER}:
            if not tables:
                raise UnsupportedQueryError('No supported schema concept was found')
            intent = Intent.LOOKUP
        if intent in {Intent.TREND, Intent.COMPARISON, Intent.AGGREGATE}:
            raise UnsupportedQueryError(f'Intent is not supported by the deterministic builder: {intent}')
        if not tables:
            raise UnsupportedQueryError('No supported table was found')

        aggregate = {Intent.SUM: Aggregation.SUM, Intent.AVG: Aggregation.AVG}.get(intent)
        if intent == Intent.COUNT:
            select: list[str | SelectItem] = []
        elif aggregate:
            value_column = self._value_column(schema_links, tables)
            select = [SelectItem(column=value_column, aggregation=aggregate)]
        elif intent == Intent.TOP_N:
            value_column = self._value_column(schema_links, tables)
            group_column = self._group_column(preprocessed, tables)
            select = [group_column, SelectItem(column=value_column, aggregation=Aggregation.SUM)]
        else:
            select = [self._lookup_column(schema_links, tables)]

        filters, tables = self._entity_filters(entities, tables)
        if preprocessed.date_range:
            anchor = next((item.column.rsplit('.', 1)[0] for item in select if isinstance(item, SelectItem) and item.column), None)
            date_column = self._date_column(tables, anchor)
            filters.extend((
                QueryFilter(column=date_column, operator=FilterOperator.GTE, value=preprocessed.date_range.start),
                QueryFilter(column=date_column, operator=FilterOperator.LT, value=preprocessed.date_range.end),
            ))

        group_by: list[str] = []
        order_by: list[OrderBy] = []
        top_k = None
        if intent == Intent.TOP_N:
            group_column = select[0]
            assert isinstance(group_column, str)
            group_by = [group_column]
            value_column = select[1]
            assert isinstance(value_column, SelectItem)
            order_by = [OrderBy(column=value_column.column, aggregation=Aggregation.SUM, direction=SortDirection.DESC)]
            top_k = self._top_k(preprocessed)
        elif aggregate and 'theo' in preprocessed.accent_stripped_text:
            group_column = self._group_column(preprocessed, tables)
            group_by = [group_column]
            select.insert(0, group_column)
            value_column = select[1]
            assert isinstance(value_column, SelectItem)
            order_by = [OrderBy(column=value_column.column, aggregation=aggregate, direction=SortDirection.DESC)]

        having_column = next((item.column for item in select if isinstance(item, SelectItem)), None)
        having = self._having(preprocessed, having_column,
                              aggregate or (Aggregation.SUM if intent == Intent.TOP_N else None), group_by)
        return QueryIR(intent=intent, tables=tables, select=select, filters=filters,
                       having=having, group_by=group_by, order_by=order_by, top_k=top_k)

    def _tables(self, links: SchemaLinkResult) -> list[str]:
        # Only strong semantic table evidence establishes the query context.
        # Weak identifier matches such as the English verb "list" are not tables.
        tables = [candidate.name for candidate in links.tables if candidate.score >= 0.90]
        if not tables:
            tables = [candidate.name.rsplit('.', 1)[0] for candidate in links.columns if candidate.score >= 0.90]
        return list(dict.fromkeys(tables))

    def _value_column(self, links: SchemaLinkResult, tables: list[str]) -> str:
        candidates = [c.name for c in links.columns if c.score >= 0.75 and c.name.rsplit('.', 1)[0] in tables]
        candidates = [c for c in candidates if self.registry.columns[c].name in _NUMERIC_NAMES]
        if not candidates:
            for table in tables:
                candidates.extend(f'{table}.{name}' for name in _NUMERIC_NAMES if f'{table}.{name}' in self.registry.columns)
        return self._one(candidates, 'numeric value column')

    def _lookup_column(self, links: SchemaLinkResult, tables: list[str]) -> str:
        candidates = [c.name for c in links.columns if c.score >= 0.75 and c.name.rsplit('.', 1)[0] in tables]
        if candidates:
            # Preserve an explicit sensitive request so column policy can deny it.
            # Falling back to ``id`` would turn a forbidden request into an
            # unrelated successful query and hide an authorization failure.
            return candidates[0]
        for table in tables:
            for name in ('name', 'code', 'id'):
                key = f'{table}.{name}'
                if key in self.registry.columns:
                    return key
        raise MissingEntityError('No safe display column is available')

    def _date_column(self, tables: list[str], anchor_table: str | None) -> str:
        ordered_tables = ([anchor_table] if anchor_table in tables else []) + [t for t in tables if t != anchor_table]
        for name in _DATE_NAMES:
            candidates = [f'{table}.{name}' for table in ordered_tables if f'{table}.{name}' in self.registry.columns]
            if len(candidates) == 1:
                return candidates[0]
            if anchor_table and f'{anchor_table}.{name}' in candidates:
                return f'{anchor_table}.{name}'
        raise MissingEntityError('Missing date column')

    def _group_column(self, preprocessed: PreprocessedQuery, tables: list[str]) -> str:
        text = preprocessed.accent_stripped_text
        preferred = []
        if 'chi nhanh' in text:
            preferred.append('org.branches.name')
        if 'khach hang' in text:
            preferred.append('customer.customers.name')
        if 'san pham' in text:
            preferred.append('product.products.name')
        candidates = [column for column in preferred if column in self.registry.columns]
        if not candidates:
            candidates = [f'{table}.name' for table in tables if f'{table}.name' in self.registry.columns]
        return self._one(candidates, 'grouping column')

    def _entity_filters(self, entities: list[ExtractedEntity], tables: list[str]) -> tuple[list[QueryFilter], list[str]]:
        filters: list[QueryFilter] = []
        all_tables = list(tables)
        targets = {
            'customer_type': 'customer.customers.customer_type',
            'department_name': 'org.departments.name',
            'department_code': 'org.departments.code',
            'branch_name': 'org.branches.name',
            'branch_code': 'org.branches.code',
            'product_code': 'product.products.code',
            'customer_code': 'customer.customers.code',
        }
        for entity in entities:
            target = targets.get(entity.entity_type)
            if entity.entity_type == 'status':
                status_columns = [f'{table}.status' for table in all_tables if f'{table}.status' in self.registry.columns]
                target = self._one(status_columns, 'status column')
            if not target:
                continue
            if target not in self.registry.columns:
                raise MissingEntityError(f'No schema field accepts {entity.entity_type}')
            owner = target.rsplit('.', 1)[0]
            if owner not in all_tables:
                all_tables.append(owner)
            filters.append(QueryFilter(column=target, operator=FilterOperator.EQ, value=entity.normalized_value))
        return filters, all_tables

    @staticmethod
    def _having(preprocessed: PreprocessedQuery, value_column: str | None,
                aggregation: Aggregation | None, group_by: list[str]) -> list[HavingCondition]:
        """Build one explicit aggregate threshold, only after a GROUP BY exists."""
        if not group_by or not value_column or not aggregation:
            return []
        text = preprocessed.accent_stripped_text
        match = re.search(r'\b(tren|lon hon|it nhat|tu|duoi|nho hon)\s+(\d+(?:[.,]\d+)?)\b', text)
        if not match:
            return []
        phrase, literal = match.groups()
        value = next((number.value for number in preprocessed.numbers if number.raw_text == literal), None)
        if value is None:
            value = Decimal(literal.replace(',', '.'))
        operator = {
            'tren': FilterOperator.GT, 'lon hon': FilterOperator.GT,
            'it nhat': FilterOperator.GTE, 'tu': FilterOperator.GTE,
            'duoi': FilterOperator.LT, 'nho hon': FilterOperator.LT,
        }[phrase]
        return [HavingCondition(column=value_column, aggregation=aggregation, operator=operator, value=value)]

    @staticmethod
    def _top_k(preprocessed: PreprocessedQuery) -> int:
        values = [int(number.value) for number in preprocessed.numbers if number.value == int(number.value) and number.value > 0]
        if len(values) != 1:
            raise MissingEntityError('TOP-K requires exactly one positive integer')
        return values[0]

    @staticmethod
    def _one(candidates: list[str], meaning: str) -> str:
        unique = list(dict.fromkeys(candidates))
        if not unique:
            raise MissingEntityError(f'Missing {meaning}')
        if len(unique) != 1:
            raise AmbiguousQueryError(f'Ambiguous {meaning}: {", ".join(unique)}')
        return unique[0]

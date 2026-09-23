"""Deterministically turn observed NLP and schema evidence into validated QueryIR."""

from datetime import date
from decimal import Decimal
import re
import unicodedata

from src.nlp.entity_extractor import ExtractedEntity
from src.query.ir import (
    ColumnRef, HavingCondition, InstanceJoinRequest, OrderBy, QueryFilter,
    QueryIR, SelectItem, TableInstance,
)
from src.schema.registry import SchemaRegistry
from src.schema.relationship_graph import RelationshipGraph
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
        self.graph = RelationshipGraph(registry)

    def build(self, preprocessed: PreprocessedQuery, schema_links: SchemaLinkResult,
              entities: list[ExtractedEntity]) -> QueryIR:
        employee_manager = self._employee_manager_query(preprocessed)
        if employee_manager is not None:
            return employee_manager
        intent = _AGGREGATE_INTENTS.get(preprocessed.intent_result.intent, preprocessed.intent_result.intent)
        tables = self._tables(schema_links)
        if intent == Intent.TOP_N:
            # The linker marks transactional tables implied by "best-selling"
            # as related business concepts. They are necessary to aggregate a
            # product dimension. Only numeric aggregate candidates are added:
            # dimensions and filters are resolved below from explicit grammar
            # and extracted entities.
            tables = list(dict.fromkeys(tables + [
                candidate.name.rsplit('.', 1)[0] for candidate in schema_links.columns
                if candidate.score >= 0.80
                and candidate.reason not in schema_links.ambiguous_concepts
                and self.registry.columns[candidate.name].name in _NUMERIC_NAMES
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
            group_owner = group_column.rsplit('.', 1)[0]
            if group_owner not in tables:
                tables.append(group_owner)
            select = [group_column, SelectItem(column=value_column, aggregation=Aggregation.SUM)]
        else:
            select = [self._lookup_column(schema_links, tables)]

        # JoinResolver builds a rooted tree. For an aggregate, root it at the
        # fact table that owns the measure so dimensions join outward from the
        # aggregate grain rather than through an unrelated shared dimension.
        aggregate_owner = next((item.column.rsplit('.', 1)[0] for item in select
                                if isinstance(item, SelectItem) and item.aggregation), None)
        if aggregate_owner in tables:
            tables = [aggregate_owner, *[table for table in tables if table != aggregate_owner]]

        filters, tables, join_hints = self._entity_filters(entities, tables, preprocessed)
        join_hints = list(dict.fromkeys([*schema_links.relationship_hints, *join_hints]))
        outer_joins, absence_filters, outer_roots = self._outer_join_semantics(preprocessed, schema_links, tables)
        if outer_roots:
            if len(outer_roots) != 1:
                raise AmbiguousQueryError('Absence language requires one preserved table')
            root = outer_roots[0]
            tables = [root, *[table for table in tables if table != root]]
        filters.extend(absence_filters)
        if preprocessed.date_range:
            anchor = next((item.column.rsplit('.', 1)[0] for item in select if isinstance(item, SelectItem) and item.column), None)
            date_column, date_owner = self._date_column(tables, anchor)
            if date_owner not in tables:
                tables.append(date_owner)
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
                       having=having, group_by=group_by, order_by=order_by, top_k=top_k,
                       join_hints=join_hints, outer_joins=outer_joins)

    def _outer_join_semantics(self, preprocessed: PreprocessedQuery, links: SchemaLinkResult,
                              tables: list[str]) -> tuple[list[str], list[QueryFilter], list[str]]:
        """Translate explicit absence language into a metadata-validated LEFT join.

        ``customers without orders`` means an anti-join and therefore adds an
        ``orders.id IS NULL`` predicate.  Inclusive wording such as ``bao gom
        ca`` / ``including`` retains matched rows as well, so it requests only
        the LEFT join.  The relationship direction comes exclusively from the
        registry; uncertain or multi-parent wording is left untouched.
        """
        text = preprocessed.accent_stripped_text
        inclusive = bool(re.search(r'\b(?:bao gom(?: ca)?|ke ca|including)\b', text))
        table_set = set(tables)
        targets: list[str] = []
        filters: list[QueryFilter] = []
        roots: list[str] = []
        for candidate in links.tables:
            child = candidate.name
            if child not in table_set:
                continue
            alias = self._accent_stripped(candidate.matched_text)
            if not alias or not re.search(
                rf'\b(?:chua co|khong co|without)\s+(?:bat ky\s+|any\s+)?{re.escape(alias)}\b', text,
            ):
                continue
            relationships = [
                relationship for relationship in self.registry.relationships
                if child in {relationship.source.rsplit('.', 1)[0], relationship.target.rsplit('.', 1)[0]}
                and (relationship.target if relationship.source.rsplit('.', 1)[0] == child
                     else relationship.source).rsplit('.', 1)[0] in table_set
            ]
            neighbors = {
                (relationship.target if relationship.source.rsplit('.', 1)[0] == child
                 else relationship.source).rsplit('.', 1)[0]
                for relationship in relationships
            }
            # A LEFT join only has a stable meaning when this phrase identifies
            # exactly one direct relationship under an already-mentioned root.
            if len(relationships) != 1 or len(neighbors) != 1:
                raise AmbiguousQueryError('Absence language requires one direct relationship between the mentioned tables')
            roots.append(next(iter(neighbors)))
            targets.append(child)
            if inclusive:
                continue
            primary_keys = [
                f'{child}.{column.name}' for column in self.registry.tables[child].columns
                if column.primary_key
            ]
            if len(primary_keys) != 1:
                continue
            filters.append(QueryFilter(
                column=primary_keys[0], operator=FilterOperator.EQ, value=None,
            ))
        return list(dict.fromkeys(targets)), filters, list(dict.fromkeys(roots))

    @staticmethod
    def _accent_stripped(value: str) -> str:
        normalized = unicodedata.normalize('NFD', value.lower()).replace('đ', 'd')
        return ''.join(char for char in normalized if not unicodedata.combining(char))

    def _employee_manager_query(self, preprocessed: PreprocessedQuery) -> QueryIR | None:
        """Recognize only explicit employee-with-their-manager relationship evidence.

        This is intentionally a small semantic whitelist, not a schema-linker
        or keyword heuristic.  It creates no SQL aliases and relies on the
        existing planner to verify the backend-owned self FK.
        """
        text = preprocessed.accent_stripped_text
        patterns = (
            r'\b(?:list|show)\s+(?:(?:each|all)\s+)?employees?\s+(?:and|with)\s+their\s+managers?\b',
            r'\bemployees?\s+(?:and|with)\s+their\s+managers?\b',
            r'\b(?:liet ke|danh sach)\s+(?:tung\s+)?nhan vien\s+(?:va|voi)\s+(?:quan ly|nguoi quan ly)\b',
        )
        if any(re.search(pattern, text) for pattern in patterns):
            required_relationship = ('hr.employees.manager_id', 'hr.employees.id')
            if (
                'hr.employees.full_name' not in self.registry.columns
                or not any((relationship.source, relationship.target) == required_relationship
                           for relationship in self.registry.relationships)
            ):
                raise UnsupportedQueryError('Employee-manager relationship is not available')
            return QueryIR(
                intent=Intent.LOOKUP,
                tables=['hr.employees'],
                select=[
                    ColumnRef(instance_id='employee_instance', column='full_name'),
                    ColumnRef(instance_id='manager_instance', column='full_name'),
                ],
                table_instances=[
                    TableInstance(instance_id='employee_instance', semantic_role='employee'),
                    TableInstance(instance_id='manager_instance', semantic_role='manager'),
                ],
                instance_joins=[InstanceJoinRequest(
                    source_instance_id='employee_instance', source_column='manager_id',
                    target_instance_id='manager_instance', target_column='id', join_type='left',
                )],
            )
        # ``manager`` alone is deliberately insufficient evidence for one of
        # several schema relationships.  Reject rather than guessing a role.
        if re.search(r'\b(?:managers?|quan ly|nguoi quan ly)\b', text):
            raise AmbiguousQueryError('Manager role requires an explicit employee-manager relationship')
        return None

    def _tables(self, links: SchemaLinkResult) -> list[str]:
        # Only strong semantic table evidence establishes the query context.
        # Weak identifier matches such as the English verb "list" are not tables.
        hinted_tables = {
            column.rsplit('.', 1)[0]
            for hint in links.relationship_hints for column in hint
        }
        tables = [candidate.name for candidate in links.tables if candidate.score >= 0.90 or (
            candidate.match_type == 'related_business_concept' and candidate.name in hinted_tables
        )]
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

    def _date_column(self, tables: list[str], anchor_table: str | None) -> tuple[str, str]:
        ordered_tables = ([anchor_table] if anchor_table in tables else []) + [t for t in tables if t != anchor_table]
        direct_neighbors = []
        if anchor_table:
            direct_neighbors = sorted(self.graph.adjacency.get(anchor_table, set()))
        for name in _DATE_NAMES:
            candidates = [f'{table}.{name}' for table in ordered_tables if f'{table}.{name}' in self.registry.columns]
            if not candidates:
                candidates = [f'{table}.{name}' for table in direct_neighbors if f'{table}.{name}' in self.registry.columns]
            if len(candidates) == 1:
                return candidates[0], candidates[0].rsplit('.', 1)[0]
            if anchor_table and f'{anchor_table}.{name}' in candidates:
                return f'{anchor_table}.{name}', anchor_table
        raise MissingEntityError('Missing date column')

    def _group_column(self, preprocessed: PreprocessedQuery, tables: list[str]) -> str:
        text = preprocessed.accent_stripped_text
        preferred = []
        # "top N <dimension>" states the output dimension directly, even
        # when later phrases mention filters such as customer or branch.
        top_dimension = re.search(r'\btop\s+\d+\s+(san pham|khach hang|chi nhanh)\b', text)
        if top_dimension:
            column = {
                'san pham': 'product.products.name',
                'khach hang': 'customer.customers.name',
                'chi nhanh': 'org.branches.name',
            }[top_dimension.group(1)]
            if column in self.registry.columns:
                return column
        # In “X theo Y”, Y is the grouping dimension. Earlier mentions are
        # filters or the aggregate's business subject, not a tie-breaker.
        grouping_text = text.split(' theo ', 1)[1] if ' theo ' in text else text
        for phrase, column in (
            ('chi nhanh', 'org.branches.name'),
            ('khach hang', 'customer.customers.name'),
            ('san pham', 'product.products.name'),
        ):
            if phrase in grouping_text:
                preferred.append(column)
        if not preferred:
            for phrase, column in (
                ('chi nhanh', 'org.branches.name'),
                ('khach hang', 'customer.customers.name'),
                ('san pham', 'product.products.name'),
            ):
                if phrase in text:
                    preferred.append(column)
        candidates = [column for column in preferred if column in self.registry.columns]
        if not candidates:
            candidates = [f'{table}.name' for table in tables if f'{table}.name' in self.registry.columns]
        return self._one(candidates, 'grouping column')

    def _entity_filters(self, entities: list[ExtractedEntity], tables: list[str],
                        preprocessed: PreprocessedQuery) -> tuple[list[QueryFilter], list[str], list[tuple[str, str]]]:
        filters: list[QueryFilter] = []
        all_tables = list(tables)
        join_hints: list[tuple[str, str]] = []
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
                status_columns = self._status_columns(preprocessed, all_tables)
                target = self._one(status_columns, 'status column')
            if not target:
                continue
            if target not in self.registry.columns:
                raise MissingEntityError(f'No schema field accepts {entity.entity_type}')
            owner = target.rsplit('.', 1)[0]
            if owner not in all_tables:
                all_tables.append(owner)
            join_hints.extend(self._role_hints(tables, owner, entity.entity_type))
            filters.append(QueryFilter(column=target, operator=FilterOperator.EQ, value=entity.normalized_value))
        return filters, all_tables, list(dict.fromkeys(join_hints))

    def _status_columns(self, preprocessed: PreprocessedQuery, tables: list[str]) -> list[str]:
        text = preprocessed.accent_stripped_text
        contextual = []
        for phrase, table in (
            ('don hang', 'sales.orders'), ('san pham', 'product.products'),
            ('khach hang', 'customer.customers'), ('nhan vien', 'hr.employees'),
        ):
            column = f'{table}.status'
            if phrase in text and column in self.registry.columns:
                contextual.append(column)
        return contextual or [f'{table}.status' for table in tables if f'{table}.status' in self.registry.columns]

    def _role_hints(self, roots: list[str], owner: str, entity_type: str) -> list[tuple[str, str]]:
        role = {
            'department_name': 'department_id', 'department_code': 'department_id',
            'branch_name': 'branch_id', 'branch_code': 'branch_id',
            'customer_code': 'customer_id', 'product_code': 'product_id',
        }.get(entity_type)
        if not role:
            return []
        target = f'{owner}.id'
        if target not in self.registry.columns:
            return []
        relationships = {frozenset((relation.source, relation.target)) for relation in self.registry.relationships}
        hints = []
        for root in roots:
            source = f'{root}.{role}'
            if source in self.registry.columns and frozenset((source, target)) in relationships:
                hints.append((source, target))
        return hints

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

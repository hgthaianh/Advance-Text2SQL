"""Deterministically turn observed NLP and schema evidence into validated QueryIR."""

from datetime import date
from decimal import Decimal
import re
import unicodedata

from src.nlp.entity_extractor import ExtractedEntity
from src.query.ir import (
    ColumnRef, HavingCondition, InstanceJoinRequest, OrderBy, QueryFilter,
    QueryIR, SelectItem, TableInstance, TimeGroup,
)
from src.schema.registry import SchemaRegistry
from src.schema.relationship_graph import RelationshipGraph
from src.shared.enums import Aggregation, FilterOperator, Intent, SortDirection
from src.shared.exceptions import AmbiguousQueryError, MissingEntityError, UnsupportedQueryError
from src.shared.models import PreprocessedQuery, SchemaLinkResult, UserContext


_AGGREGATE_INTENTS = {Intent.REVENUE: Intent.SUM, Intent.AVERAGE: Intent.AVG}
_NUMERIC_NAMES = ('total_amount', 'amount', 'line_total', 'quantity', 'base_salary', 'price', 'cost')
_DATE_NAMES = (
    'order_date', 'invoice_date', 'payment_date', 'entry_date', 'po_date',
    'receipt_date', 'transfer_date', 'request_date', 'work_date', 'hire_date',
    'transaction_at', 'created_at', 'date',
)


class QueryIRBuilder:
    """Conservative builder which rejects ambiguity instead of inventing SQL."""

    def __init__(self, registry: SchemaRegistry) -> None:
        self.registry = registry
        self.graph = RelationshipGraph(registry)

    def build(self, preprocessed: PreprocessedQuery, schema_links: SchemaLinkResult,
              entities: list[ExtractedEntity], user_context: UserContext | None = None) -> QueryIR:
        self_department = self._self_department_query(preprocessed, user_context)
        if self_department is not None:
            return self_department
        employee_manager = self._employee_manager_query(preprocessed)
        if employee_manager is not None:
            return employee_manager
        intent = _AGGREGATE_INTENTS.get(preprocessed.intent_result.intent, preprocessed.intent_result.intent)
        tables = self._tables(schema_links)
        if (
            re.search(r'\b(?:nhan vien|employees?)\b', preprocessed.accent_stripped_text)
            and any(entity.entity_type in {'department_name', 'department_code'} for entity in entities)
        ):
            # A named department qualifies employee membership; a lexical
            # alias such as "nhan vien kinh doanh" must not change the fact
            # grain to sales.sales_reps.
            tables = ['hr.employees', *[
                table for table in tables if table not in {'hr.employees', 'sales.sales_reps'}
            ]]
        semantic_value = self._semantic_value_column(preprocessed, tables)
        if semantic_value and schema_links.ambiguous_concepts:
            ambiguous_tables = {
                candidate.name for candidate in schema_links.tables
                if candidate.reason in schema_links.ambiguous_concepts
            }
            tables = [table for table in tables if table not in ambiguous_tables]
        if intent == Intent.UNKNOWN and semantic_value and 'theo' in preprocessed.accent_stripped_text:
            intent = Intent.SUM
        elif intent == Intent.UNKNOWN and re.search(r'^\s*so\s+', preprocessed.accent_stripped_text):
            intent = Intent.COUNT
        if intent == Intent.COUNT and re.search(
            r'\b(?:so luong|tong)\s+(?:hang da nhan|dieu chuyen kho)\b',
            preprocessed.accent_stripped_text,
        ):
            intent = Intent.SUM
        if intent == Intent.TOP_N:
            # The linker marks transactional tables implied by "best-selling"
            # as related business concepts. They are necessary to aggregate a
            # product dimension. Only numeric aggregate candidates are added:
            # dimensions and filters are resolved below from explicit grammar
            # and extracted entities.
            numeric_columns = [
                candidate for candidate in schema_links.columns
                if candidate.score >= 0.80
                and candidate.reason not in schema_links.ambiguous_concepts
                and self.registry.columns[candidate.name].name in _NUMERIC_NAMES
            ]
            direct_tables = {
                candidate.name for candidate in schema_links.tables
                if candidate.reason != 'owner_of_matched_column'
                and candidate.match_type in ('exact', 'accent_insensitive')
            }
            contextual_columns = [
                candidate for candidate in numeric_columns
                if candidate.name.rsplit('.', 1)[0] in direct_tables
            ]
            if contextual_columns:
                numeric_columns = contextual_columns
            tables = list(dict.fromkeys(tables + [
                candidate.name.rsplit('.', 1)[0] for candidate in numeric_columns
            ]))
        if intent in {Intent.UNKNOWN, Intent.DETAIL, Intent.LIST, Intent.SELECT, Intent.FILTER}:
            if not tables:
                if schema_links.ambiguous_concepts:
                    raise AmbiguousQueryError('Schema concept has multiple business meanings')
                raise UnsupportedQueryError('No supported schema concept was found')
            intent = Intent.LOOKUP
        status_values = [entity.normalized_value for entity in entities if entity.entity_type == 'status']
        if intent == Intent.COMPARISON and len(set(status_values)) >= 2:
            # Comparing named lifecycle states is a grouped count, not an
            # open-ended analytical comparison.
            intent = Intent.COUNT
        if intent in {Intent.TREND, Intent.COMPARISON, Intent.AGGREGATE}:
            raise UnsupportedQueryError(f'Intent is not supported by the deterministic builder: {intent}')
        if semantic_value:
            owner = semantic_value.rsplit('.', 1)[0]
            if owner not in tables:
                tables.append(owner)
        if not tables:
            raise UnsupportedQueryError('No supported table was found')

        aggregate = {Intent.SUM: Aggregation.SUM, Intent.AVG: Aggregation.AVG}.get(intent)
        if intent == Intent.COUNT:
            select: list[str | SelectItem] = []
        elif aggregate:
            value_column = semantic_value or self._value_column(schema_links, tables)
            owner = value_column.rsplit('.', 1)[0]
            if owner not in tables:
                tables.append(owner)
            select = [SelectItem(column=value_column, aggregation=aggregate)]
        elif intent == Intent.TOP_N:
            value_column, ranking_aggregation = self._ranking_measure(preprocessed, schema_links, tables)
            group_column = self._group_column(preprocessed, tables)
            group_owner = group_column.rsplit('.', 1)[0]
            if group_owner not in tables:
                tables.append(group_owner)
            value_owner = value_column.rsplit('.', 1)[0]
            if value_owner not in tables:
                tables.append(value_owner)
            select = [group_column, SelectItem(column=value_column, aggregation=ranking_aggregation)]
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
        join_hints.extend(self._semantic_join_hints(preprocessed, tables))
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
        time_group = self._time_group(preprocessed, tables, anchor=aggregate_owner)
        if intent == Intent.COUNT and time_group:
            count_column = self._count_column(tables)
            select = [SelectItem(column=count_column, aggregation=Aggregation.COUNT, distinct=True)]
        if intent == Intent.TOP_N:
            group_column = select[0]
            assert isinstance(group_column, str)
            group_by = [group_column]
            value_column = select[1]
            assert isinstance(value_column, SelectItem)
            order_by = [OrderBy(
                column=value_column.column,
                aggregation=value_column.aggregation,
                direction=self._ranking_direction(preprocessed),
            )]
            top_k = self._top_k(preprocessed)
        elif intent == Intent.COUNT and (
            'theo' in preprocessed.accent_stripped_text or len(set(status_values)) >= 2
        ) and not time_group:
            group_by = (
                [filters[0].column] if len(set(status_values)) >= 2
                else self._group_columns(preprocessed, tables)
            )
            for group_column in group_by:
                owner = group_column.rsplit('.', 1)[0]
                if owner not in tables:
                    tables.append(owner)
            count_column = self._count_column(tables)
            select = [*group_by, SelectItem(column=count_column, aggregation=Aggregation.COUNT, distinct=True)]
            order_by = [OrderBy(column=count_column, aggregation=Aggregation.COUNT, direction=SortDirection.DESC)]
        elif aggregate and 'theo' in preprocessed.accent_stripped_text and not time_group:
            group_by = self._group_columns(preprocessed, tables)
            select[0:0] = group_by
            value_column = next(item for item in select if isinstance(item, SelectItem))
            assert isinstance(value_column, SelectItem)
            order_by = [OrderBy(column=value_column.column, aggregation=aggregate, direction=SortDirection.DESC)]

        having_column = next((item.column for item in select if isinstance(item, SelectItem)), None)
        having = self._having(preprocessed, having_column,
                              aggregate or (Aggregation.SUM if intent == Intent.TOP_N else None), group_by)
        return QueryIR(intent=intent, tables=tables, select=select, filters=filters,
                       having=having, group_by=group_by, order_by=order_by, top_k=top_k,
                       time_group=time_group, join_hints=join_hints, outer_joins=outer_joins)

    def _semantic_value_column(self, preprocessed: PreprocessedQuery, tables: list[str]) -> str | None:
        """Resolve explicit business measures before falling back to column rank."""
        text = preprocessed.accent_stripped_text
        rules = (
            (r'\bluong co ban\b', 'hr.employees.base_salary'),
            (r'\bdiem danh gia\b', 'hr.performance_reviews.performance_score'),
            (r'\bton kho kha dung\b', 'inventory.inventory_balances.qty_available'),
            (r'\bton kho\b', 'inventory.inventory_balances.qty_on_hand'),
            (r'\b(?:gia tri|tong gia tri)\s+(?:po|purchase order)\b', 'procurement.purchase_orders.total_amount'),
            (r'\bvendor bill\b', 'finance.vendor_bills.total_amount'),
            (r'\bitem dat mua\b', 'procurement.purchase_order_items.ordered_qty'),
            (r'\bhang da nhan\b', 'procurement.goods_receipt_items.received_qty'),
            (r'\bdieu chuyen kho\b', 'inventory.stock_transfer_items.received_qty'),
            (r'\bthanh toan\b', 'sales.payments.amount'),
            (r'\bphat sinh but toan\b', 'finance.journal_entry_lines.debit_amount'),
            (r'\bchi phi\b', 'finance.journal_entry_lines.debit_amount'),
            (r'\b(?:doanh thu|doanh so)\b.*\b(?:thuong hieu|danh muc|san pham)\b', 'sales.order_items.line_total'),
            (r'\b(?:doanh thu|doanh so)\b', 'sales.orders.total_amount'),
        )
        for pattern, column in rules:
            if re.search(pattern, text) and column in self.registry.columns:
                return column
        return None

    def _ranking_measure(self, preprocessed: PreprocessedQuery, links: SchemaLinkResult,
                         tables: list[str]) -> tuple[str, Aggregation]:
        text = preprocessed.accent_stripped_text
        count_rules = (
            (r'\bnhieu nhan vien\b', 'hr.employees.id'),
            (r'\bnhieu ticket\b', 'support.support_tickets.id'),
        )
        for pattern, column in count_rules:
            if re.search(pattern, text) and column in self.registry.columns:
                return column, Aggregation.COUNT
        if re.search(r'\bsan pham ban (?:chay|nhieu) nhat\b', text):
            return 'sales.order_items.quantity', Aggregation.SUM
        try:
            column = self._semantic_value_column(preprocessed, tables) or self._value_column(links, tables)
        except MissingEntityError as exc:
            raise AmbiguousQueryError('Ranking requires an explicit business measure') from exc
        return column, Aggregation.SUM

    def _count_column(self, tables: list[str]) -> str:
        for table in tables:
            primary = next((column for column in self.registry.tables[table].columns if column.primary_key), None)
            if primary:
                return f'{table}.{primary.name}'
        return '*'

    def _time_group(self, preprocessed: PreprocessedQuery, tables: list[str],
                    anchor: str | None) -> TimeGroup | None:
        text = preprocessed.accent_stripped_text
        match = re.search(r'\b(?:theo|tung)\s+(ngay|thang|nam)\b', text)
        if not match:
            return None
        date_column, owner = self._date_column(tables, anchor)
        if owner not in tables:
            tables.append(owner)
        return TimeGroup(column=date_column, granularity={
            'ngay': 'day', 'thang': 'month', 'nam': 'year',
        }[match.group(1)])

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
        if re.search(r'\b(?:managers?|nguoi quan ly|quan ly\s+(?:cua|nhan vien))\b', text):
            raise AmbiguousQueryError('Manager role requires an explicit employee-manager relationship')
        return None

    def _self_department_query(self, preprocessed: PreprocessedQuery,
                               user_context: UserContext | None) -> QueryIR | None:
        """Resolve an explicit first-person department question using trusted identity."""
        text = preprocessed.accent_stripped_text
        if not re.search(r'\b(?:toi|me)\s+(?:thuoc\s+)?(?:phong ban|department)\s+(?:nao|gi|which|what)\b', text):
            return None
        if user_context is None or not user_context.employee_id:
            raise MissingEntityError('An employee identity is required for a self department query')
        employee_id: int | str = user_context.employee_id
        if isinstance(employee_id, str) and employee_id.isdigit():
            employee_id = int(employee_id)
        return QueryIR(
            intent=Intent.LOOKUP,
            tables=['hr.employees', 'org.departments'],
            select=['org.departments.name'],
            filters=[QueryFilter(column='hr.employees.id', operator=FilterOperator.EQ, value=employee_id)],
            join_hints=[('hr.employees.department_id', 'org.departments.id')],
        )

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
        # A direct table phrase such as "nhân viên" is stronger context than a
        # generic value phrase such as "lương".  Prefer its numeric columns
        # before considering other tables inferred only from the generic phrase.
        direct_tables = {
            candidate.name for candidate in links.tables
            if candidate.reason != 'owner_of_matched_column'
            and candidate.match_type in ('exact', 'accent_insensitive')
        }
        contextual_candidates = [
            candidate for candidate in candidates
            if candidate.rsplit('.', 1)[0] in direct_tables
        ]
        if contextual_candidates:
            return self._one(contextual_candidates, 'numeric value column')
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
        return self._group_columns(preprocessed, tables)[0]

    def _group_columns(self, preprocessed: PreprocessedQuery, tables: list[str]) -> list[str]:
        text = preprocessed.accent_stripped_text
        preferred = []
        # "top N <dimension>" states the output dimension directly, even
        # when later phrases mention filters such as customer or branch.
        top_dimension = re.search(
            r'\btop\s+\d+\s+(phong ban|nha cung cap|san pham|khach hang|chi nhanh|nhan vien|chuyen vien)\b',
            text,
        )
        if top_dimension:
            column = {
                'phong ban': 'org.departments.name',
                'nha cung cap': 'procurement.suppliers.name',
                'san pham': 'product.products.name',
                'khach hang': 'customer.customers.name',
                'chi nhanh': 'org.branches.name',
                'nhan vien': 'hr.employees.full_name',
                'chuyen vien': 'hr.employees.full_name',
            }[top_dimension.group(1)]
            if column in self.registry.columns:
                return [column]
        # In “X theo Y”, Y is the grouping dimension. Earlier mentions are
        # filters or the aggregate's business subject, not a tie-breaker.
        grouping_text = text.split(' theo ', 1)[1] if ' theo ' in text else text
        dimensions = (
            ('sales representative', 'hr.employees.full_name'),
            ('phan khuc', 'customer.customer_segments.name'),
            ('thuong hieu', 'product.brands.name'),
            ('danh muc', 'product.categories.name'),
            ('cost center', 'org.cost_centers.name'),
            ('nha cung cap', 'procurement.suppliers.name'),
            ('supplier', 'procurement.suppliers.name'),
            ('warehouse', 'inventory.warehouses.name'),
            ('kho', 'inventory.warehouses.name'),
            ('phong ban', 'org.departments.name'),
            ('chi nhanh', 'org.branches.name'),
            ('khach hang', 'customer.customers.name'),
            ('san pham', 'product.products.name'),
            ('nhan vien', 'hr.employees.full_name'),
            ('chuyen vien', 'hr.employees.full_name'),
            ('tai khoan', 'finance.chart_of_accounts.account_name'),
            ('sla', 'support.sla_policies.policy_name'),
        )
        matches = [
            (match.start(), index, column)
            for index, (phrase, column) in enumerate(dimensions)
            if column in self.registry.columns
            and (match := re.search(rf'(?<!\w){re.escape(phrase)}(?!\w)', grouping_text))
        ]
        preferred.extend(column for _, _, column in sorted(matches))
        if not preferred:
            matches = [
                (match.start(), index, column)
                for index, (phrase, column) in enumerate(dimensions)
                if column in self.registry.columns
                and (match := re.search(rf'(?<!\w){re.escape(phrase)}(?!\w)', text))
            ]
            preferred.extend(column for _, _, column in sorted(matches))
        candidates = [column for column in preferred if column in self.registry.columns]
        if not candidates:
            candidates = [f'{table}.name' for table in tables if f'{table}.name' in self.registry.columns]
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            return [self._one(candidates, 'grouping column')]
        return candidates

    def _semantic_join_hints(self, preprocessed: PreprocessedQuery,
                             tables: list[str]) -> list[tuple[str, str]]:
        """Return FK-role evidence expressed by ordinary business wording."""
        text = preprocessed.accent_stripped_text
        hints: list[tuple[str, str]] = []

        def add(source: str, target: str) -> None:
            if source in self.registry.columns and target in self.registry.columns:
                hints.append((source, target))

        if 'hr.employees' in tables or re.search(r'\b(?:nhan vien|phong ban)\b', text):
            add('hr.employees.department_id', 'org.departments.id')
        if 'hr.leave_requests' in tables and not re.search(r'\b(?:nguoi duyet|approved by|approver)\b', text):
            add('hr.leave_requests.employee_id', 'hr.employees.id')
        if 'hr.performance_reviews' in tables and not re.search(r'\b(?:reviewer|nguoi danh gia)\b', text):
            add('hr.performance_reviews.employee_id', 'hr.employees.id')
        if 'inventory.inventory_balances' in tables or re.search(r'\bton kho\b', text):
            add('inventory.inventory_balances.warehouse_id', 'inventory.warehouses.id')
            add('inventory.inventory_balances.product_id', 'product.products.id')
        if 'inventory.stock_serials' in tables:
            add('inventory.stock_serials.warehouse_id', 'inventory.warehouses.id')
            add('inventory.warehouses.tenant_id', 'org.tenants.id')
        if re.search(r'\b(?:hang da nhan|goods receipt)\b', text):
            add('procurement.goods_receipt_items.receipt_id', 'procurement.goods_receipts.id')
            add('procurement.goods_receipts.warehouse_id', 'inventory.warehouses.id')
            add('procurement.goods_receipts.supplier_id', 'procurement.suppliers.id')
        if re.search(r'\b(?:phat sinh but toan|journal)\b', text):
            add('finance.journal_entry_lines.journal_entry_id', 'finance.journal_entries.id')
            add('finance.journal_entry_lines.account_id', 'finance.chart_of_accounts.id')
        if re.search(r'\b(?:ticket).*(?:theo\s+sla|sla)\b', text):
            add('support.ticket_sla_events.ticket_id', 'support.support_tickets.id')
            add('support.ticket_sla_events.sla_policy_id', 'support.sla_policies.id')
        if re.search(r'\bdia chi\b', text) and re.search(r'\bkhach hang\b', text):
            add('customer.customer_addresses.customer_id', 'customer.customers.id')
            add('customer.customer_addresses.address_id', 'customer.addresses.id')
        if re.search(r'\b(?:sales representative|(?:doanh thu|doanh so).*phong(?: ban)?)\b', text):
            add('sales.orders.sales_rep_id', 'sales.sales_reps.id')
            add('sales.sales_reps.employee_id', 'hr.employees.id')
            add('hr.employees.department_id', 'org.departments.id')
        return list(dict.fromkeys(hints))

    def _entity_filters(self, entities: list[ExtractedEntity], tables: list[str],
                        preprocessed: PreprocessedQuery) -> tuple[list[QueryFilter], list[str], list[tuple[str, str]]]:
        filters: list[QueryFilter] = []
        all_tables = list(tables)
        join_hints: list[tuple[str, str]] = []
        targets = {
            'tenant_id': 'org.tenants.id',
            'customer_type': 'customer.customers.customer_type',
            'customer_tier': 'customer.customers.customer_tier',
            'priority': 'support.support_tickets.priority',
            'department_name': 'org.departments.name',
            'department_code': 'org.departments.code',
            'branch_name': 'org.branches.name',
            'branch_code': 'org.branches.code',
            'product_code': 'product.products.code',
            'customer_code': 'customer.customers.code',
            'employee_code': 'hr.employees.employee_code',
            'order_code': 'sales.orders.order_no',
            'invoice_code': 'sales.invoices.invoice_no',
            'purchase_order_code': 'procurement.purchase_orders.po_number',
            'ticket_code': 'support.support_tickets.ticket_number',
        }
        status_entities = [entity for entity in entities if entity.entity_type == 'status']
        grouped_status_values = list(dict.fromkeys(entity.normalized_value for entity in status_entities))
        emitted_status_filter = False
        for entity in entities:
            target = targets.get(entity.entity_type)
            if entity.entity_type == 'status':
                if emitted_status_filter:
                    continue
                status_columns = self._status_columns(preprocessed, all_tables)
                target = self._one(status_columns, 'status column')
                emitted_status_filter = True
            if not target:
                continue
            if target not in self.registry.columns:
                raise MissingEntityError(f'No schema field accepts {entity.entity_type}')
            owner = target.rsplit('.', 1)[0]
            if owner not in all_tables:
                all_tables.append(owner)
            join_hints.extend(self._role_hints(tables, owner, entity.entity_type))
            operator = FilterOperator.LIKE if entity.entity_type == 'department_name' else FilterOperator.EQ
            value = f'{entity.normalized_value}%' if operator == FilterOperator.LIKE else entity.normalized_value
            if entity.entity_type == 'tenant_id':
                value = int(value)
            if entity.entity_type == 'status' and len(grouped_status_values) >= 2:
                operator = FilterOperator.IN
                value = grouped_status_values
            filters.append(QueryFilter(column=target, operator=operator, value=value))
        return filters, all_tables, list(dict.fromkeys(join_hints))

    def _status_columns(self, preprocessed: PreprocessedQuery, tables: list[str]) -> list[str]:
        text = preprocessed.accent_stripped_text
        contextual = []
        for phrase, table in (
            ('don hang', 'sales.orders'), ('san pham', 'product.products'),
            ('khach hang', 'customer.customers'), ('nhan vien', 'hr.employees'),
            ('don nghi phep', 'hr.leave_requests'),
            ('purchase order', 'procurement.purchase_orders'), (' po ', 'procurement.purchase_orders'),
            ('vendor bill', 'finance.vendor_bills'), ('hoa don', 'sales.invoices'),
            ('thanh toan', 'sales.payments'), ('ticket', 'support.support_tickets'),
        ):
            field = 'employment_status' if table == 'hr.employees' else 'status'
            column = f'{table}.{field}'
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
        if phrase == 'tu' and preprocessed.date_range:
            return []
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
        text = preprocessed.accent_stripped_text
        explicit = re.search(r'\btop\s+(\d+)\b', text)
        if explicit:
            return int(explicit.group(1))
        leading = re.match(r'\s*(\d+)\s+', text)
        if leading:
            return int(leading.group(1))
        # A singular superlative has an unambiguous cardinality even when a
        # date range contributes other numeric tokens.
        if re.search(r'\b(?:cao nhat|thap nhat|nhieu nhat|it nhat|lon nhat|nho nhat|best[- ]selling)\b', text):
            return 1
        raise MissingEntityError('TOP-K requires a positive result count')

    @staticmethod
    def _ranking_direction(preprocessed: PreprocessedQuery) -> SortDirection:
        """Rank smallest values first only when the language states that direction."""
        if re.search(r'\b(?:thap nhat|it nhat|nho nhat|lowest|least)\b', preprocessed.accent_stripped_text):
            return SortDirection.ASC
        return SortDirection.DESC

    @staticmethod
    def _one(candidates: list[str], meaning: str) -> str:
        unique = list(dict.fromkeys(candidates))
        if not unique:
            raise MissingEntityError(f'Missing {meaning}')
        if len(unique) != 1:
            raise AmbiguousQueryError(f'Ambiguous {meaning}: {", ".join(unique)}')
        return unique[0]

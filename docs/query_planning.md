# Query planning engine

The engine consumes `QueryIR`, resolves metadata and unique FK paths, then builds parameterized SQL using the project's existing SQLAlchemy dependency. It does not parse questions, classify intent, decide permissions or execute database queries.

```text
QueryIR -> QueryPlanner -> JoinResolver -> QueryPlan
        -> BasicQueryHandler / SQLBuilder -> CompiledQuery
```

`BasicQueryHandler.handle()` is the convenience entry point that runs the planner and builder together. Call the planner/builder separately to inspect the plan.

## Runnable example

The merged registry supports both `SchemaRegistry()` / `SchemaRegistry(path=...)` for YAML metadata and injected tables/relationships as shown below. The schema linker and query engine share the same registry and graph. No tables or column mappings are hardcoded in the planner.

```python
from src.query.handlers.basic import BasicQueryHandler
from src.query.ir import QueryIR
from src.schema.registry import SchemaRegistry
from src.shared.models import SchemaColumn, SchemaRelationship, SchemaTable

registry = SchemaRegistry(
    tables=[
        SchemaTable(name="orders", schema_name="sales", columns=[
            SchemaColumn(name="id", data_type="bigint", primary_key=True),
            SchemaColumn(name="customer_id", data_type="bigint"),
            SchemaColumn(name="total_amount", data_type="numeric(18,2)"),
        ]),
        SchemaTable(name="customers", schema_name="customer", columns=[
            SchemaColumn(name="id", data_type="bigint", primary_key=True),
            SchemaColumn(name="customer_type", data_type="text"),
        ]),
    ],
    relationships=[SchemaRelationship(
        source="sales.orders.customer_id", target="customer.customers.id",
    )],
)

handler = BasicQueryHandler(registry)
compiled = handler.handle(QueryIR(
    intent="SUM",
    tables=["orders", "customers"],
    select=[{"column": "orders.total_amount", "aggregation": "SUM"}],
    filters=[{"column": "customers.customer_type", "operator": "=", "value": "VIP"}],
))
print(compiled.sql)
print(compiled.params)
```

Output includes quoted identifiers and named parameters:

```sql
SELECT sum("sales"."orders"."total_amount") AS sum_1
FROM "sales"."orders" JOIN "customer"."customers"
ON "sales"."orders"."customer_id" = "customer"."customers"."id"
WHERE "customer"."customers"."customer_type" = :p0
```

```python
{"p0": "VIP"}
```

`compiled.sql` uses the PostgreSQL dialect with named parameters for inspection. `compiled.statement` is the SQLAlchemy `Select` expected by the existing executor/validator contracts; an authorized execution layer can execute that statement directly. Never interpolate `compiled.params` into the SQL string.

To inspect plans:

```python
from src.query.planner import QueryPlanner
from src.query.sql_builder import SQLBuilder

planner = QueryPlanner(registry)
plan = planner.plan(QueryIR(intent="COUNT", tables=["orders"]))
compiled = SQLBuilder(registry, planner.graph).build(plan)
```

## Input compatibility and validation

- Existing `select=["column"]`, lowercase `Intent` enum values and textual filter operators (`eq`, `gte`, etc.) continue to work. Structured `SelectItem` values add `aggregation`. Uppercase input intent/aggregation and symbolic comparison operators are normalized at the IR boundary.
- Supported intents: `LOOKUP`, `FILTER`, `COUNT`, `SUM`, `AVG`; existing `SELECT`, `LIST`, `DETAIL` use lookup semantics and `AVERAGE` uses AVG. `TOP_N` requires explicit projections and ordering, with structured aggregate projections when needed. Other intents are explicitly rejected. No revenue column or date range is inferred. Period comparisons use the separate structured API below.
- An explicit base table is required. Qualified columns can add owner tables, including tables referenced by permission-injected filters. Bare columns must be unique among explicitly requested tables. Canonical identifiers are `schema.table.column`; duplicate short table names require qualification.
- LOOKUP/FILTER require explicit projections; there is no implicit `SELECT *`. COUNT without projections becomes `COUNT(*)`. SUM/AVG require a numeric column. An explicit aggregate in a SELECT projection is also supported.
- Grouped aggregates must include every nonaggregate projection in `group_by`. Aggregate ordering references a group key, a unique selected aggregate column, or an explicit `OrderBy.aggregation`. HAVING accepts structured COUNT/SUM/AVG comparisons, including aggregates absent from the projection. HAVING conditions are combined with AND, support the filter comparison/IN/null rules below, and reject LIKE. Raw expressions, custom aliases, subqueries and windows remain unsupported.
- `limit` defaults to 100; `limit` and optional `top_k` must be integers 1–1000. The effective emitted limit must also respect `Settings.query_max_limit` (`QUERY_MAX_LIMIT`, default 1000), or the handler/planner/builder constructor's `max_limit` override. Nonaggregate queries use `top_k` when supplied, otherwise `limit`. Ordinary aggregates continue to ignore `limit`, including grouped aggregates. Explicit aggregate TOP-K uses `top_k`, or `intent="TOP_N"` with `limit`, and requires GROUP BY plus ORDER BY. LIMIT applies to resulting groups after HAVING and ORDER BY; no input limit or limited subquery is inserted before aggregation. Booleans and strings are rejected as limits.
- Unsupported operators/intent/aggregates and schema resolution failures raise domain exceptions from `src/shared/exceptions.py`. Planner input shape errors become `MalformedQueryIRError`. Direct Pydantic construction still raises `ValidationError` for ordinary structural errors, preserving existing model behavior.
- Unknown IR fields are rejected rather than silently discarded. The planner revalidates model instances; the builder revalidates and replans its public plan input to reject forged joins or mutated IR.

## Filters and permission compatibility

`QueryFilter` and the alias `FilterCondition` refer to the same model. The optional `source` field is preserved as evidence but has no effect on compilation:

```python
from src.query.ir import FilterCondition

query = QueryIR(intent="COUNT", tables=["employees"], filters=[
    FilterCondition(column="employees.department_id", operator="=", value=12, source="permission")
])
# With employee metadata present, the filter compiles as department_id = :p0.
```

Permission policies must inject their filters into `query.filters` before planning. The planner does not accept a PermissionContext or decide which filters to add. Every filter is combined with AND. Supported operators are `=`, `!=`/`<>`, `>`, `>=`, `<`, `<=`, `IN` and `LIKE`.

Values are bind parameters, including each IN element, strings containing SQL, dates and Decimal values. Empty IN lists compile to false, so an empty injected scope cannot broaden a query. `= None` and `!= None` compile to IS NULL and IS NOT NULL. Other null comparisons, IN lists containing null, scalar IN values and non-string LIKE values are rejected.

## Join semantics and limits

Path search lives only in `src/schema/relationship_graph.py`. `find_relationship_path()` performs BFS and rejects equal-length alternatives, including two distinct FKs between the same pair of tables. `find_path()` preserves the table-pair interface while sharing that search. Missing paths and ambiguous paths raise distinct domain exceptions.

`JoinResolver` merges unique shortest paths from the first requested table to every other required table, preserving intermediate tables and deduplicating shared edges. This is a rooted shortest-path tree, not a global minimum-cost join optimizer. All generated joins are INNER JOINs on metadata FK columns.

The initial relationship contract represents single-column foreign keys. Self-joins with multiple table roles, composite foreign keys, explicit path disambiguation and outer joins require additional IR/metadata contracts and are not implemented. COUNT(*) counts joined rows; COUNT(column) excludes nulls. There is no automatic DISTINCT, fan-out correction or preaggregation, so upstream query construction must choose the intended relational semantics.

For example, one customer with two orders containing three order items produces
three rows after `customers -> orders -> order_items`. `COUNT(customers.id)` is
therefore **3, not 1**. This is deliberate v1 SQL row semantics, not a count of
unique customers. SUM and AVG can also be affected by one-to-many join fan-out.
Future support for `COUNT(DISTINCT column)` must be explicit in the IR and SQL
builder; it is not implemented or inferred today.

Ambiguous joins always raise `AmbiguousJoinPathError`. Metadata insertion order,
edge traversal order and query direction must never select an arbitrary first
path. Both parallel FKs and equal-length multi-hop alternatives are covered by
permutation tests through the full handler, ensuring no SQL is compiled.

The API mock and database executor remain unchanged. Metadata can come from the default schema specification or application injection; neither path inspects a live database.

## Complex queries (T2-21)

`ComplexQueryHandler.handle()` uses the same planner/builder as the basic handler.
For example, using the registry in the runnable example above:

```python
from src.query.handlers.complex import ComplexQueryHandler
from src.query.ir import HavingCondition, OrderBy, SelectItem

handler = ComplexQueryHandler(registry)
compiled = handler.handle(QueryIR(
    intent="SELECT",
    tables=["orders"],
    select=["customers.customer_type", SelectItem(column="orders.total_amount", aggregation="SUM")],
    group_by=["customers.customer_type"],
    having=[HavingCondition(column="orders.total_amount", aggregation="SUM",
                            operator=">", value=100_000_000)],
    order_by=[OrderBy(column="orders.total_amount", aggregation="SUM", direction="DESC")],
    top_k=5,
))
```

Both HAVING thresholds and the result limit are bound parameters. Identifiers
come from the registry, and directions/functions come from validated enums.
Columns used only in HAVING or ORDER BY also participate in join resolution.
For SUM and AVG, the referenced column must have a numeric schema type.
`COUNT(*)` is represented by `column="*", aggregation="COUNT"`.

For aggregate TOP-K, missing group keys are appended to ORDER BY ASC to break
ties deterministically. Row TOP-K requires explicit ORDER BY; callers should
include a unique key as the last ordering field when stable membership among
equal values is required. No unique row key is inferred.

### Explicit period comparison

With a registry containing `orders.order_date` as a date/timestamp column:

```python
from datetime import date
from src.query.ir import PeriodComparison, TimePeriod

request = PeriodComparison(
    query=QueryIR(intent="SUM", tables=["orders"], select=["orders.total_amount"],
                  filters=[{"column": "orders.customer_id", "value": 12, "source": "permission"}]),
    date_column="orders.order_date",
    periods=[
        TimePeriod(label="previous", start=date(2026, 8, 1), end=date(2026, 9, 1)),
        TimePeriod(label="current", start=date(2026, 9, 1), end=date(2026, 10, 1)),
    ],
)
results = handler.compare(request)  # list[CompiledPeriodQuery], in input order
for result in results:
    print(result.period.label, result.query.sql, result.query.params)
```

The caller resolves calendar boundaries. Each result independently binds
`date_column >= start AND date_column < end`, keeping every original filter.
Existing date filters also remain and intersect with the period. The engine
does not read the clock, parse relative dates, infer time zones, execute queries,
or calculate percentage changes. Execute both statements in a suitable shared
database snapshot when consistent comparison results are needed.

Exactly two differently labelled periods are required, with `start < end` and
matching endpoint types/timezone awareness within each period. Dates and
datetimes are accepted. The comparison base query must be an ungrouped
aggregate, optionally with multiple aggregate projections; grouped comparison,
HAVING, ordering and TOP-K within comparisons are rejected. Ordinary SQL null
semantics remain: SUM/AVG of empty input are NULL, COUNT is zero.

### Contract changes and boundaries

- `QueryIR.having`: list of `HavingCondition` (aggregate plus filter fields).
- `QueryIR.top_k`: optional positive strict integer; ordinary aggregate `limit` behavior is unchanged.
- `OrderBy.aggregation`: optional COUNT/SUM/AVG, resolving ambiguity when several functions use the same column.
- `TimePeriod`, `PeriodComparison` and `CompiledPeriodQuery`: two explicit aggregate plans, without nested SQL or changes to `CompiledQuery`.
- `Settings.query_max_limit` and optional `max_limit` constructor argument: cap emitted row/group limits.
- The legacy permission adapter discovers HAVING table references using its existing allowlist checks. No permission policies are added to the SQL planner.

Arbitrary/correlated subqueries, UNION, CTEs, window functions, raw SQL,
arbitrary expressions, OR trees, aggregate DISTINCT, outer/self joins and
automatic fan-out correction remain unsupported. An ambiguous FK path still
fails with `AmbiguousJoinPathError`; there is no first-path fallback.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/query -q
.\.venv\Scripts\python.exe -m pytest -q
```

Tests cover supported intents/operators, ordinary and injected filters, null/empty-IN semantics, direct/reverse/multi-hop joins, disconnected and ambiguous graphs, schema validation, forged plans and parameterization. An in-memory SQLite test checks actual aggregate and join results using SQLAlchemy statements; PostgreSQL SQL generation is checked through dialect compilation, without connecting to a PostgreSQL server.

Complex-query tests additionally execute grouped TOP-K over 150 rows per group,
with multi-hop joins, permission filters, half-open dates and multiple HAVING
conditions. Separate comparison tests execute both periods and check boundary
membership and permission scope. A previous rejection test for the formerly
unsupported `having` field now uses the still-unsupported `window` field.


## Compatibility after merging dev

`QueryIR.joins` is retained for the existing FK adapter. The SQL planner recomputes
FK paths and rejects supplied joins that differ from those paths.
`FKQueryPlanner` retains the upstream permission adapter behavior; the new
`QueryPlanner` and `BasicQueryHandler` remain policy-free. Injected row-filter
owners participate in the adapter's access checks before resolving joins.
`FKJoinResolver` and `FKRelationshipGraph` retain column-pair interfaces while
using the shared strict path search. Disconnected plans now fail rather than
returning a collection of unjoined tables; ambiguous paths never pick the first
match. Table adjacency remains a set of neighbor names for schema-linker ranking.

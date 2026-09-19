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
- Supported intents: `LOOKUP`, `FILTER`, `COUNT`, `SUM`, `AVG`; existing `SELECT`, `LIST`, `DETAIL` use lookup semantics and `AVERAGE` uses AVG. Other intents are explicitly rejected. No inferred revenue column, TOP_N, comparison or trend behavior is added.
- An explicit base table is required. Qualified columns can add owner tables, including tables referenced by permission-injected filters. Bare columns must be unique among explicitly requested tables. Canonical identifiers are `schema.table.column`; duplicate short table names require qualification.
- LOOKUP/FILTER require explicit projections; there is no implicit `SELECT *`. COUNT without projections becomes `COUNT(*)`. SUM/AVG require a numeric column. An explicit aggregate in a SELECT projection is also supported.
- Grouped aggregates must include every nonaggregate projection in `group_by`. Aggregate ordering references a group key or a unique selected aggregate column; ordering by that aggregate column emits the aggregate expression. No HAVING, raw expressions, custom aliases, subqueries or windows are supported.
- `limit` defaults to 100 and is validated as an integer 1–1000. It is bound as `:limit` only for nonaggregate row queries (LOOKUP/FILTER and their compatible intents). Queries containing aggregate projections emit no LIMIT, even when the caller sets `limit` explicitly. This includes grouped aggregates and SELECT with an explicit aggregation. Aggregate-result pagination is not supported in v1. No limit or limited subquery is inserted before aggregation. Booleans and strings are rejected as limits.
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

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/query -q
.\.venv\Scripts\python.exe -m pytest -q
```

Tests cover supported intents/operators, ordinary and injected filters, null/empty-IN semantics, direct/reverse/multi-hop joins, disconnected and ambiguous graphs, schema validation, forged plans and parameterization. An in-memory SQLite test checks actual aggregate and join results using SQLAlchemy statements; PostgreSQL SQL generation is checked through dialect compilation, without connecting to a PostgreSQL server.


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

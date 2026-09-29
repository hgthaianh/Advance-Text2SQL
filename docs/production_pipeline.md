# Production pipeline bridge

`POST /query` delegates to `Text2SQLPipeline`; the API route contains no NLP, schema, permission, or SQL logic.

The pipeline calls, in order: `NLPRobustnessProcessor`, `DeterministicEntityExtractor`, `SchemaLinker`, `QueryIRBuilder`, permission resolution and row-policy injection, `BasicQueryHandler`, `ReadOnlySQLValidator`, and `ReadOnlyExecutor`.

The bridge is deterministic. `QueryIRBuilder` only uses schema-linked candidates and schema metadata. It raises controlled errors for missing values, ambiguous columns, and unsupported intents instead of generating guessed SQL.

`ReadOnlySQLValidator` accepts only typed SQLAlchemy `Select` statements and rejects raw SQL fragments. `ReadOnlyExecutor` accepts only `CompiledQuery`, binds parameters, starts PostgreSQL transactions as `READ ONLY`, applies `statement_timeout`, and returns a generic execution error without connection details.

`UserContext.employee_id` is distinct from `user_id`. In registry-aware production resolution, `SELF` obtains the sole primary key of the schema's `employees` table and emits a trusted `source="permission"` filter. User filters cannot remove it; the planner validates it as a normal schema column.

The integration fixture in `tests/integration/pipeline/` provides deterministic SQLite data for global/department counts, SELF access, sensitive-column denial, revenue date/entity filtering, and grouped joins.

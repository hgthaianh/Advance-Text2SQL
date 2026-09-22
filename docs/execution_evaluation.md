# Seeded PostgreSQL execution evaluation

The full 233-case corpus measures schema linking and compilation across the production schema. It intentionally does not invent result labels for tables that have no deterministic fixture.

`evaluation/result_accuracy_cases.json` is a separate 60-case result-accuracy suite. It uses a dedicated PostgreSQL database whose name must end in `_eval`; the seed command refuses every other database name.

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_execution_eval --database-url "postgresql+psycopg://text2sql:password@localhost:5432/text2sql_eval" --coverage-report evaluation\results\execution_coverage.json
.\.venv\Scripts\python.exe evaluation\run_execution_coverage_eval.py --database-url "postgresql+psycopg://text2sql:password@localhost:5432/text2sql_eval"
.\.venv\Scripts\python.exe evaluation\run_result_accuracy_eval.py --database-url "postgresql+psycopg://text2sql:password@localhost:5432/text2sql_eval"
```

The seed includes two tenants, department-scoped employees, SELF identity data, VIP orders, and a sensitive salary field. It supports assertions for tenant isolation, department scope, SELF, revenue with date/entity filters, and column denial.

Production pipeline resolution requires a trusted `tenant_id`. `ScopedRowPolicy` injects `tenant_id` filters for every tenant-owned table in the validated join path. Branch, department, and team scope filters use direct columns when possible; otherwise they resolve one unique FK path to a scope-bearing table. Missing or ambiguous paths fail closed.

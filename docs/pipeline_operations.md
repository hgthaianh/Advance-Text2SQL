# Pipeline operations

## Entity catalog

`DeterministicEntityExtractor` receives an `EntityCatalog` at application
startup. Entries are typed canonical values with exact aliases; they do not
contain database columns or SQL. The baseline catalog covers common branch,
department, status and customer-value terms. A deployment should inject its
own tenant-approved names and aliases when constructing the extractor.

Explicitly labelled codes are also supported without a database lookup:
`customer code C-01`, `product code P-02`, `branch code B1` and `department
code D1`. Unlabelled short tokens are intentionally ignored to avoid treating
ordinary Vietnamese words as codes.

## Query semantics

The IR builder can emit multiple entity filters, grouped aggregates, one
explicit aggregate HAVING threshold, and TOP-K. Qualified filter/group columns
add their owner tables, so the planner resolves a direct or unique multi-hop
FK path from registry metadata. It never supplies a join path itself.

If metadata has multiple equally short FK paths, planning fails with
`AMBIGUOUS_QUERY`. Explicit catalog roles can safely narrow that tie: a
department code on an employee request selects the registered
`employees.department_id -> departments.id` relationship. Unexplained ties
still fail; no first-path fallback is permitted.

## Scope routing

`ScopeRouter` runs after normalization and before entity extraction and schema
linking. It makes an explainable `IN_SCOPE`, `OUT_OF_SCOPE`, or `AMBIGUOUS`
decision from request structure: database actions, business entities, record
identifiers, temporal/status filters, and explicit non-data requests. A clear
non-data request returns `UNSUPPORTED_QUERY`; an underspecified request returns
`AMBIGUOUS_QUERY`. In both cases schema linking is skipped.

The router is independent of schema aliases and never identifies a table,
column, intent, or SQL. Its separately labelled 60-case benchmark contains 24
in-scope, 24 out-of-scope and 12 ambiguous requests:

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_scope_routing --output evaluation\results\scope_routing_current.json
```

## API identity and errors

`POST /query` accepts only:

```json
{"question": "doanh thu theo chi nhanh"}
```

Identity comes from the `get_authenticated_user` dependency. The current
adapter expects authenticated middleware to set `X-User-Id`, `X-Role`, and
`X-Tenant-Id`; optional employee, department and branch identifiers are also
read from headers. Production integration must replace or front this adapter
with credential-verifying middleware. `user` in the request body is rejected.

Controlled pipeline failures use this stable response:

```json
{"query_id": "...", "error_code": "PERMISSION_DENIED", "message": "..."}
```

`PERMISSION_DENIED` maps to 403; semantic/IR/SQL-validation errors map to 422;
execution failures map to 503. The response never exposes SQL or bind values.

## Observability and regression trend

Every pipeline request emits a terminal event with `query_id`, terminal stage,
outcome, latency and optional error code. It deliberately excludes question
text, identity, SQL and bind values.

Run an execution evaluation against the dedicated seeded database, then render
its metric-only trend dashboard:

```powershell
.\.venv\Scripts\python.exe evaluation\run_execution_eval.py --database-url $evalUrl
.\.venv\Scripts\python.exe evaluation\run_result_accuracy_eval.py --database-url $evalUrl
.\.venv\Scripts\python.exe evaluation\build_regression_dashboard.py
```

To validate the full benchmark's executable schema footprint, first generate
the report-derived manifest, then use it to expand the same dedicated fixture:

```powershell
.\.venv\Scripts\python.exe -m evaluation.run_full_eval --output evaluation\results\full_eval_current.json
.\.venv\Scripts\python.exe evaluation\analyze_execution_coverage.py --report evaluation\results\full_eval_current.json
.\.venv\Scripts\python.exe -m scripts.seed_execution_eval --database-url $evalUrl --coverage-report evaluation\results\execution_coverage.json
.\.venv\Scripts\python.exe evaluation\run_execution_coverage_eval.py --database-url $evalUrl
```

This coverage evaluation executes only QueryIR plans that compile from the
233-case report. It checks that the fixture has the needed tables, columns and
unique FK joins. It does not claim that natural-language linking or returned
business values are correct; retain the ordinary six-case result assertions for
that purpose.

`evaluation/result_accuracy_cases.json` is a separate 60-case benchmark whose
expected scalars, rows and controlled errors are manually specified from the
fixed core rows in `scripts/seed_execution_eval.py`. Run it only after seeding
the coverage fixture:

```powershell
.\.venv\Scripts\python.exe evaluation\run_result_accuracy_eval.py --database-url $evalUrl
```

Its `result_accuracy` is a real PostgreSQL result comparison. It is a baseline
for this fixture and question set, not a production quality claim.

### Result baseline

The latest run on the version-1 coverage fixture passed **60/60 cases
(100%)**. Three employee code-filter cases are resolved through validated
role-specific FK hints, and the dated TOP-K case now uses the direct
transactional date-table neighbor. Their independently specified expected
results were not changed.

The evaluator appends only timestamp, total count and pass rate to
`evaluation/results/regression_history.jsonl`; the dashboard shows the latest
ten samples. These generated artifacts are intentionally ignored by Git.

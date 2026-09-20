# Full pipeline evaluation

Run the evaluator from the repository root:

```powershell
.\.venv\Scripts\python.exe evaluation/run_full_eval.py
```

It expands `evaluation/full_pipeline_cases.json` into 233 cases: 220 frozen schema-linking records plus 13 focused cases for unsupported input, date/number parsing, permission scopes, aggregation, grouping, and top-k planning. The source records remain the source of truth; the runner does not copy or modify their labels.

The generated artifacts are ignored by Git:

- `evaluation/results/full_eval.json` has every stage observation and the primary first-failure reason.
- `reports/generated/full_eval_summary.md` has the metric and bottleneck summary.
- `reports/generated/full_eval_failures.csv` has a compact list for triage.

The evaluator runs entity extraction, deterministic QueryIR construction, permission resolution, planning, joins, and SQL validation. It also runs explicit `query_ir` cases as downstream contract probes. Those probes do not measure natural-language-to-IR accuracy.

The fixed 233-case corpus has no deterministic database fixture spanning all 100 schema tables. The evaluator therefore reports a successfully compiled natural-language case as `SQL_EXECUTION_ERROR` until a matching full-schema seed is available. It does not fill the execution gap with evaluation-only values.

Metrics whose stage is unavailable are reported as `null` in JSON and `not available` in Markdown. Permission violation rate means the fraction of cases expected to be denied that were actually allowed. A controlled unsupported response or a correct permission denial is an end-to-end pass.

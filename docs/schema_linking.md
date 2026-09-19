# Schema linking

`SchemaLinker` maps text to ranked schema candidates and reusable concepts. It is independent of intent classification and can accept a string or the result of `Normalizer.normalize()`.

```python
from src.schema.linker import SchemaLinker

linker = SchemaLinker()  # Create once at startup and reuse.
result = linker.link("doanh thu khách hàng theo chi nhánh")
print(result.model_dump_json(indent=2))

columns = linker.link_columns("name", candidate_tables=["products"], top_k=10)
tables = linker.link_tables("nhan vien", top_k=5)
print(linker.coverage())
```

Results use `schema.table` and `schema.table.column` identifiers. Unique unqualified table names are accepted as context; ambiguous short names are rejected. The actual employee salary column is `hr.employees.base_salary`. `candidate_tables` adds context rather than filtering candidates. Linker scores express evidence strength, not calibrated probabilities.

## Metadata and vocabulary

The checked-in database has **100 tables, 855 columns and 11 namespaces**. The original `dataset_v2/docs/schema_spec.yaml` is an empty placeholder. `schema/schema_spec.yaml` was bootstrapped from the bundled `dataset_v2/sql/01_schema.sql`, including inline and deferred foreign keys, and is now the runtime metadata source. Runtime linking reads neither SQL nor the database. The bootstrap helper only understands the bundled DDL format; it refuses to overwrite an existing output:

```powershell
.\.venv\Scripts\python.exe -m scripts.build_schema_spec --output tmp/schema-review.yaml
```

Maintain structural metadata in `schema/schema_spec.yaml` and vocabulary in `schema/aliases.yaml`. There is no schema-name mapping in Python. Manual aliases outrank metadata labels/descriptions and identifier-derived aliases. All columns receive identifier aliases; business vocabulary is explicit for 657 columns. The dictionary has 100% table coverage and 1,498 alias strings across tables, columns and concepts. Cross-target repeated aliases are intentional ambiguity; duplicate aliases within one target, duplicate YAML keys, unknown references and empty lists are rejected.

The normalizer supplies accent-insensitive matching; do not copy unaccented spellings into YAML. Optional `label`, `description` and `aliases` fields on schema tables/columns also participate in the index. The `concepts` section names semantic associations with explicit table/column targets (currently best sellers and VIP customers). These return candidate evidence, not assertions about which tables must be used.

## Ranking and ambiguity

The index performs token-boundary exact and accent-insensitive phrase matches first, preferring the longest recognized phrases. Uncovered text uses token overlap, prefix/suffix similarity and a trigram-pruned `difflib` fallback. Single-token matches can still participate in a longer approximate phrase. `RankingConfig` centralizes thresholds and score adjustments. BM25/TF-IDF is unnecessary for this initial in-memory vocabulary.

Table context and directly connected foreign-key neighbors raise scores within each match tier. Connectivity does not introduce arbitrary intermediate tables. `RelationshipGraph.find_path()` exposes deterministic graph paths for callers; the linker itself does not choose a join plan.

Ambiguity is retained for generic `name`/`tên`, `phone`, `salary`, `amount`, date fields and repeated foreign-key roles. Near-tied alternatives are reported in `ambiguous_terms`; `unmatched_terms` contains accent-stripped tokens not explained by retained evidence. Dates, numbers and values such as HCM are not schema entities by themselves. Truncating `top_k` does not mean remaining alternatives were ruled out.

## Tests and evaluation

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/schema -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -X utf8 -m evaluation.evaluate_schema_linking --output evaluation/schema_linking_report.json
```

The 272 checked-in evaluation questions include **200 vocabulary coverage cases and 72 synthetic challenge cases**, across all 11 namespaces. Every table is represented. Coverage cases are intentionally derived from vocabulary; challenge questions exercise composition, context, noise, typos and ambiguity. They were used during development and are **not a held-out quality benchmark**. Do not extrapolate their scores to production questions. The report separates both groups and records real timings, startup cost and missing gold targets.

Recall@K is the macro-average of `|gold ∩ predicted[:K]| / |gold|` across cases with nonempty gold targets. Top-1 accuracy means the first prediction belongs to the gold set, which can contain multiple tables/columns. Empty column gold lists are excluded from column metrics. A six-table question cannot attain full Table Recall@5.

Tests validate all references and retrieve every one of the 100 tables and 855 columns by identifier. Timing is measured by the evaluator rather than asserted as a flaky unit-test bound. Startup includes YAML loading and index creation; request latency is measured separately on a reused instance.

## Limits

This is lexical linking: it does not resolve values, negation, intent, SQL, permissions or final joins. Fuzzy matching can return weak alternatives, and short generic terms may remain ambiguous. English singularization is deliberately conservative; irregular forms should be explicit aliases. Relationship boosts use direct neighbors, not global graph optimization. Dictionary edits require rebuilding the linker instance. Production deployments must provide the metadata/alias paths (the Docker image copies `schema/`).

## Business benchmark v1 (provisional)

The original 272 cases remain **regression only**. The additional frozen dataset has 200 questions:

| Split | Linkable | Ambiguous | No schema match | Total |
|---|---:|---:|---:|---:|
| Development | 80 | 10 | 10 | 100 |
| Held-out | 80 | 10 | 10 | 100 |

Each split contains 40 positive business scenarios with two paraphrases each, plus 20 distinct ambiguity/negative scenarios: **60 groups per split**. Positive scenarios span all 11 namespaces. These are correlated paraphrases, not 100 independent observations. Positive cases include indirect wording, unaccented text, noise and typos; negatives include lexical traps such as an everyday phrase containing a business word.

`evaluation/schema_linking_dev.json` is for diagnosis and future tuning. `evaluation/schema_linking_heldout.json` is for release evaluation after selecting changes on development. `evaluation/schema_linking_manifest.json` records split hashes, scenario groups and the pre-evaluation implementation baseline. Tests reject checksum changes, duplicate IDs and cross-split question/group overlap. Hashes normalize line endings/BOM for portability across Windows and Linux. No held-out predictions run in CI; CI only validates its structure and references.

**Provenance:** the same assistant that wrote the linker authored these schema-guided questions and provisional gold labels. They were not generated by reading aliases or selected based on prediction outcomes, but the author already knew the implementation. Consequently, “held-out” describes the tuning split, not independent authorship. Human review and real user questions are still needed before making production-quality claims. The current baseline was evaluated without changing the linker, aliases or ranking thresholds.

### Run and inspect

```powershell
# Safe to use repeatedly while developing.
.\.venv\Scripts\python.exe -X utf8 -m evaluation.evaluate_schema_linking --split dev --output evaluation/schema_linking_dev_report.json

# Use after freezing the release; do not tune to the resulting errors.
.\.venv\Scripts\python.exe -X utf8 -m evaluation.evaluate_schema_linking --split heldout --output evaluation/schema_linking_heldout_report.json

# Keep the older vocabulary/regression results separate.
.\.venv\Scripts\python.exe -X utf8 -m evaluation.evaluate_schema_linking --output evaluation/schema_linking_report.json

.\.venv\Scripts\python.exe -m pytest tests/unit/evaluation -q
```

Reports include dataset and implementation hashes, ranking configuration, metric denominators, per-domain results and error cases with both missing and extra predictions. The console prints summary metrics; complete diagnostics are saved to the requested report. Latency includes the full `link()` call, with startup reported separately. Performance on lexical coverage questions must not be generalized to longer paraphrases.

### Annotation policy

- `link`: annotate the minimal schema sources and requested/filter/grouping columns needed for the business question. Do not add display names, primary keys or intermediate join tables unless explicitly needed by the question. Entity values such as VIP or a city are not themselves database identifiers.
- `ambiguous`: there is insufficient context to select a single interpretation. `acceptable_interpretations` lists at least two plausible alternatives; they are **not** a union of all required tables and are not guaranteed exhaustive. These cases are excluded from ordinary retrieval precision/recall.
- `no_match`: the question does not request information from this enterprise schema. Include lexical traps, not only random strings. Metalinguistic or figurative mentions are useful diagnostics, but a purely lexical linker may not reject them without broader task context; this evaluator adds no intent classifier.
- All new gold is `provisional`, pending human review. A reviewer should inspect source metadata and business meaning, not change labels to fit predictions. Disagreements about minimal sources should be adjudicated before using precision as a release gate.

### Additional metrics

- **Precision@K:** `|gold ∩ returned[:K]| / len(returned[:K])`, macro-averaged on positive annotated cases. Empty predictions score zero. This measures the purity of returned candidates, not a fixed K-slot denominator; read it together with Recall@K. Unannotated column gold in old regression cases is excluded, not treated as negative.
- **False-link rate:** fraction of `no_match` cases returning any table or column. Per-type rates and `no_match_accuracy` are also reported. Ten negatives per split are a diagnostic sample, not a stable population estimate.
- **Ambiguity detection:** fraction of ambiguous cases for which existing `ambiguous_terms` is nonempty and candidates exist. This is a diagnostic proxy; the linker still returns candidates and does not enforce clarification or abstention downstream.
- **Ambiguity option recall@10:** fraction of annotated interpretations whose full table/column sets are present in the returned top ten of each type. It is reported separately from ambiguity detection.
- **False ambiguity / false rejection:** flags on positive cases, so a system cannot appear successful simply by marking everything ambiguous or returning nothing.

No deployment threshold is calibrated by this change. After reviewing development gold, adjust vocabulary or ranking on development only, freeze the chosen version, and then evaluate held-out. Scores are not probabilities. If held-out questions are used to tune a later version, retire that version of the held-out set and collect a new one. Dataset corrections require a reviewed/versioned manifest update; checksums detect accidental edits but are not an access-control mechanism.

### Frozen baseline results

The first baseline run produced the following measurements. No linker, alias or threshold changes were made to fit either new split.

| Metric | Development | Held-out |
|---|---:|---:|
| Table Recall@5 | 88.75% | 90.00% |
| Table Recall@10 | 90.00% | 90.00% |
| Table Precision@5 | 51.90% | 55.21% |
| Table Precision@10 | 51.16% | 53.99% |
| Column Recall@5 | 43.75% | 36.88% |
| Column Recall@10 | 45.00% | 36.88% |
| Column Precision@5 | 32.71% | 24.19% |
| Column Precision@10 | 32.51% | 23.53% |
| Top-1 table accuracy | 76.25% | 82.50% |
| Top-1 column accuracy | 41.25% | 37.50% |
| False links on no-match questions | 6/10 | 5/10 |
| Ambiguity detected | 7/10 | 6/10 |
| Warm latency median | 11.805 ms | 17.168 ms |
| Warm latency p95 | 244.093 ms | 265.454 ms |

These results expose substantial column-retrieval and extra-candidate problems hidden by the earlier vocabulary regression scores. The <50 ms latency target is also not met at p95 on these longer questions. Review development annotations, then diagnose development errors and profile approximate matching before changing the implementation. Held-out error cases should not become alias additions for this benchmark version. The reports remain provisional because the gold has not received independent human review.

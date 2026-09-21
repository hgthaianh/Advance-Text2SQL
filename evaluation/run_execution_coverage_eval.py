"""Execute every compilable full-benchmark QueryIR against the coverage fixture.

This is a schema-execution smoke test. It proves fixture table/column/join
coverage, not natural-language accuracy or business-result correctness.
"""

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine

from src.database.executor import ReadOnlyExecutor
from src.query.handlers.basic import BasicQueryHandler
from src.query.ir import QueryIR
from src.schema.registry import ROOT, SchemaRegistry
from src.validation.sql_validator import ReadOnlySQLValidator


def _hydrate_temporal_filters(query: QueryIR, registry: SchemaRegistry) -> QueryIR:
    """Restore dates lost when QueryIR was serialized into the JSON diagnostics."""
    filters = []
    for condition in [*query.filters, *query.having]:
        value = condition.value
        column = registry.resolve_column(condition.column, query.tables) if condition.column != '*' else None
        data_type = registry.columns[column].data_type.lower() if column else ''
        if isinstance(value, str) and data_type == 'date':
            value = date.fromisoformat(value)
        elif isinstance(value, str) and 'timestamp' in data_type:
            value = datetime.fromisoformat(value)
        filters.append(condition.model_copy(update={'value': value}))
    return query.model_copy(update={
        'filters': filters[:len(query.filters)], 'having': filters[len(query.filters):],
    })


def run(report_path: Path, database_url: str, coverage_path: Path | None = None, limit: int | None = None) -> dict:
    report = json.loads(report_path.read_text(encoding='utf-8'))
    eligible_ids = None
    if coverage_path is not None:
        coverage = json.loads(coverage_path.read_text(encoding='utf-8'))
        eligible_ids = set(coverage['execution_eligible_case_ids'])
    registry = SchemaRegistry()
    handler = BasicQueryHandler(registry)
    validator = ReadOnlySQLValidator()
    engine = create_engine(database_url)
    executor = ReadOnlyExecutor(engine, timeout_ms=500)
    results = []
    try:
        for case in report['cases']:
            if eligible_ids is not None and case['id'] not in eligible_ids:
                continue
            if limit is not None and len(results) >= limit:
                break
            raw_query = case.get('actual', {}).get('query_ir')
            if not raw_query:
                continue
            try:
                query = _hydrate_temporal_filters(QueryIR.model_validate(raw_query), registry)
                compiled = handler.handle(query)
                validator.validate(compiled.statement)
                result = executor.execute(compiled)
                results.append({'id': case['id'], 'passed': True, 'row_count': result.row_count})
            except Exception as exc:
                results.append({'id': case['id'], 'passed': False, 'error': type(exc).__name__})
    finally:
        engine.dispose()
    passed = sum(item['passed'] for item in results)
    return {
        'source_cases': report['summary']['total_cases'], 'execution_eligible_cases': len(results),
        'passed': passed, 'failed': len(results) - passed,
        'metrics': {'fixture_execution_pass_rate': passed / len(results) if results else 0.0},
        'cases': results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--report', type=Path, default=ROOT / 'evaluation/results/full_eval_current.json')
    parser.add_argument('--coverage', type=Path, default=ROOT / 'evaluation/results/execution_coverage.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'evaluation/results/execution_coverage_eval.json')
    parser.add_argument('--limit', type=int, help='Run only the first N eligible plans for diagnosis')
    args = parser.parse_args()
    result = run(args.report, args.database_url, args.coverage, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('source_cases', 'execution_eligible_cases', 'passed', 'failed', 'metrics')}, indent=2))


if __name__ == '__main__':
    main()

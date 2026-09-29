"""Derive the minimal schema fixture required by the full evaluation corpus."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src.query.ir import QueryIR, SelectItem
from src.query.planner import QueryPlanner
from src.schema.registry import ROOT, SchemaRegistry


DEFAULT_REPORT = ROOT / 'evaluation/results/full_eval_current.json'
DEFAULT_OUTPUT = ROOT / 'evaluation/results/execution_coverage.json'


def _columns(query: QueryIR) -> set[str]:
    names: set[str] = set(query.group_by)
    names.update(item.column for item in query.select if isinstance(item, SelectItem) and item.column not in (None, '*'))
    names.update(condition.column for condition in [*query.filters, *query.having] if condition.column != '*')
    names.update(item.column for item in query.order_by if item.column != '*')
    return names


def analyze(report: dict[str, Any], registry: SchemaRegistry | None = None) -> dict[str, Any]:
    """Return only tables, columns and FK edges needed by compilable QueryIRs."""
    registry = registry or SchemaRegistry()
    planner = QueryPlanner(registry)
    table_cases: Counter[str] = Counter()
    column_cases: Counter[str] = Counter()
    join_cases: Counter[tuple[str, str]] = Counter()
    excluded: Counter[str] = Counter()
    eligible_case_ids: list[str] = []
    included_cases = 0
    for case in report['cases']:
        raw_query = case.get('actual', {}).get('query_ir')
        if not raw_query:
            excluded[case.get('primary_failure') or 'NO_QUERY_IR'] += 1
            continue
        try:
            plan = planner.plan(QueryIR.model_validate(raw_query))
        except Exception as exc:
            excluded[type(exc).__name__] += 1
            continue
        included_cases += 1
        eligible_case_ids.append(case['id'])
        query_columns = _columns(plan.query)
        for table in plan.tables:
            table_cases[table] += 1
            if f'{table}.tenant_id' in registry.columns:
                query_columns.add(f'{table}.tenant_id')
        for column in query_columns:
            column_cases[column] += 1
        for edge in plan.joins:
            join_cases[(edge.source, edge.target)] += 1
            column_cases[edge.source] += 1
            column_cases[edge.target] += 1
    tables = sorted(table_cases)
    columns = sorted(column_cases)
    joins = [{'source': source, 'target': target, 'cases': count} for (source, target), count in sorted(join_cases.items())]
    return {
        'source_cases': report['summary']['total_cases'],
        'execution_eligible_cases': included_cases,
        'excluded_cases': sum(excluded.values()),
        'exclusion_reasons': dict(sorted(excluded.items())),
        'table_count': len(tables),
        'column_count': len(columns),
        'join_count': len(joins),
        'execution_eligible_case_ids': eligible_case_ids,
        'tables': [{'name': name, 'cases': table_cases[name]} for name in tables],
        'columns': [{'name': name, 'cases': column_cases[name]} for name in columns],
        'joins': joins,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=DEFAULT_REPORT)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding='utf-8'))
    coverage = analyze(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: coverage[key] for key in (
        'source_cases', 'execution_eligible_cases', 'excluded_cases',
        'table_count', 'column_count', 'join_count',
    )}, indent=2))


if __name__ == '__main__':
    main()

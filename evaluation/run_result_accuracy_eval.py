"""Measure independently specified result accuracy on a seeded PostgreSQL fixture."""

import argparse
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from src.pipeline.orchestrator import Text2SQLPipeline
from src.schema.registry import ROOT
from src.shared.models import UserContext


DEFAULT_CASES = ROOT / 'evaluation/result_accuracy_cases.json'
DEFAULT_OUTPUT = ROOT / 'evaluation/results/result_accuracy_eval.json'


def load_cases(path: Path = DEFAULT_CASES) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding='utf-8'))
    cases = data.get('cases', [])
    ids = [case.get('id') for case in cases]
    if not 60 <= len(cases) <= 80 or len(ids) != len(set(ids)) or any(not case.get('expected') for case in cases):
        raise ValueError('Result-accuracy benchmark requires 60-80 unique cases with independent expected results')
    return data, cases


def _matches(actual: Any, expected: dict[str, Any]) -> bool:
    if 'error_code' in expected:
        return actual.error_code == expected['error_code']
    return actual.success and all(getattr(actual, field) == value for field, value in expected.items())


def run(cases_path: Path, database_url: str) -> dict[str, Any]:
    data, cases = load_cases(cases_path)
    engine = create_engine(database_url)
    pipeline = Text2SQLPipeline(engine=engine)
    reference_date = date.fromisoformat(data['reference_date'])
    results = []
    try:
        for case in cases:
            result = pipeline.execute(
                case['question'], UserContext.model_validate(case['user_context']), reference_date=reference_date,
            )
            expected = case['expected']
            passed = _matches(result, expected)
            results.append({
                'id': case['id'], 'passed': passed, 'expected': expected,
                'actual': result.model_dump(mode='json'),
            })
    finally:
        engine.dispose()
    passed = sum(item['passed'] for item in results)
    families: dict[str, list[bool]] = defaultdict(list)
    for item in results:
        families[item['id'].split('-', 2)[1]].append(item['passed'])
    return {
        'benchmark_version': data['version'], 'expected_source': data['expected_source'],
        'total_cases': len(results), 'passed': passed, 'failed': len(results) - passed,
        'metrics': {'result_accuracy': passed / len(results) if results else 0.0},
        'families': {name: {'cases': len(values), 'result_accuracy': sum(values) / len(values)} for name, values in sorted(families.items())},
        'failure_counts': dict(Counter(
            item['actual'].get('error_code') or 'RESULT_MISMATCH' for item in results if not item['passed']
        )),
        'cases': results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--cases', type=Path, default=DEFAULT_CASES)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run(args.cases, args.database_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('total_cases', 'passed', 'failed', 'metrics', 'families', 'failure_counts')}, indent=2))


if __name__ == '__main__':
    main()

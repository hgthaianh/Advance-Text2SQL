"""Run deterministic result assertions against the dedicated seeded PostgreSQL eval database."""

import argparse
import json
from datetime import date
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine

from src.pipeline.orchestrator import Text2SQLPipeline
from src.schema.registry import ROOT
from src.shared.models import UserContext


def run(cases_path: Path, database_url: str) -> dict:
    data = json.loads(cases_path.read_text(encoding='utf-8'))
    engine = create_engine(database_url)
    pipeline = Text2SQLPipeline(engine=engine)
    reference_date = date.fromisoformat(data['reference_date'])
    results = []
    try:
        for case in data['cases']:
            result = pipeline.execute(case['question'], UserContext.model_validate(case['user_context']), reference_date=reference_date)
            expected = case['expected']
            passed = result.success and all(getattr(result, key) == value for key, value in expected.items() if key != 'error_code')
            if 'error_code' in expected:
                passed = result.error_code == expected['error_code']
            results.append({'id': case['id'], 'passed': passed, 'expected': expected, 'actual': result.model_dump(mode='json')})
    finally:
        engine.dispose()
    passed = sum(row['passed'] for row in results)
    total = len(results)
    return {
        'total_cases': total, 'passed': passed, 'failed': total - passed,
        'metrics': {'execution_pass_rate': passed / total if total else 0.0}, 'cases': results,
    }


def append_history(history_path: Path, report: dict) -> None:
    """Append a metric-only evaluation sample; no question, identity or row data."""
    sample = {
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'execution_pass_rate': report['metrics']['execution_pass_rate'],
        'execution_total_cases': report['total_cases'],
    }
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(sample) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--cases', type=Path, default=ROOT / 'evaluation/execution_cases.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'evaluation/results/execution_eval.json')
    parser.add_argument('--history', type=Path, default=ROOT / 'evaluation/results/regression_history.jsonl')
    args = parser.parse_args()
    report = run(args.cases, args.database_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    append_history(args.history, report)
    print(json.dumps({key: report[key] for key in ('total_cases', 'passed', 'failed')}, indent=2))


if __name__ == '__main__':
    main()

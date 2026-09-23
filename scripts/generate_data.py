"""Install the deterministic, guarded benchmark fixture in an ``*_eval`` DB."""

import argparse
import json
from pathlib import Path

from scripts.seed_execution_eval import seed
from src.schema.registry import ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True, help='Dedicated database whose name ends in _eval')
    parser.add_argument('--coverage-report', type=Path, default=ROOT / 'evaluation/results/execution_coverage.json')
    args = parser.parse_args()
    if not args.coverage_report.exists():
        raise SystemExit(f'Coverage report not found: {args.coverage_report}. Run evaluation/analyze_execution_coverage.py first.')
    seed(args.database_url, json.loads(args.coverage_report.read_text(encoding='utf-8')))
    print('Deterministic benchmark fixture installed.')


if __name__ == "__main__":
    main()

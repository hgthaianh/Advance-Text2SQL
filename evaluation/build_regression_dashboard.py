"""Render a small metric-only regression dashboard from evaluation artifacts."""

import argparse
import json
from pathlib import Path

from src.schema.registry import ROOT


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def _history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def render(execution: dict, full_pipeline: dict, history: list[dict]) -> str:
    """Render only aggregate metrics; source questions and result rows stay private."""
    execution_rate = execution.get('metrics', {}).get('execution_pass_rate')
    if execution_rate is None and execution.get('total_cases'):
        execution_rate = execution.get('passed', 0) / execution['total_cases']
    full_summary = full_pipeline.get('summary', {})
    lines = [
        '# Text2SQL Regression Dashboard', '',
        f'- Execution pass rate: {execution_rate:.1%}' if execution_rate is not None else '- Execution pass rate: unavailable',
        f"- Full pipeline: {full_summary.get('passed', 0)}/{full_summary.get('total_cases', 0)} cases passed" if full_summary else '- Full pipeline: unavailable',
        '', '## Execution trend', '', '| Recorded at | Pass rate | Cases |', '| --- | ---: | ---: |',
    ]
    for sample in history[-10:]:
        lines.append(f"| {sample.get('recorded_at', 'unknown')} | {sample.get('execution_pass_rate', 0):.1%} | {sample.get('execution_total_cases', 0)} |")
    if not history:
        lines.append('| No samples yet | — | — |')
    return '\n'.join(lines) + '\n'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execution', type=Path, default=ROOT / 'evaluation/results/execution_eval.json')
    parser.add_argument('--full-pipeline', type=Path, default=ROOT / 'evaluation/results/full_eval.json')
    parser.add_argument('--history', type=Path, default=ROOT / 'evaluation/results/regression_history.jsonl')
    parser.add_argument('--output', type=Path, default=ROOT / 'evaluation/results/regression_dashboard.md')
    args = parser.parse_args()
    dashboard = render(_read_json(args.execution), _read_json(args.full_pipeline), _history(args.history))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(dashboard, encoding='utf-8')
    print(dashboard)


if __name__ == '__main__':
    main()

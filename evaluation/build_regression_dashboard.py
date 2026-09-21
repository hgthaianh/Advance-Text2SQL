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


def render(execution: dict, full_pipeline: dict, history: list[dict],
           result_accuracy: dict | None = None) -> str:
    """Render only aggregate metrics; source questions and result rows stay private."""
    execution_rate = execution.get('metrics', {}).get('execution_pass_rate')
    if execution_rate is None and execution.get('total_cases'):
        execution_rate = execution.get('passed', 0) / execution['total_cases']
    full_summary = full_pipeline.get('summary', {})
    compile_rate = full_pipeline.get('metrics', {}).get('sql_compile_success')
    compile_samples = full_pipeline.get('metric_samples', {}).get('sql_compile_success', 0)
    result_accuracy = result_accuracy or {}
    result_total = result_accuracy.get('total_cases', 0)
    result_passed = result_accuracy.get('passed', 0)
    result_rate = result_accuracy.get('metrics', {}).get('result_accuracy')
    lines = [
        '# Text2SQL Regression Dashboard', '',
        f'- Execution pass rate: {execution_rate:.1%}' if execution_rate is not None else '- Execution pass rate: unavailable',
        f'- Full-evaluation compile checks: {compile_rate:.1%} (n={compile_samples})'
        if compile_rate is not None
        else f"- Full pipeline: {full_summary.get('passed', 0)}/{full_summary.get('total_cases', 0)} cases passed" if full_summary else '- Full pipeline: unavailable',
        f'- Result accuracy: {result_passed}/{result_total} cases ({result_rate:.1%})'
        if result_rate is not None else '- Result accuracy: unavailable',
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
    parser.add_argument('--result-accuracy', type=Path, default=ROOT / 'evaluation/results/result_accuracy_eval.json')
    parser.add_argument('--history', type=Path, default=ROOT / 'evaluation/results/regression_history.jsonl')
    parser.add_argument('--output', type=Path, default=ROOT / 'evaluation/results/regression_dashboard.md')
    args = parser.parse_args()
    dashboard = render(
        _read_json(args.execution), _read_json(args.full_pipeline), _history(args.history),
        _read_json(args.result_accuracy),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(dashboard, encoding='utf-8')
    print(dashboard)


if __name__ == '__main__':
    main()

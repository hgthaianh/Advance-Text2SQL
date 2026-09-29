"""Evaluate deterministic scope routing on a fixed lexical-trap benchmark."""

import argparse
import json
from collections import Counter
from pathlib import Path

from src.nlp.scope_router import ScopeRouter
from src.schema.registry import ROOT
from src.shared.enums import ScopeDecision


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def evaluate(cases: list[dict], router: ScopeRouter | None = None) -> dict:
    """Return explicit class metrics; labels are never inferred from router output."""
    if not 60 <= len(cases) <= 100 or len({case.get('id') for case in cases}) != len(cases):
        raise ValueError('Scope benchmark requires 60-100 uniquely identified cases')
    router = router or ScopeRouter()
    expected = [ScopeDecision(case['expected']) for case in cases]
    if any(not isinstance(case.get('question'), str) or not case['question'].strip() for case in cases):
        raise ValueError('Every scope case needs a question')
    predicted = [router.route(case['question']).decision for case in cases]
    labels = list(ScopeDecision)
    metrics = {'overall_accuracy': _rate(sum(a == b for a, b in zip(predicted, expected)), len(cases))}
    for label in labels:
        true_positive = sum(a == b == label for a, b in zip(predicted, expected))
        metrics[f'{label.value.lower()}_precision'] = _rate(true_positive, sum(a == label for a in predicted))
        metrics[f'{label.value.lower()}_recall'] = _rate(true_positive, sum(b == label for b in expected))
    metrics['ambiguous_accuracy'] = metrics['ambiguous_recall']
    out_of_scope = [actual for actual, gold in zip(predicted, expected) if gold == ScopeDecision.OUT_OF_SCOPE]
    in_scope = [actual for actual, gold in zip(predicted, expected) if gold == ScopeDecision.IN_SCOPE]
    metrics['out_of_scope_false_link_rate'] = _rate(sum(actual == ScopeDecision.IN_SCOPE for actual in out_of_scope), len(out_of_scope))
    metrics['false_reject_rate'] = _rate(sum(actual != ScopeDecision.IN_SCOPE for actual in in_scope), len(in_scope))
    return {
        'cases': len(cases), 'label_counts': dict(Counter(label.value for label in expected)),
        'metrics': metrics,
        'errors': [
            {'id': case['id'], 'expected': gold.value, 'actual': actual.value}
            for case, actual, gold in zip(cases, predicted, expected) if actual != gold
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=ROOT / 'evaluation/scope_routing_cases.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = evaluate(json.loads(args.cases.read_text(encoding='utf-8')))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'errors'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

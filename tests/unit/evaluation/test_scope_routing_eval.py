import json

from evaluation.evaluate_scope_routing import evaluate
from src.schema.registry import ROOT


def test_fixed_scope_benchmark_is_balanced_and_measurable():
    cases = json.loads((ROOT / 'evaluation/scope_routing_cases.json').read_text(encoding='utf-8'))
    report = evaluate(cases)

    assert report['label_counts'] == {'IN_SCOPE': 24, 'OUT_OF_SCOPE': 24, 'AMBIGUOUS': 12}
    assert report['metrics']['out_of_scope_false_link_rate'] == 0
    assert report['metrics']['in_scope_recall'] >= .95

from evaluation.run_result_accuracy_eval import _matches, load_cases
from src.schema.registry import ROOT
from src.shared.models import QueryResult


def test_result_accuracy_dataset_has_independent_expected_results_in_required_range():
    data, cases = load_cases(ROOT / 'evaluation/result_accuracy_cases.json')

    assert data['expected_source'].startswith('Manually specified')
    assert len(cases) == 60
    assert all(case['expected'] for case in cases)


def test_result_matcher_handles_values_and_controlled_errors():
    assert _matches(QueryResult(success=True, scalar=3), {'scalar': 3})
    assert _matches(QueryResult(success=False, error_code='PERMISSION_DENIED'), {'error_code': 'PERMISSION_DENIED'})
    assert not _matches(QueryResult(success=True, scalar=2), {'scalar': 3})

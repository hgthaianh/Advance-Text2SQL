from evaluation.build_regression_dashboard import render


def test_dashboard_reports_aggregate_metrics_without_case_contents():
    dashboard = render(
        {'metrics': {'execution_pass_rate': 1.0}},
        {'summary': {'passed': 10, 'total_cases': 12}},
        [{'recorded_at': '2026-09-20T00:00:00+00:00', 'execution_pass_rate': 0.9, 'execution_total_cases': 10}],
    )

    assert '100.0%' in dashboard
    assert '10/12' in dashboard
    assert '90.0%' in dashboard


def test_dashboard_labels_full_evaluation_compile_probes_accurately():
    dashboard = render(
        {}, {'metrics': {'sql_compile_success': 0.95}, 'metric_samples': {'sql_compile_success': 20}}, [],
    )

    assert 'Full-evaluation compile checks: 95.0% (n=20)' in dashboard


def test_dashboard_reports_independent_result_accuracy_when_available():
    dashboard = render(
        {}, {}, [],
        {'total_cases': 60, 'passed': 60, 'metrics': {'result_accuracy': 1.0}},
    )

    assert 'Result accuracy: 60/60 cases (100.0%)' in dashboard

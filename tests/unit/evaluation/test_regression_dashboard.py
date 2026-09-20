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

from datetime import date

from evaluation.run_full_eval import (
    _date_matches,
    _first_observable_failure,
    evaluate,
    load_cases,
    write_reports,
)


def test_full_case_set_is_unique_and_in_target_range():
    cases, reference_date = load_cases()

    assert reference_date == date(2026, 9, 19)
    assert 150 <= len(cases) <= 300
    assert len({case['id'] for case in cases}) == len(cases)
    assert any(case['id'] == 'FP-PERM-DENY-SALARY' for case in cases)


def test_first_failure_prefers_available_earlier_stage():
    checks = {
        'intent': True,
        'date': True,
        'number': True,
        'table_recall_at_5': False,
        'column_recall_at_5': False,
    }

    assert _first_observable_failure(checks) == 'TABLE_LINK_WRONG'
    assert _date_matches({'start': '2026-08-01', 'end': '2026-09-01', 'raw_text': 'thang truoc'}, {
        'start': '2026-08-01', 'end': '2026-09-01',
    })


def test_evaluation_reports_compiled_bridge_and_writes_artifacts(tmp_path):
    cases, reference_date = load_cases()
    report = evaluate([next(case for case in cases if case['id'] == 'FP-DATE-1')], reference_date)

    result = report['cases'][0]
    assert result['checks']['date'] is True
    assert result['primary_failure'] == 'SQL_EXECUTION_ERROR'
    assert report['metrics']['entity_extraction_accuracy'] is None
    assert report['stage_availability']['entity_extraction'] is True
    assert report['stage_availability']['database_execution'] is False

    output, summary, failures = (tmp_path / 'result.json', tmp_path / 'summary.md', tmp_path / 'failures.csv')
    write_reports(report, output, summary, failures)
    assert 'SQL_EXECUTION_ERROR' in output.read_text(encoding='utf-8')
    assert 'Final-result accuracy' in summary.read_text(encoding='utf-8')
    assert 'primary_failure' in failures.read_text(encoding='utf-8')

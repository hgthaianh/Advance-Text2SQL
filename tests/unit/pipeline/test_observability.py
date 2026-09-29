from sqlalchemy import create_engine, text

from src.pipeline.observability import PersistedPipelineObserver, PipelineEvent
from src.pipeline.orchestrator import Text2SQLPipeline
from src.shared.exceptions import FanoutRiskError


def test_fanout_risk_is_exposed_as_a_controlled_ambiguity():
    assert Text2SQLPipeline._error_code(FanoutRiskError('unsafe allocation')) == 'AMBIGUOUS_QUERY'


def test_persisted_audit_keeps_safe_fields_and_aggregates_stage_failures():
    engine = create_engine('sqlite://')
    observer = PersistedPipelineObserver(engine)
    observer.record(PipelineEvent(
        query_id='safe-id', outcome='failure', stage='sql_validation', duration_ms=12.5,
        error_code='SQL_VALIDATION_ERROR', stage_durations_ms={'preprocess': 1.0, 'sql_validation': 2.0},
    ))

    with engine.connect() as connection:
        row = connection.execute(text('SELECT * FROM text2sql_query_audits')).mappings().one()
    assert set(row) == {
        'query_id', 'recorded_at', 'outcome', 'stage', 'duration_ms', 'error_code', 'stage_durations_json',
        'sql_text', 'row_count', 'truncated',
    }
    assert 'token' not in str(row).lower()
    assert 'password' not in str(row).lower()
    summary = observer.aggregate()
    assert summary['failure_by_stage'] == {'sql_validation': 1}
    assert summary['error_categories'] == {'SQL_VALIDATION_ERROR': 1}
    assert summary['stage_latency_ms']['preprocess']['average'] == 1.0


def test_persisted_audit_retains_only_the_configured_recent_records():
    observer = PersistedPipelineObserver(create_engine('sqlite://'), max_records=2)
    for index in range(3):
        observer.record(PipelineEvent(
            query_id=f'id-{index}', outcome='success', stage='execution', duration_ms=float(index),
        ))

    assert observer.aggregate()['total_requests'] == 2

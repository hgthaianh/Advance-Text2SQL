import json
import logging

from sqlalchemy import create_engine, text

from src.pipeline.observability import (
    LoggingPipelineObserver, PersistedPipelineObserver, PipelineEvent, safe_bind_params,
)
from src.pipeline.orchestrator import Text2SQLPipeline
from src.shared.exceptions import FanoutRiskError


def test_fanout_risk_is_exposed_as_a_controlled_ambiguity():
    assert Text2SQLPipeline._error_code(FanoutRiskError('unsafe allocation')) == 'AMBIGUOUS_QUERY'


def test_persisted_audit_keeps_safe_fields_and_aggregates_stage_failures():
    engine = create_engine('sqlite://')
    observer = PersistedPipelineObserver(engine)
    observer.record(PipelineEvent(
        query_id='safe-id', outcome='failure', stage='sql_validation', duration_ms=12.5,
        error_code='SQL_VALIDATION_ERROR', question='show employee salaries',
        stage_durations_ms={'preprocess': 1.0, 'sql_validation': 2.0},
    ))

    with engine.connect() as connection:
        row = connection.execute(text('SELECT * FROM text2sql_query_audits')).mappings().one()
    assert set(row) == {
        'query_id', 'recorded_at', 'outcome', 'stage', 'duration_ms', 'error_code', 'stage_durations_json',
        'question_text', 'sql_text', 'row_count', 'truncated', 'safe_params_json',
    }
    assert row['question_text'] == 'show employee salaries'
    assert 'token' not in str(row).lower()
    assert 'password' not in str(row).lower()
    summary = observer.aggregate()
    assert summary['failure_by_stage'] == {'sql_validation': 1}
    assert summary['error_categories'] == {'SQL_VALIDATION_ERROR': 1}
    assert summary['stage_latency_ms']['preprocess']['average'] == 1.0


def test_admin_trace_masks_text_binds_but_retains_safe_calendar_values_and_stage_order():
    engine = create_engine('sqlite://')
    observer = PersistedPipelineObserver(engine)
    observer.record(PipelineEvent(
        query_id='trace-with-binds', outcome='success', stage='execution', duration_ms=12,
        stage_durations_ms={'preprocess': 1.0, 'semantic_review': 2.0, 'execution': 3.0},
        safe_params=safe_bind_params({'p0': 'secret-token', 'p1': '2026-09-30', 'p2': 3}),
    ))

    history = observer.history(query_id='trace-with-binds')[0]
    assert list(history['stage_durations_ms']) == ['preprocess', 'semantic_review', 'execution']
    assert history['params'] == {'p0': '***', 'p1': '2026-09-30', 'p2': 3}


def test_persisted_audit_retains_only_the_configured_recent_records():
    observer = PersistedPipelineObserver(create_engine('sqlite://'), max_records=2)
    for index in range(3):
        observer.record(PipelineEvent(
            query_id=f'id-{index}', outcome='success', stage='execution', duration_ms=float(index),
        ))

    assert observer.aggregate()['total_requests'] == 2


def test_structured_logs_redact_prompt_and_sql(caplog):
    logger = logging.getLogger('test.pipeline.observability')
    observer = LoggingPipelineObserver(logger)

    with caplog.at_level(logging.INFO, logger=logger.name):
        observer.record(PipelineEvent(
            query_id='safe-id', outcome='success', stage='execution', duration_ms=1,
            question='private prompt', sql='SELECT private_column FROM private_table',
        ))

    payload = json.loads(caplog.records[-1].message.removeprefix('pipeline_event='))
    assert payload['question'] is None
    assert payload['sql'] is None

"""Public-pipeline execution proof for a registered composite FK fixture."""

from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from src.nlp.entity_extractor import EntityExtractor
from src.pipeline.orchestrator import Text2SQLPipeline
from src.schema.linker import SchemaLinker
from src.schema.registry import SchemaRegistry
from src.shared.enums import Intent, ScopeDecision
from src.shared.models import IntentResult, PreprocessedQuery, SchemaColumn, SchemaRelationship, SchemaTable, ScopeRoutingResult, UserContext


class _StaticNLP:
    def process_query(self, question, _reference_date):
        return PreprocessedQuery(original_text=question, normalized_text=question.lower(), accent_stripped_text=question.lower(), intent_result=IntentResult(intent=Intent.LIST, confidence=1, reason='fixture'))


class _InScope:
    def route(self, _preprocessed):
        return ScopeRoutingResult(decision=ScopeDecision.IN_SCOPE, confidence=1, reasons=['fixture'])


class _NoEntities(EntityExtractor):
    def extract(self, _text):
        return []


def test_composite_fk_executes_through_linker_ir_planner_and_public_pipeline():
    registry = SchemaRegistry(
        tables=[
            SchemaTable(name='parents', schema_name='public', columns=[SchemaColumn(name='tenant_key', data_type='integer', primary_key=True), SchemaColumn(name='parent_key', data_type='integer', primary_key=True), SchemaColumn(name='name', data_type='text')]),
            SchemaTable(name='children', schema_name='public', columns=[SchemaColumn(name='id', data_type='integer', primary_key=True), SchemaColumn(name='tenant_key', data_type='integer'), SchemaColumn(name='parent_key', data_type='integer')]),
        ],
        relationships=[SchemaRelationship(source='public.children.parent_key', target='public.parents.parent_key', composite_keys=[('public.children.tenant_key', 'public.parents.tenant_key'), ('public.children.parent_key', 'public.parents.parent_key')])],
    )
    aliases = Path(__file__).parents[2] / 'fixtures' / 'composite_fk_aliases.yaml'
    linker = SchemaLinker(registry, aliases_path=aliases)
    linked = linker.link('list child record ids with parent records')
    assert {candidate.name for candidate in linked.tables} >= {'public.children', 'public.parents'}
    assert 'public.children.id' in {candidate.name for candidate in linked.columns}

    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS public")
            connection.exec_driver_sql('CREATE TABLE public.parents (tenant_key INTEGER, parent_key INTEGER, name TEXT)')
            connection.exec_driver_sql('CREATE TABLE public.children (id INTEGER, tenant_key INTEGER, parent_key INTEGER)')
            connection.exec_driver_sql("INSERT INTO public.parents VALUES (1, 10, 'P1'), (2, 10, 'P2')")
            connection.exec_driver_sql('INSERT INTO public.children VALUES (100, 1, 10), (200, 2, 10)')
        pipeline = Text2SQLPipeline(registry=registry, engine=engine, nlp=_StaticNLP(), linker=linker, scope_router=_InScope(), entity_extractor=_NoEntities())
        result = pipeline.execute('list child record ids with parent records', UserContext(user_id='admin', role='admin', tenant_id='T1'), reference_date=date(2026, 9, 20))
    finally:
        engine.dispose()

    assert result.success
    assert result.rows == [{'id': 100}, {'id': 200}]

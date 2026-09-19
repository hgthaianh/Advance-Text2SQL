"""Metric arithmetic and frozen split checks; never predict held-out cases in CI."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation.evaluate_schema_linking import MANIFEST, content_hash, evaluate, load_benchmark, validate_cases
from src.nlp.normalizer import Normalizer
from src.schema.registry import ROOT, SchemaRegistry
from src.shared.models import SchemaCandidate, SchemaLinkResult


@pytest.fixture(scope='module')
def registry():
    return SchemaRegistry()


def case(behavior='link', **updates):
    data = dict(id='case', domain='sales', question='question', expected_behavior=behavior,
                expected_tables=['sales.orders'], expected_columns=['sales.orders.total_amount'])
    if behavior != 'link':
        data.update(expected_tables=[], expected_columns=[])
    data.update(updates)
    return data


def prediction(tables=(), columns=(), ambiguous=False):
    def candidate(name, kind):
        return SchemaCandidate(name=name, candidate_type=kind, score=.9,
                               matched_text='text', matched_alias='text', match_type='exact', alias_source='manual')
    return SchemaLinkResult(tables=[candidate(n, 'table') for n in tables],
                            columns=[candidate(n, 'column') for n in columns],
                            ambiguous_terms=['text'] if ambiguous else [])


def stub(registry, outputs):
    return SimpleNamespace(registry=registry, coverage=lambda: {}, link=lambda text, **kwargs: outputs[text])


def test_precision_penalizes_extra_predictions(registry):
    result = prediction(['sales.orders', 'customer.customers'], ['sales.orders.total_amount', 'sales.orders.id'])
    report = evaluate(stub(registry, {'question': result}), [case()])
    assert report['metrics']['table_recall_at_5'] == 1
    assert report['metrics']['table_precision_at_5'] == .5
    assert report['metrics']['column_precision_at_10'] == .5
    assert report['metric_case_counts']['table_precision_at_5'] == 1
    assert report['errors'][0]['unexpected']['table'] == ['customer.customers']


def test_precision_uses_actual_returned_count(registry):
    report = evaluate(stub(registry, {'question': prediction(['sales.orders'], ['sales.orders.total_amount'])}), [case()])
    assert report['metrics']['table_precision_at_5'] == 1
    assert report['metrics']['table_precision_at_10'] == 1


def test_empty_predictions_count_as_zero_on_positive_gold(registry):
    report = evaluate(stub(registry, {'question': prediction()}), [case()])
    assert report['metrics']['table_precision_at_5'] == 0
    assert report['metrics']['column_recall_at_10'] == 0
    assert report['metrics']['false_rejection_rate'] == 1


def test_top_five_vs_ten_and_macro_average(registry):
    tables = ['hr.employees', 'org.branches', 'product.products', 'finance.budgets', 'sales.payments', 'sales.orders']
    cases = [case(question='first'), case(id='other', question='second', expected_tables=['sales.orders', 'sales.payments'], expected_columns=[])]
    report = evaluate(stub(registry, {'first': prediction(tables), 'second': prediction(['sales.orders'])}), cases)
    assert report['metrics']['table_recall_at_5'] == .25
    assert report['metrics']['table_recall_at_10'] == .75
    assert report['metrics']['top_1_table_accuracy'] == .5
    assert report['metric_case_counts']['column_recall_at_5'] == 1


def test_negative_cases_do_not_inflate_positive_metrics(registry):
    cases = [case(question='yes'), case('no_match', id='no', question='no')]
    report = evaluate(stub(registry, {'yes': prediction(['sales.orders']), 'no': prediction()}), cases)
    assert report['metrics']['table_precision_at_5'] == 1
    assert report['metric_case_counts']['table_precision_at_5'] == 1
    assert report['metrics']['no_match_accuracy'] == 1
    assert report['metrics']['false_link_rate'] == 0


def test_negative_false_links_include_column_only(registry):
    report = evaluate(stub(registry, {'question': prediction(columns=['sales.orders.total_amount'])}), [case('no_match')])
    assert report['metrics']['false_link_rate'] == 1
    assert report['metrics']['column_false_link_rate'] == 1
    assert report['metrics']['table_false_link_rate'] == 0
    assert 'table_recall_at_5' not in report['metrics']
    assert report['metrics']['no_match_accuracy'] == 0


def ambiguous_case():
    return case('ambiguous', acceptable_interpretations=[
        dict(tables=['customer.customers'], columns=['customer.customers.name']),
        dict(tables=['product.products'], columns=['product.products.name']),
    ])


def test_ambiguity_is_not_a_union_of_required_gold(registry):
    result = prediction(['customer.customers'], ['customer.customers.name'], ambiguous=True)
    report = evaluate(stub(registry, {'question': result}), [ambiguous_case()])
    assert report['metrics']['ambiguity_detection_rate'] == 1
    assert report['metrics']['ambiguity_option_recall_at_10'] == .5
    assert 'column_recall_at_10' not in report['metrics']
    assert report['failures_at_10'] == []


def test_ambiguity_missing_one_column_does_not_cover_option(registry):
    report = evaluate(stub(registry, {'question': prediction(['customer.customers', 'product.products'], ambiguous=True)}), [ambiguous_case()])
    assert report['metrics']['ambiguity_option_recall_at_10'] == 0


def test_empty_ambiguity_flag_does_not_hide_no_match(registry):
    report = evaluate(stub(registry, {'question': prediction(ambiguous=True)}), [ambiguous_case()])
    assert report['metrics']['ambiguity_detection_rate'] == 0


def test_positive_false_ambiguity_is_reported(registry):
    report = evaluate(stub(registry, {'question': prediction(['sales.orders'], ambiguous=True)}), [case()])
    assert report['metrics']['false_ambiguity_rate'] == 1


@pytest.mark.parametrize('bad', [
    case(expected_behavior='unknown'), case(question=' '), case(expected_tables=['missing']),
    case(expected_columns=['sales.orders.missing']), case(expected_tables=['sales.orders'] * 2),
    case(expected_columns=[] , expected_tables=[]), case('no_match', expected_tables=['sales.orders']),
    case('ambiguous'), case('ambiguous', acceptable_interpretations=[{'tables': [], 'columns': []}] * 2),
    case(acceptable_interpretations=[{'tables': ['sales.orders'], 'columns': []}]),
    case('no_match', acceptable_interpretations=[{'tables': ['sales.orders'], 'columns': []}]),
])
def test_bad_gold_is_rejected(registry, bad):
    with pytest.raises(ValueError):
        validate_cases([bad], registry)


def test_invalid_alternative_reference_is_rejected(registry):
    bad = ambiguous_case()
    bad['acceptable_interpretations'][0]['columns'] = ['products.fake']
    with pytest.raises(ValueError): validate_cases([bad], registry)
    bad = ambiguous_case()
    bad['acceptable_interpretations'][1] = copy.deepcopy(bad['acceptable_interpretations'][0])
    with pytest.raises(ValueError): validate_cases([bad], registry)


def test_frozen_dataset_structure_and_nonoverlap(registry):
    dev, manifest = load_benchmark('dev')
    heldout, _ = load_benchmark('heldout')
    assert manifest['gold_status'] == 'provisional_pending_human_review'
    for split, cases in [('dev', dev), ('heldout', heldout)]:
        validate_cases(cases, registry)
        assert len(cases) == 100
        assert len({c['group_id'] for c in cases}) == 60
        assert sum(c['expected_behavior'] == 'link' for c in cases) == 80
        assert sum(c['expected_behavior'] == 'ambiguous' for c in cases) == 10
        assert sum(c['expected_behavior'] == 'no_match' for c in cases) == 10
        assert len({c['domain'] for c in cases if c['expected_behavior'] == 'link'}) == 11
        assert {c['gold_status'] for c in cases} == {'provisional'}
    assert not {c['group_id'] for c in dev} & {c['group_id'] for c in heldout}
    regression = json.loads((ROOT / 'evaluation/schema_linking_cases.json').read_text(encoding='utf-8'))
    normalized = lambda cases: {Normalizer().normalize(c['question']).accent_stripped_text for c in cases}
    assert not normalized(heldout) & normalized(regression)


def test_hash_is_portable(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.write_bytes(b'first\nsecond\n')
    second.write_bytes(b'\xef\xbb\xbffirst\r\nsecond\r\n')
    assert content_hash(first) == content_hash(second)


def frozen_copy(tmp_path):
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    for entry in manifest['splits'].values():
        (tmp_path / entry['file']).write_bytes((MANIFEST.parent / entry['file']).read_bytes())
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path, manifest


def test_checksum_rejects_silent_gold_edit(tmp_path):
    path, manifest = frozen_copy(tmp_path)
    dataset = tmp_path / manifest['splits']['heldout']['file']
    cases = json.loads(dataset.read_text(encoding='utf-8'))
    cases[0]['question'] += ' modified'
    dataset.write_text(json.dumps(cases), encoding='utf-8')
    with pytest.raises(ValueError, match='checksum'):
        load_benchmark('heldout', path)


@pytest.mark.parametrize('field', ['group_id', 'question'])
def test_leakage_rejected_even_if_checksum_updated(tmp_path, field):
    path, manifest = frozen_copy(tmp_path)
    dev = json.loads((tmp_path / manifest['splits']['dev']['file']).read_text(encoding='utf-8'))
    dataset = tmp_path / manifest['splits']['heldout']['file']
    cases = json.loads(dataset.read_text(encoding='utf-8'))
    old = cases[0][field]
    for c in cases:
        if c[field] == old: c[field] = dev[0][field]
    dataset.write_text(json.dumps(cases), encoding='utf-8')
    manifest['splits']['heldout']['sha256'] = content_hash(dataset)
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='leakage'):
        load_benchmark('heldout', path)

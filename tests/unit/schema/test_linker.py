from dataclasses import replace
from pathlib import Path
import unicodedata

import pytest
import yaml

from src.nlp.normalizer import Normalizer
from src.schema.linker import RankingConfig, SchemaLinker
from src.schema.registry import SchemaRegistry


@pytest.fixture(scope='module')
def linker():
    return SchemaLinker()


@pytest.mark.parametrize('question, table', [
    ('khách hàng', 'customer.customers'), ('khach hang', 'customer.customers'),
    ('nhân viên', 'hr.employees'), ('nhan vien', 'hr.employees'),
    ('customer', 'customer.customers'), ('employee', 'hr.employees'),
    ('DOANH THU', 'sales.orders'), ('nhà cung cấp', 'procurement.suppliers'),
    ('ngân sách', 'finance.budgets'), ('yêu cầu hỗ trợ', 'support.support_tickets'),
])
def test_table_mentions(linker, question, table):
    result = linker.link_tables(question)
    assert result[0].name == table
    assert result[0].candidate_type == 'table'
    assert result[0].score > .9


@pytest.mark.parametrize('question, column', [
    ('doanh thu', 'sales.orders.total_amount'),
    ('lương nhân viên', 'hr.employees.base_salary'),
    ('employee salary', 'hr.employees.base_salary'),
    ('salary employee', 'hr.employees.base_salary'),
    ('tên sản phẩm', 'product.products.name'),
    ('tên khách hàng', 'customer.customers.name'),
    ('số điện thoại khách hàng', 'customer.customers.phone'),
    ('so dien thoai khach hang', 'customer.customers.phone'),
    ('chi nhánh theo tên', 'org.branches.name'),
    ('name customers', 'customer.customers.name'),
    ('tổng giá trị đơn hàng', 'sales.orders.total_amount'),
    ('số lượng tồn kho', 'inventory.inventory_balances.qty_on_hand'),
])
def test_column_linking(linker, question, column):
    result = linker.link_columns(question)
    assert result[0].name == column
    assert result[0].matched_text
    assert result[0].matched_alias


def test_multiple_tables_and_noise(linker):
    for text in ('doanh thu khách hàng theo chi nhánh', 'doanh   thu khach hang chi nhanh HCM!!!'):
        result = linker.link(text)
        assert {'sales.orders', 'customer.customers', 'org.branches'} <= {t.name for t in result.tables}
        assert 'sales.orders.total_amount' in {c.name for c in result.columns}


def test_long_query_concepts(linker):
    result = linker.link('top 5 sản phẩm bán chạy của khách hàng vip tại chi nhánh hcm tháng trước')
    assert {'product.products', 'sales.order_items', 'sales.orders', 'customer.customers', 'customer.customer_groups', 'org.branches'} <= {t.name for t in result.tables}
    assert {c.name for c in result.concepts} == {'best_sellers', 'vip_customers', 'ho_chi_minh'}
    assert any(c.reason == 'best_sellers' and c.match_type == 'related_business_concept' for c in result.tables)
    assert 'hcm' not in result.unmatched_terms
    assert 'org.locations.city' in {c.name for c in result.columns}
    assert 'sản phẩm bán chạy' not in result.ambiguous_terms


def test_longest_phrase_wins(linker):
    result = linker.link('chi tiết đơn mua hàng')
    assert result.tables[0].name == 'procurement.purchase_order_items'
    assert all(c.matched_text != 'đơn hàng' for c in result.tables)
    assert result.tables[0].matched_text == 'chi tiết đơn mua hàng'


def test_ambiguity_is_retained(linker):
    result = linker.link('name', column_top_k=100)
    assert {'customer.customers.name', 'product.products.name', 'org.branches.name'} <= {c.name for c in result.columns}
    assert 'name' in result.ambiguous_terms
    assert len({c.score for c in result.columns}) == 1


def test_explicit_table_context_reranks_without_filtering(linker):
    result = linker.link_columns('name', candidate_tables=['products'], top_k=100)
    assert result[0].name == 'product.products.name'
    assert 'customer.customers.name' in {c.name for c in result}
    with pytest.raises(ValueError, match='table'):
        linker.link_columns('name', candidate_tables=['missing'])


def test_relationship_reranking(linker):
    without = SchemaLinker(config=replace(linker.config, relationship_boost=0))
    text = 'doanh thu chi nhánh'
    with_score = linker.link_columns(text)[0]
    without_score = without.link_columns(text)[0]
    assert with_score.name == without_score.name == 'sales.orders.total_amount'
    assert with_score.score > without_score.score
    assert with_score.reason == 'foreign_key_neighbor_of_context'


@pytest.mark.parametrize('query, table, match_type', [('custmoer', 'customer.customers', 'fuzzy'), ('nhan vienn', 'hr.employees', 'prefix')])
def test_fuzzy_fallback(linker, query, table, match_type):
    candidates = linker.link_tables(query)
    assert candidates[0].name == table
    assert candidates[0].match_type == match_type
    assert candidates[0].score < linker.link_tables('customer')[0].score


def test_prefix_fallback(linker):
    result = linker.link_tables('custome')
    assert result[0].name == 'customer.customers'
    assert result[0].match_type == 'prefix'


def test_exact_accent_score_order(linker):
    accented = linker.link_tables('nhân viên')[0]
    plain = linker.link_tables('nhan vien')[0]
    assert accented.match_type == 'exact'
    assert plain.match_type == 'accent_insensitive'
    assert accented.score > plain.score


@pytest.mark.parametrize('query', ['', '   ', 'của các cho và là được trong theo', 'zzqxzv 12345'])
def test_empty_stopwords_and_unknown(linker, query):
    result = linker.link(query)
    assert result.tables == []
    assert result.columns == []


def test_token_boundaries(linker):
    assert all(c.match_type not in ('exact', 'accent_insensitive') for c in linker.link_tables('xemployeez'))


def test_table_mention_does_not_imply_primary_key(linker):
    assert 'customer.customers.id' not in {c.name for c in linker.link_columns('customers')}


def test_repeated_mentions_are_not_unmatched(linker):
    assert linker.link('khách hàng customer').unmatched_terms == []


def test_unicode_and_normalizer_contract(linker):
    text = 'Tên sản phẩm'
    expected = linker.link(text).model_dump()
    assert linker.link(unicodedata.normalize('NFD', text)).model_dump() == expected
    assert linker.link(Normalizer().normalize(text)).model_dump() == expected


def test_deterministic_top_k_and_json(linker):
    first = linker.link('name', table_top_k=3, column_top_k=4)
    assert len(first.tables) == 3 and len(first.columns) == 4
    assert first.model_dump_json() == linker.link('name', table_top_k=3, column_top_k=4).model_dump_json()
    assert linker.link_tables('name', top_k=0) == []
    assert linker.link_columns('name', top_k=0) == []
    with pytest.raises(ValueError): linker.link('name', table_top_k=-1)
    with pytest.raises(ValueError): linker.link_tables('name', top_k=-1)
    with pytest.raises(ValueError): linker.link_columns('name', top_k=-1)


def test_no_request_time_file_reads(linker, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('Request tried to reread a file')
    monkeypatch.setattr(Path, 'open', fail)
    assert linker.link('doanh thu').columns[0].name == 'sales.orders.total_amount'


@pytest.mark.parametrize('options', [dict(fuzzy_match_min=0), dict(token_match_min=1.1), dict(exact_score=.5), dict(context_boost=-1), dict(min_fuzzy_chars=0)])
def test_config_validation(options):
    with pytest.raises(ValueError): RankingConfig(**options)


def test_manual_schema_automatic_priority_and_token_matching(tmp_path):
    schema = tmp_path / 'schema.yaml'
    aliases = tmp_path / 'aliases.yaml'
    schema.write_text(yaml.safe_dump({'tables': [
        dict(name='widgets', label='special metric', columns=[dict(name='special_metric', data_type='int')]),
        dict(name='measurements', columns=[dict(name='value', data_type='int')]),
    ]}), encoding='utf-8')
    aliases.write_text(yaml.safe_dump({'columns': {'measurements.value': {'aliases': ['special metric']}}}), encoding='utf-8')
    linker = SchemaLinker(SchemaRegistry(schema), aliases)
    result = linker.link('special metric')
    assert result.columns[0].name == 'public.measurements.value'
    assert result.columns[0].alias_source == 'manual'
    assert next(c for c in result.tables if c.name == 'public.widgets').alias_source == 'schema'
    assert next(c for c in result.columns if c.name == 'public.widgets.special_metric').alias_source == 'automatic'
    reordered = linker.link_columns('metric special')
    assert reordered[0].name == 'public.measurements.value'
    assert reordered[0].match_type == 'token'


# Every schema item must actually be retrievable, not just counted in a report.
_REGISTRY = SchemaRegistry()


@pytest.mark.parametrize('name', sorted(_REGISTRY.tables))
def test_every_table_is_linkable(linker, name):
    assert linker.link_tables(name)[0].name == name


@pytest.mark.parametrize('name', sorted(_REGISTRY.columns))
def test_every_column_is_linkable(linker, name):
    assert linker.link_columns(name)[0].name == name

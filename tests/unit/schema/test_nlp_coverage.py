"""Tiered, dataset-driven regression coverage for practical schema vocabulary."""

import json

from evaluation.evaluate_schema_linking import evaluate, validate_cases
from src.schema.linker import SchemaLinker
from src.schema.registry import ROOT, SchemaRegistry


CASES_PATH = ROOT / 'evaluation/schema_nlp_coverage_cases.json'
CORE_TABLES = {
    'org.branches', 'org.departments', 'hr.employees',
    'customer.customers', 'customer.customer_groups',
    'product.products', 'product.categories',
    'inventory.warehouses', 'inventory.inventory_balances',
    'inventory.inventory_transactions',
    'procurement.suppliers', 'procurement.purchase_orders', 'procurement.goods_receipts',
    'sales.orders', 'sales.order_items', 'sales.invoices', 'sales.payments', 'sales.quotations',
    'finance.budgets', 'support.support_tickets',
}


def cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding='utf-8'))


def test_tiered_dataset_is_valid_and_covers_core_business_tables():
    dataset = cases()
    validate_cases(dataset, SchemaRegistry())
    assert {case['tier'] for case in dataset} == {'A', 'B'}
    assert CORE_TABLES <= {table for case in dataset for table in case['expected_tables']}


def test_core_abbreviations_and_location_concept_are_linked():
    linker = SchemaLinker()
    assert linker.link_tables('NV')[0].name == 'hr.employees'
    assert linker.link_tables('KH')[0].name == 'customer.customers'
    assert linker.link_tables('SP')[0].name == 'product.products'
    hcm = linker.link('chi nhanh HCM')
    assert 'ho_chi_minh' in {candidate.name for candidate in hcm.concepts}
    assert 'org.locations.city' in {candidate.name for candidate in hcm.columns}


def test_tiered_retrieval_metrics_are_measurable():
    report = evaluate(SchemaLinker(), cases())
    core, extended = report['metrics_by_tier']['A'], report['metrics_by_tier']['B']
    assert core['table_recall_at_1'] >= .95
    assert core['column_recall_at_1'] >= .90
    assert core['table_recall_at_5'] >= .95
    assert extended['table_recall_at_1'] >= .95
    assert extended['table_recall_at_5'] >= .95
    assert extended['column_recall_at_1'] >= .90
    assert extended['column_recall_at_5'] >= .90
    assert extended['no_match_accuracy'] == 1
    assert extended['ambiguity_detection_rate'] == 1

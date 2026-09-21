from datetime import date

from evaluation.analyze_execution_coverage import analyze
from evaluation.run_execution_coverage_eval import _hydrate_temporal_filters
from scripts.seed_execution_eval import _required_columns
from src.query.ir import QueryIR
from src.schema.registry import SchemaRegistry


def test_coverage_analysis_collects_query_columns_tenant_column_and_join_endpoints():
    report = {
        'summary': {'total_cases': 1},
        'cases': [{
            'id': 'case-1', 'actual': {'query_ir': {
                'intent': 'SUM', 'tables': ['sales.orders'],
                'select': [{'column': 'sales.orders.total_amount', 'aggregation': 'SUM'}],
                'filters': [{'column': 'sales.orders.order_date', 'operator': 'gte', 'value': '2026-08-01'}],
            }},
        }],
    }

    coverage = analyze(report)

    assert coverage['execution_eligible_cases'] == 1
    assert coverage['table_count'] == 1
    assert {item['name'] for item in coverage['columns']} >= {
        'sales.orders.total_amount', 'sales.orders.order_date', 'sales.orders.tenant_id',
    }
    assert coverage['execution_eligible_case_ids'] == ['case-1']


def test_coverage_runner_restores_date_value_after_json_round_trip():
    registry = SchemaRegistry()
    query = QueryIR.model_validate({
        'intent': 'SUM', 'tables': ['sales.orders'], 'select': ['sales.orders.total_amount'],
        'filters': [{'column': 'sales.orders.order_date', 'operator': 'gte', 'value': '2026-08-01'}],
    })

    hydrated = _hydrate_temporal_filters(query, registry)

    assert hydrated.filters[0].value == date(2026, 8, 1)


def test_fixture_requirements_include_coverage_and_fixed_baseline_columns():
    registry = SchemaRegistry()
    coverage = {
        'tables': [{'name': 'sales.orders'}],
        'columns': [{'name': 'sales.orders.total_amount'}],
    }

    required = _required_columns(coverage, registry)

    assert {'id', 'tenant_id', 'total_amount', 'order_date', 'customer_id', 'branch_id'} <= required['sales.orders']
    assert 'base_salary' in required['hr.employees']

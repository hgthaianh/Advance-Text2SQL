"""Run the external ERP QA set against the current pipeline and seeded DB."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import json
from pathlib import Path
import re
from time import perf_counter

from sqlalchemy import text

from src.auth.provider import _role_from_code
from src.config import get_settings
from src.database.session import get_engine
from src.nlp.entity_extractor import DeterministicEntityExtractor
from src.nlp.robustness import NLPRobustnessProcessor
from src.nlp.scope_router import ScopeRouter
from src.pipeline.observability import PipelineEvent, PipelineObserver
from src.pipeline.orchestrator import Text2SQLPipeline
from src.schema.linker import SchemaLinker
from src.schema.registry import SchemaRegistry
from src.shared.models import UserContext


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "evaluation/QA/erp_text2sql_qa_regression.json"
DEFAULT_OUTPUT = ROOT / "evaluation/results/erp_qa_regression_current.json"


class EventCollector(PipelineObserver):
    def __init__(self) -> None:
        self.events: dict[str, PipelineEvent] = {}

    def record(self, event: PipelineEvent) -> None:
        self.events[event.query_id] = event


def load_users(engine, usernames: set[str]) -> dict[str, UserContext]:
    statement = text("""
        SELECT u.username, u.id, u.tenant_id, u.branch_id, u.department_id,
               u.team_id, u.employee_id, r.code AS role_code
        FROM iam.users u
        JOIN iam.user_roles ur ON ur.user_id = u.id
        JOIN iam.roles r ON r.id = ur.role_id
        WHERE u.username = ANY(:usernames)
    """)
    with engine.connect() as connection:
        rows = connection.execute(statement, {"usernames": sorted(usernames)}).mappings()
        return {
            row["username"]: UserContext(
                user_id=str(row["id"]),
                role=_role_from_code(row["role_code"]),
                tenant_id=str(row["tenant_id"]),
                branch_id=str(row["branch_id"]) if row["branch_id"] is not None else None,
                department_id=str(row["department_id"]) if row["department_id"] is not None else None,
                team_id=str(row["team_id"]) if row["team_id"] is not None else None,
                employee_id=str(row["employee_id"]) if row["employee_id"] is not None else None,
            )
            for row in rows
        }


def behavior_matches(expected: str, result) -> bool:
    if expected == "SUCCESS":
        return result.success
    if expected == "AMBIGUOUS":
        return result.error_code == "AMBIGUOUS_QUERY"
    if expected == "UNSUPPORTED":
        return result.error_code == "UNSUPPORTED_QUERY"
    if expected == "DENY":
        return result.error_code == "PERMISSION_DENIED"
    if expected == "DENY_OR_EMPTY":
        return result.error_code == "PERMISSION_DENIED" or (
            result.success and (result.row_count == 0 or result.scalar == 0)
        )
    if expected.startswith("DENY_OR_"):
        return result.success or result.error_code == "PERMISSION_DENIED"
    return False


_ENTITY_BY_TABLE = {
    'hr.employees': {'employee'}, 'org.departments': {'department'},
    'org.branches': {'branch'}, 'org.teams': {'team'},
    'hr.leave_requests': {'leave'}, 'hr.employment_contracts': {'contract'},
    'hr.payroll_details': {'payroll'}, 'sales.orders': {'sales_order', 'order'},
    'sales.invoices': {'invoice'}, 'sales.payments': {'payment'},
    'sales.order_items': {'order_item'}, 'inventory.inventory_balances': {'inventory'},
    'inventory.warehouses': {'warehouse'}, 'inventory.stock_lots': {'inventory_lot'},
    'inventory.stock_serials': {'serial'}, 'inventory.stock_transfers': {'inventory_transfer'},
    'procurement.purchase_orders': {'purchase_order'}, 'procurement.suppliers': {'supplier'},
    'procurement.goods_receipts': {'goods_receipt'}, 'finance.vendor_bills': {'vendor_bill'},
    'finance.journal_entries': {'journal_entry'},
    'finance.journal_entry_lines': {'journal_entry_line'},
    'finance.chart_of_accounts': {'account'}, 'customer.customers': {'customer'},
    'customer.customer_segments': {'customer_segment'}, 'customer.addresses': {'address'},
    'product.products': {'product'}, 'product.categories': {'category'},
    'product.brands': {'brand'}, 'product.product_variants': {'variant'},
    'product.price_list_items': {'price'}, 'support.support_tickets': {'ticket'},
    'support.ticket_comments': {'ticket_comment'},
    'support.ticket_status_history': {'ticket_status_history'},
    'support.sla_policies': {'sla'}, 'support.ticket_sla_events': {'sla'},
}


def _ratio(matches: list[bool]) -> dict:
    return {
        'matched': sum(matches), 'total': len(matches),
        'accuracy': round(sum(matches) / len(matches), 4) if matches else None,
    }


def _filter_evidence(expected_filters: list[str], sql: str | None, params: dict) -> tuple[bool, bool]:
    if not expected_filters:
        return True, True
    sql_text = (sql or '').lower()
    parameter_values = [str(value).lower() for value in params.values()]
    column_aliases = {
        'employee_status': 'employment_status', 'department': 'departments',
        'date': '_date', 'tenant_id': 'tenant_id', 'customer_tier': 'customer_tier',
        'ticket_code': 'ticket_number', 'employee_code': 'employee_code',
        'product_code': '.code', 'customer_code': '.code', 'status': 'status',
    }
    canonical_values = {'hr': ('nhân sự', 'nhan su'), 'sales': ('kinh doanh',)}
    columns_ok = True
    values_ok = True
    for expected in expected_filters:
        key = re.split(r'\s*(?:=|\bbetween\b|\bin\b)', expected, maxsplit=1, flags=re.I)[0].strip().lower()
        column_token = column_aliases.get(key, key)
        columns_ok &= column_token in sql_text
        literals = re.findall(r'\b[A-Z][A-Z0-9_]*\b|\d{4}-\d{2}-\d{2}|(?<==)\d+', expected)
        for literal in literals:
            alternatives = canonical_values.get(literal.lower(), (literal.lower(),))
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', literal):
                # QA ranges describe an inclusive calendar end while QueryIR
                # deliberately compiles half-open intervals.
                alternatives = (*alternatives, str(date.fromisoformat(literal) + timedelta(days=1)))
            values_ok &= any(any(option in value for option in alternatives) for value in parameter_values)
    return bool(columns_ok), bool(values_ok)


def _ir_shape_matches(expected_shape: str | None, sql: str | None) -> bool:
    if expected_shape is None:
        return True
    text = (sql or '').upper()
    checks = {
        'SELECT': 'SELECT', 'COUNT': 'COUNT(', 'SUM': 'SUM(', 'AVG': 'AVG(',
        'GROUP BY': 'GROUP BY', 'ORDER BY': 'ORDER BY', 'LIMIT': 'LIMIT',
    }
    return all(token not in expected_shape.upper() or fragment in text for token, fragment in checks.items())


def _result_shape_matches(expected_type: str, result) -> bool:
    if not result.success:
        return False
    if expected_type == 'scalar':
        return result.scalar is not None
    if expected_type == 'row':
        return result.row_count <= 1
    if expected_type == 'none':
        return result.row_count == 0 and result.scalar in (None, 0)
    return bool(result.columns)


def expected_scope_decision(expected_behavior: str) -> str:
    if expected_behavior == "AMBIGUOUS":
        return "AMBIGUOUS"
    if expected_behavior == "UNSUPPORTED":
        return "OUT_OF_SCOPE"
    return "IN_SCOPE"


def run(suite_path: Path, output_path: Path) -> dict:
    payload = json.loads(suite_path.read_text(encoding="utf-8"))
    cases = payload["cases"]
    registry = SchemaRegistry()
    invalid_table_labels = sorted({
        table for case in cases for table in case.get("expected_table_concepts", [])
        if table not in registry.tables
    })
    engine = get_engine()
    users = load_users(engine, {case["username"] for case in cases})
    missing_users = sorted({case["username"] for case in cases} - users.keys())
    if missing_users:
        raise RuntimeError(f"Missing seeded users: {', '.join(missing_users)}")

    collector = EventCollector()
    pipeline = Text2SQLPipeline(registry=registry, engine=engine, observer=collector)
    nlp = NLPRobustnessProcessor()
    router = ScopeRouter()
    linker = SchemaLinker(registry)
    extractor = DeterministicEntityExtractor()
    records = []
    started = perf_counter()

    for case in cases:
        question = case["question"]
        preprocessed = nlp.process_query(question, date.today())
        scope = router.route(preprocessed)
        links = linker.link(preprocessed)
        entities = extractor.extract(preprocessed.normalized_text)
        result = pipeline.execute(question, users[case["username"]])
        event = collector.events.get(result.query_id or "")
        expected_tables = case.get("expected_table_concepts", [])
        valid_expected_tables = [table for table in expected_tables if table in registry.tables]
        linked_tables = [candidate.name for candidate in links.tables]
        entity_evidence = {entity.entity_type for entity in entities}
        for table in linked_tables:
            entity_evidence.update(_ENTITY_BY_TABLE.get(table, set()))
        expected_entities = set(case.get('expected_entities', []))
        entity_recall = len(expected_entities & entity_evidence) / len(expected_entities) if expected_entities else 1.0
        column_match, value_match = _filter_evidence(case.get('expected_filters', []), result.sql, result.params)
        expected_hops = sum(path.count('->') for path in case.get('expected_join_path', []))
        join_match = not expected_hops or bool(result.sql and result.sql.upper().count(' JOIN ') >= expected_hops)
        intent_match = preprocessed.intent_result.intent.value.upper() == case['expected_intent']
        ir_match = _ir_shape_matches(case.get('expected_sql_shape'), result.sql)
        behavior_match = behavior_matches(case["expected_behavior"], result)
        permission_reached = result.success or result.error_code == 'PERMISSION_DENIED'
        result_shape_match = (
            _result_shape_matches(case.get('expected_result_type', 'rows'), result)
            if case['expected_behavior'] == 'SUCCESS' else behavior_match
        )
        records.append({
            "id": case["id"],
            "category": case["category"],
            "username": case["username"],
            "question": question,
            "expected_behavior": case["expected_behavior"],
            "behavior_match": behavior_match,
            "expected_intent": case["expected_intent"],
            "actual_intent": preprocessed.intent_result.intent.value.upper(),
            "intent_match": intent_match,
            "actual_scope_decision": scope.decision.value,
            "scope_match": scope.decision.value == expected_scope_decision(case["expected_behavior"]),
            "scope_signals": scope.matched_signals,
            "linked_tables": linked_tables,
            "valid_expected_tables": valid_expected_tables,
            "invalid_expected_tables": [table for table in expected_tables if table not in registry.tables],
            "valid_table_recall": (
                len(set(valid_expected_tables) & set(linked_tables)) / len(valid_expected_tables)
                if valid_expected_tables else None
            ),
            "entity_recall": round(entity_recall, 4),
            "column_recall_match": column_match,
            "value_grounding_match": value_match,
            "filter_preservation_match": column_match and value_match,
            "ir_shape_match": ir_match,
            "permission_match": (
                (behavior_match if case['expected_permission'] != 'ALLOW' else result.success)
                if permission_reached else None
            ),
            "join_planning_match": join_match,
            "sql_validity_match": bool(result.sql) if result.success else case['expected_behavior'] != 'SUCCESS',
            "result_shape_match": result_shape_match,
            "extracted_entities": [
                {"type": entity.entity_type, "value": entity.normalized_value}
                for entity in entities
            ],
            "success": result.success,
            "error_code": result.error_code,
            "message": result.message,
            "failure_stage": event.stage if event and event.outcome == "failure" else None,
            "row_count": result.row_count,
            "scalar": result.scalar,
            "columns": result.columns,
            "sql": result.sql,
            "params": result.params,
        })

    by_category: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "matched": 0})
    for record in records:
        item = by_category[record["category"]]
        item["total"] += 1
        item["matched"] += int(record["behavior_match"])
    for item in by_category.values():
        item["rate"] = round(item["matched"] / item["total"], 4)

    failures = [record for record in records if not record["behavior_match"]]
    expected_success_records = [r for r in records if r['expected_behavior'] == 'SUCCESS']
    table_recall_values = [r['valid_table_recall'] for r in records if r['valid_table_recall'] is not None]
    paraphrase_groups: dict[str, list[dict]] = defaultdict(list)
    for case, record in zip(cases, records):
        if case.get('paraphrase_group'):
            paraphrase_groups[case['paraphrase_group']].append(record)
    paraphrase_group_scores = {
        group: round(sum(item['behavior_match'] and item['intent_match'] for item in items) / len(items), 4)
        for group, items in paraphrase_groups.items()
    }
    cross_tenant = [
        record for case, record in zip(cases, records)
        if case.get('expected_permission') == 'BLOCK_OTHER_TENANT'
    ]
    semantic_final = [
        r['behavior_match'] and (
            r['expected_behavior'] != 'SUCCESS' or all((
                r['intent_match'], r['filter_preservation_match'], r['ir_shape_match'],
                r['join_planning_match'], r['result_shape_match'],
            ))
        ) for r in records
    ]
    metrics = {
        'scope_accuracy': _ratio([r['scope_match'] for r in records]),
        'intent_accuracy': _ratio([r['intent_match'] for r in records]),
        'entity_accuracy': {
            'average_recall': round(sum(r['entity_recall'] for r in records) / len(records), 4),
            'fully_matched': sum(r['entity_recall'] == 1 for r in records), 'total': len(records),
        },
        'table_recall': round(sum(table_recall_values) / len(table_recall_values), 4),
        'column_recall': _ratio([r['column_recall_match'] for r in records]),
        'value_grounding_accuracy': _ratio([r['value_grounding_match'] for r in records]),
        'filter_preservation_accuracy': _ratio([r['filter_preservation_match'] for r in records]),
        'ir_accuracy': _ratio([r['ir_shape_match'] for r in records]),
        'permission_accuracy': {
            **_ratio([r['permission_match'] for r in records if r['permission_match'] is not None]),
            'not_reached': sum(r['permission_match'] is None for r in records),
        },
        'join_planning_accuracy': _ratio([r['join_planning_match'] for r in records]),
        'sql_validity': _ratio([r['sql_validity_match'] for r in records]),
        'execution_accuracy': _ratio([r['success'] for r in expected_success_records]),
        'final_result_accuracy': {
            **_ratio(semantic_final),
            'note': 'Structural/semantic accuracy; the QA suite has no exact expected row values.',
        },
        'paraphrase_consistency': {
            'groups': paraphrase_group_scores,
            'average': round(sum(paraphrase_group_scores.values()) / len(paraphrase_group_scores), 4)
            if paraphrase_group_scores else None,
        },
        'ambiguity_rejection_accuracy': _ratio([
            r['error_code'] == 'AMBIGUOUS_QUERY' for r in records if r['expected_behavior'] == 'AMBIGUOUS'
        ]),
        'cross_tenant_leakage': {
            'violations': sum(not r['behavior_match'] for r in cross_tenant),
            'tested': len(cross_tenant),
        },
    }
    report = {
        "suite": payload.get("metadata", {}).get("suite_name"),
        "run_date": date.today().isoformat(),
        "duration_seconds": round(perf_counter() - started, 3),
        "summary": {
            "total": len(records),
            "behavior_matched": len(records) - len(failures),
            "behavior_failed": len(failures),
            "behavior_match_rate": round((len(records) - len(failures)) / len(records), 4),
            "scope_matched": sum(record["scope_match"] for record in records),
            "pipeline_success": sum(record["success"] for record in records),
            "expected_success": sum(record["expected_behavior"] == "SUCCESS" for record in records),
            "invalid_expected_table_labels": len(invalid_table_labels),
            "cases_with_invalid_table_labels": sum(bool(record["invalid_expected_tables"]) for record in records),
        },
        "by_category": dict(sorted(by_category.items())),
        "actual_error_codes": dict(sorted(Counter(record["error_code"] or "SUCCESS" for record in records).items())),
        "failure_stages": dict(sorted(Counter(record["failure_stage"] for record in records if record["failure_stage"]).items())),
        "metrics": metrics,
        "invalid_expected_table_labels": invalid_table_labels,
        "uncovered_cases": failures,
        "cases": records,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run(args.suite, args.output)
    print(json.dumps({
        "summary": report["summary"],
        "by_category": report["by_category"],
        "actual_error_codes": report["actual_error_codes"],
        "failure_stages": report["failure_stages"],
        "metrics": report["metrics"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

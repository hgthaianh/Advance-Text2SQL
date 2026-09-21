"""Measure available Text2SQL stages without adding missing production features."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

from src.nlp.robustness import NLPRobustnessProcessor
from src.nlp.entity_extractor import DeterministicEntityExtractor
from src.permission.column_policy import DefaultColumnPolicy
from src.permission.resolver import DefaultPermissionResolver
from src.permission.row_policy import ScopedRowPolicy
from src.query.handlers.basic import BasicQueryHandler
from src.query.ir import QueryIR
from src.query.ir_builder import QueryIRBuilder
from src.schema.linker import SchemaLinker
from src.schema.registry import ROOT
from src.shared.exceptions import (
    AmbiguousJoinPathError, AmbiguousQueryError, JoinPathNotFoundError,
    MissingEntityError, PermissionDeniedError, SQLValidationError, UnsupportedQueryError,
)
from src.shared.models import UserContext
from src.validation.sql_validator import ReadOnlySQLValidator

DEFAULT_CASES = ROOT / 'evaluation/full_pipeline_cases.json'
DEFAULT_RESULTS = ROOT / 'evaluation/results/full_eval.json'
DEFAULT_SUMMARY = ROOT / 'reports/generated/full_eval_summary.md'
DEFAULT_FAILURES = ROOT / 'reports/generated/full_eval_failures.csv'


def load_cases(path: Path = DEFAULT_CASES) -> tuple[list[dict[str, Any]], date]:
    """Expand frozen schema-linking records into one unified evaluation shape."""
    config = json.loads(path.read_text(encoding='utf-8'))
    cases: list[dict[str, Any]] = []
    for source in config['sources']:
        records = json.loads((path.parent / source['file']).read_text(encoding='utf-8'))
        selected = [r for r in records if r.get('kind') == source['kind']][:source['limit']]
        for record in selected:
            cases.append({
                'id': f"SCHEMA-{record['id']}", 'category': ['schema_linking', source['kind']],
                'question': record['question'], 'user_context': {'user_id': 'eval', 'role': 'admin'},
                'expected': {'tables': record['expected_tables'], 'columns': record['expected_columns'], 'should_execute': True},
                'source_case_id': record['id'],
            })
    cases.extend(config['cases'])
    ids = [case['id'] for case in cases]
    if not 150 <= len(cases) <= 300 or len(ids) != len(set(ids)):
        raise ValueError('Full evaluation requires 150–300 uniquely identified cases')
    return cases, date.fromisoformat(config['reference_date'])


def _rate(values: list[bool | float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _recall(actual: list[str], expected: list[str], k: int) -> float | None:
    """Macro Recall@K; cases with more than K gold items remain measurable."""
    return len(set(actual[:k]) & set(expected)) / len(expected) if expected else None


def _date_matches(actual: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    """Compare only date fields supplied by a case, not parser debug text."""
    return actual is not None and all(actual.get(key) == value for key, value in expected.items())


def _first_observable_failure(checks: dict[str, bool | float | None]) -> str | None:
    """Return the earliest failed stage for fields the case actually asserts."""
    for check, failure in (
        ('intent', 'INTENT_WRONG'),
        ('date', 'DATE_PARSE_WRONG'),
        ('number', 'NUMBER_PARSE_WRONG'),
        # A case may legitimately have more than five required schema items.
        # Use Recall@10 for pass/fail; Recall@1/3/5 remain diagnostic metrics.
        ('table_recall_at_10', 'TABLE_LINK_WRONG'),
        ('column_recall_at_10', 'COLUMN_LINK_WRONG'),
    ):
        value = checks.get(check)
        if value is False or isinstance(value, float) and value < 1:
            return failure
    return None


def _failure_for_exception(exc: Exception) -> str:
    if isinstance(exc, PermissionDeniedError): return 'PERMISSION_ALLOW_WRONG'
    if isinstance(exc, AmbiguousJoinPathError): return 'JOIN_AMBIGUOUS'
    if isinstance(exc, JoinPathNotFoundError): return 'JOIN_NOT_FOUND'
    if isinstance(exc, AmbiguousQueryError): return 'SCHEMA_AMBIGUOUS'
    if isinstance(exc, MissingEntityError): return 'IR_INCOMPLETE'
    if isinstance(exc, UnsupportedQueryError): return 'SCHEMA_UNSUPPORTED'
    if isinstance(exc, SQLValidationError): return 'SQL_COMPILE_ERROR'
    return 'QUERY_PLAN_ERROR'


def evaluate_case(case: dict[str, Any], reference_date: date, nlp: NLPRobustnessProcessor,
                  linker: SchemaLinker, handler: BasicQueryHandler, extractor: DeterministicEntityExtractor,
                  ir_builder: QueryIRBuilder, validator: ReadOnlySQLValidator) -> dict[str, Any]:
    """Run observable stages and attribute the first product-path divergence."""
    start = perf_counter()
    expected, question = case['expected'], case['question']
    preprocessed = nlp.process_query(question, reference_date)
    linked = linker.link(preprocessed)
    tables = [c.name for c in linked.tables]
    columns = [c.name for c in linked.columns]
    actual: dict[str, Any] = {
        'normalized_text': preprocessed.normalized_text,
        'intent': preprocessed.intent_result.intent.value,
        'date_range': preprocessed.date_range.model_dump(mode='json') if preprocessed.date_range else None,
        'numbers': [str(n.value) for n in preprocessed.numbers],
        'tables': tables, 'columns': columns, 'ambiguous_terms': linked.ambiguous_terms,
        'entity_extractor': 'available', 'entities': [], 'query_ir': None, 'permission_scope': None,
        'permission_outcome': None, 'sql': None, 'execution': 'unavailable', 'result': None,
    }
    checks: dict[str, bool | float | None] = {
        'intent': actual['intent'] == expected['intent'] if 'intent' in expected else None,
        'date': _date_matches(actual['date_range'], expected['date_range']) if 'date_range' in expected else None,
        'number': actual['numbers'] == expected['numbers'] if 'numbers' in expected else None,
        **{f'table_recall_at_{k}': _recall(tables, expected.get('tables', []), k) for k in (1, 3, 5, 10)},
        **{f'column_recall_at_{k}': _recall(columns, expected.get('columns', []), k) for k in (1, 3, 5, 10)},
    }
    failure = _first_observable_failure(checks)
    unsupported = preprocessed.intent_result.intent.value in {'unknown', 'trend', 'comparison'} or not tables and not columns
    if expected.get('unsupported'):
        checks['unsupported'] = unsupported
        passed = unsupported
        failure = None if passed else 'UNSUPPORTED_NOT_DETECTED'
    elif failure:
        passed = False
    elif 'query_ir' not in case:
        try:
            entities = extractor.extract(question)
            actual['entities'] = [entity.model_dump(mode='json') for entity in entities]
            query = ir_builder.build(preprocessed, linked, entities)
            actual['query_ir'] = query.model_dump(mode='json')
            permissions = DefaultPermissionResolver(linker.registry).resolve(UserContext.model_validate(case['user_context']))
            actual['permission_scope'] = permissions.scope.value
            query = ScopedRowPolicy().apply(query, permissions)
            DefaultColumnPolicy().validate(query, permissions)
            compiled = handler.handle(query)
            validator.validate(compiled.statement, permissions)
            actual['sql'], actual['params'] = compiled.sql, compiled.params
            checks['planning_success'] = True
            checks['sql_compile_success'] = True
            # The full corpus has no independent expected database result.
            # Compilation is measured above; result correctness is not.
            actual['execution'] = 'not_measured'
            passed, failure = None, None
        except Exception as exc:
            actual['error'] = f'{type(exc).__name__}: {exc}'
            checks['planning_success'] = False
            checks['sql_compile_success'] = False
            passed, failure = False, _failure_for_exception(exc)
    else:
        user = UserContext.model_validate(case['user_context'])
        permissions = DefaultPermissionResolver(linker.registry).resolve(user)
        actual['permission_scope'] = permissions.scope.value
        checks['permission_scope'] = actual['permission_scope'] == expected.get('permission_scope', actual['permission_scope'])
        if checks['permission_scope'] is False:
            passed, failure = False, 'PERMISSION_SCOPE_WRONG'
        else:
            query = ScopedRowPolicy().apply(QueryIR.model_validate(case['query_ir']), permissions)
            actual['query_ir'] = query.model_dump(mode='json')
            try:
                DefaultColumnPolicy().validate(query, permissions)
                actual['permission_outcome'] = 'allowed'
                checks['permission_denied'] = not expected.get('permission_denied', False)
                checks['permission_allow'] = not expected.get('permission_denied', False)
            except PermissionDeniedError:
                actual['permission_outcome'] = 'denied'
                checks['permission_denied'] = expected.get('permission_denied', False)
                checks['permission_allow'] = False if not expected.get('permission_denied', False) else None
                passed = bool(checks['permission_denied'])
                failure = None if passed else 'PERMISSION_DENY_WRONG'
            else:
                if expected.get('permission_denied', False):
                    passed, failure = False, 'PERMISSION_ALLOW_WRONG'
                else:
                    try:
                        compiled = handler.handle(query)
                        actual['sql'], actual['params'] = compiled.sql, compiled.params
                        validator.validate(compiled.statement, permissions)
                        checks['planning_success'] = True
                        checks['sql_compile_success'] = True
                        actual['execution'] = 'not_measured'
                        passed, failure = None, None
                    except Exception as exc:
                        actual['error'] = f'{type(exc).__name__}: {exc}'
                        checks['planning_success'] = False
                        checks['sql_compile_success'] = False
                        passed, failure = False, _failure_for_exception(exc)
    return {
        'id': case['id'], 'question': question, 'category': case['category'], 'passed': passed,
        'primary_failure': failure, 'checks': checks, 'actual': actual,
        'latency_ms': round((perf_counter() - start) * 1000, 3),
    }


def evaluate(cases: list[dict[str, Any]], reference_date: date) -> dict[str, Any]:
    nlp, linker = NLPRobustnessProcessor(), SchemaLinker()
    handler = BasicQueryHandler(linker.registry)
    extractor, ir_builder, validator = DeterministicEntityExtractor(), QueryIRBuilder(linker.registry), ReadOnlySQLValidator()
    results = [evaluate_case(case, reference_date, nlp, linker, handler, extractor, ir_builder, validator) for case in cases]
    failures = Counter(r['primary_failure'] for r in results if r['primary_failure'])
    def metric(name: str) -> float | None: return _rate([r['checks'][name] for r in results if r['checks'].get(name) is not None])
    measured_names = (
        'intent', 'date', 'number',
        'table_recall_at_1', 'table_recall_at_3', 'table_recall_at_5', 'table_recall_at_10',
        'column_recall_at_1', 'column_recall_at_3', 'column_recall_at_5', 'column_recall_at_10',
        'unsupported', 'permission_scope', 'planning_success', 'sql_compile_success',
    )
    metrics = {name: metric(name) for name in measured_names}
    metric_samples = {
        name: sum(r['checks'].get(name) is not None for r in results) for name in measured_names
    }
    permission_rows = [r for r in results if r['checks'].get('permission_denied') is not None]
    metrics['permission_allow_accuracy'] = _rate([
        r['checks']['permission_allow'] for r in permission_rows if r['checks'].get('permission_allow') is not None
    ])
    metric_samples['permission_allow_accuracy'] = sum(
        r['checks'].get('permission_allow') is not None for r in permission_rows
    )
    metrics['permission_deny_accuracy'] = _rate([
        r['checks']['permission_denied'] for r in permission_rows if r['checks'].get('permission_allow') is None
    ])
    metric_samples['permission_deny_accuracy'] = sum(
        r['checks'].get('permission_allow') is None for r in permission_rows
    )
    expected_denials = [r for r in permission_rows if r['checks'].get('permission_allow') is None]
    metrics['permission_violation_rate'] = _rate([
        r['actual']['permission_outcome'] == 'allowed' for r in expected_denials
    ]) or 0.0
    metric_samples['permission_violation_rate'] = len(expected_denials)
    metrics['entity_extraction_accuracy'] = None
    metrics['query_ir_accuracy'] = None
    metrics['join_path_accuracy'] = None
    metrics['sql_execution_accuracy'] = None
    metrics['final_result_accuracy'] = None
    metrics['end_to_end_success_rate'] = None
    metrics['ambiguity_rate'] = _rate([bool(r['actual']['ambiguous_terms']) for r in results])
    metric_samples.update({
        'entity_extraction_accuracy': 0,
        'query_ir_accuracy': 0,
        'join_path_accuracy': 0,
        'sql_execution_accuracy': 0,
        'final_result_accuracy': 0,
        'end_to_end_success_rate': 0,
        'ambiguity_rate': len(results),
    })
    categories: dict[str, list[dict]] = defaultdict(list)
    for result in results:
        for category in result['category']: categories[category].append(result)
    category_metrics = {
        name: {
            'cases': len(rows),
            'success_rate': _rate([r['passed'] for r in rows if r['passed'] is not None]),
        }
        for name, rows in sorted(categories.items())
    }
    latency = sorted(r['latency_ms'] for r in results)
    return {
        'summary': {
            'total_cases': len(results),
            'passed': sum(r['passed'] is True for r in results),
            'failed': sum(r['passed'] is False for r in results),
            'unmeasured': sum(r['passed'] is None for r in results),
        },
        'metrics': metrics, 'metric_samples': metric_samples,
        'failure_counts': dict(failures), 'category_metrics': category_metrics,
        'latency_ms': {'p50': median(latency), 'p95': latency[int(.95 * (len(latency) - 1))]},
        'stage_availability': {
            'entity_extraction': True, 'query_ir_construction': True,
            'sql_validation': True, 'database_execution': False,
        },
        'cases': results,
    }


def write_reports(report: dict[str, Any], output: Path, summary_path: Path, failures_path: Path) -> None:
    for path in (output, summary_path, failures_path): path.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    metrics, samples, summary = report['metrics'], report['metric_samples'], report['summary']
    def display(value: float | None) -> str:
        return 'not available' if value is None else f'{value:.1%}'

    lines = ['# Full pipeline evaluation', '', f"Total cases: {summary['total_cases']}", f"Passed: {summary['passed']}", f"Failed: {summary['failed']}", f"Unmeasured result cases: {summary['unmeasured']}", f"Final-result accuracy: {display(metrics['final_result_accuracy'])}", f"End-to-end success: {display(metrics['end_to_end_success_rate'])}", '', '## Stage metrics', '']
    lines += [f"- {name}: {display(value)} (n={samples[name]})" for name, value in metrics.items()]
    lines += ['', '## First-failure breakdown', '']
    lines += [f'- {name}: {count}' for name, count in Counter(report['failure_counts']).most_common()]
    recommendations = {
        'ENTITY_MISSING': 'Implement the entity-extraction and natural-language-to-QueryIR boundary.',
        'TABLE_LINK_WRONG': 'Review table-linking aliases and ranking on the affected case.',
        'COLUMN_LINK_WRONG': 'Review column-linking aliases and context ranking on the affected case.',
        'PERMISSION_DENY_WRONG': 'Review permission allowlists for injected row-scope filters.',
        'SQL_EXECUTION_ERROR': 'Add concrete SQL validation and database execution before interpreting result accuracy.',
    }
    top_failure = next(iter(Counter(report['failure_counts']).most_common()), None)
    if top_failure:
        lines += ['', '## Recommended next focus', '', f"- {recommendations.get(top_failure[0], 'Inspect the highest-volume first-failure category.')} ({top_failure[0]}, {top_failure[1]} cases)"]
    worst = sorted(
        report['category_metrics'].items(),
        key=lambda item: item[1]['success_rate'] if item[1]['success_rate'] is not None else 1,
    )[:5]
    lines += ['', '## Worst categories', '']
    lines += [f"- {name}: {display(values['success_rate'])} ({values['cases']} cases)" for name, values in worst]
    lines += ['', '## Measured limitations', '', '- The runner now exercises entity extraction, QueryIR construction, planning, and SQL validation. Its fixed 233-case corpus has no deterministic database fixture for all 100 schema tables, so compilation is reported separately from execution.', '- QueryIR cases are downstream contract probes. They measure permission, planning and compilation only; they do not claim natural-language-to-IR accuracy.']
    summary_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    with failures_path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['id', 'primary_failure', 'category', 'question'])
        writer.writeheader()
        writer.writerows({'id': r['id'], 'primary_failure': r['primary_failure'], 'category': '|'.join(r['category']), 'question': r['question']} for r in report['cases'] if not r['passed'])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=DEFAULT_CASES)
    parser.add_argument('--output', type=Path, default=DEFAULT_RESULTS)
    parser.add_argument('--summary', type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument('--failures', type=Path, default=DEFAULT_FAILURES)
    args = parser.parse_args()
    cases, reference = load_cases(args.cases)
    report = evaluate(cases, reference)
    write_reports(report, args.output, args.summary, args.failures)
    print(json.dumps({k: report[k] for k in ('summary', 'metrics', 'failure_counts', 'latency_ms')}, indent=2))


if __name__ == '__main__': main()

"""Schema-linking regression and frozen synthetic benchmark evaluation.

Run with --split dev or --split heldout for the group-disjoint business cases.
The default remains the 272-case internal regression dataset. Precision@K uses
actual returned candidates, not K empty slots; empty predictions score zero on
positive gold. Ambiguous and no-match cases are assessed separately.
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from src.nlp.normalizer import Normalizer
from src.schema.linker import SchemaLinker
from src.schema.registry import ROOT, SchemaRegistry

MANIFEST = ROOT / 'evaluation/schema_linking_manifest.json'


def content_hash(path: Path) -> str:
    """Hash canonical UTF-8 text, independent of checkout line endings and BOM."""
    return hashlib.sha256(path.read_text(encoding='utf-8-sig').encode('utf-8')).hexdigest()


def validate_cases(cases: list[dict], registry: SchemaRegistry) -> None:
    """Reject unknown gold, malformed alternatives and inconsistent behaviors."""
    if not isinstance(cases, list) or not cases:
        raise ValueError('Nonempty cases required')
    seen = set()
    for case in cases:
        if not isinstance(case.get('id'), str) or not case['id']:
            raise ValueError('Nonempty case id required')
        if case['id'] in seen:
            raise ValueError(f"Duplicate case id: {case['id']}")
        seen.add(case['id'])
        if not isinstance(case.get('question'), str) or not case['question'].strip():
            raise ValueError(f"Nonempty question required: {case['id']}")
        behavior = case.get('expected_behavior', 'link')
        if behavior not in ('link', 'ambiguous', 'no_match'):
            raise ValueError(f'Unknown expected behavior: {behavior}')
        for label in ('table', 'column'):
            names = case.get(f'expected_{label}s')
            if not isinstance(names, list) or any(not isinstance(n, str) for n in names) or len(names) != len(set(names)):
                raise ValueError(f'Expected unique {label} gold list')
            registry_names = getattr(registry, f'{label}s')
            for name in names:
                if name not in registry_names:
                    raise ValueError(f'Unknown gold {label}: {name}')
        alternatives = case.get('acceptable_interpretations', [])
        if behavior == 'link':
            if not case['expected_tables'] and not case['expected_columns']:
                raise ValueError('Link case needs nonempty gold')
            if alternatives:
                raise ValueError('Alternatives require ambiguous behavior')
        else:
            if case['expected_tables'] or case['expected_columns']:
                raise ValueError('Ambiguous/no_match cases must not assert a single gold set')
            if behavior == 'no_match' and alternatives:
                raise ValueError('No-match case cannot have interpretations')
        if behavior == 'ambiguous':
            if not isinstance(alternatives, list) or len(alternatives) < 2:
                raise ValueError('Ambiguous case needs at least two interpretations')
            unique = set()
            for alternative in alternatives:
                if not isinstance(alternative, dict) or set(alternative) != {'tables', 'columns'}:
                    raise ValueError('Interpretation requires tables and columns')
                for label in ('table', 'column'):
                    names = alternative[f'{label}s']
                    if not isinstance(names, list) or any(not isinstance(n, str) or n not in getattr(registry, f'{label}s') for n in names):
                        raise ValueError(f'Unknown interpretation {label}')
                    if len(names) != len(set(names)):
                        raise ValueError('Duplicate interpretation reference')
                if not alternative['tables'] and not alternative['columns']:
                    raise ValueError('Empty interpretation')
                unique.add((tuple(sorted(alternative['tables'])), tuple(sorted(alternative['columns']))))
            if len(unique) != len(alternatives):
                raise ValueError('Duplicate interpretations')


def load_benchmark(split: str, manifest_path: Path = MANIFEST) -> tuple[list[dict], dict]:
    """Verify frozen datasets and reject cross-split family or question leakage.

    Checks read labels only; they never run linker predictions on held-out cases.
    Dataset edits require a reviewed new manifest/version, not silent regeneration.
    """
    if split not in ('dev', 'heldout'):
        raise ValueError('Split must be dev or heldout')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    partitions = {}
    all_ids, groups, questions = set(), {}, {}
    for name in ('dev', 'heldout'):
        entry = manifest['splits'][name]
        path = manifest_path.parent / entry['file']
        if content_hash(path) != entry['sha256']:
            raise ValueError(f'Frozen dataset checksum mismatch: {name}')
        cases = json.loads(path.read_text(encoding='utf-8'))
        if len(cases) != entry['cases'] or len({c['group_id'] for c in cases}) != entry['groups']:
            raise ValueError(f'Frozen dataset counts mismatch: {name}')
        for case in cases:
            if case['split'] != name or not isinstance(case['group_id'], str) or not case['group_id']:
                raise ValueError('Invalid split/group metadata')
            if case['id'] in all_ids:
                raise ValueError('Duplicate case id across splits')
            all_ids.add(case['id'])
            key = ' '.join(re.findall(r'\w+', Normalizer().normalize(case['question']).accent_stripped_text))
            for seen, value in ((groups, case['group_id']), (questions, key)):
                if value in seen and seen[value] != name:
                    raise ValueError(f'Cross-split leakage: {value}')
                seen[value] = name
        partitions[name] = cases
    return partitions[split], manifest


def evaluate(linker: SchemaLinker, cases: list[dict], repeats: int = 1) -> dict:
    """Compute retrieval, extra-candidate, ambiguity and rejection diagnostics."""
    if repeats < 1:
        raise ValueError('Positive repeats required')
    validate_cases(cases, linker.registry)
    samples, failures, errors = [], [], []
    linker.link(cases[0]['question'])
    times = []
    for case in cases:
        for _ in range(repeats):
            start = perf_counter()
            result = linker.link(case['question'], table_top_k=10, column_top_k=10)
            times.append((perf_counter() - start) * 1000)
        predictions = {label: list(dict.fromkeys(c.name for c in getattr(result, f'{label}s'))) for label in ('table', 'column')}
        has_candidates = bool(predictions['table'] or predictions['column'])
        predicted_behavior = 'no_match' if not has_candidates else 'ambiguous' if result.ambiguous_terms else 'link'
        behavior = case.get('expected_behavior', 'link')
        sample = {'kind': case.get('kind', 'challenge'), 'behavior': behavior}
        missing, unexpected = {}, {}
        if behavior == 'link':
            for label in ('table', 'column'):
                expected = set(case[f'expected_{label}s'])
                predicted = predictions[label]
                # Legacy regression cases often omit column gold; absent labels
                # are not interpreted as an assertion that all columns are wrong.
                if not expected:
                    continue
                for k in (5, 10):
                    correct = len(expected & set(predicted[:k]))
                    sample[f'{label}_recall_at_{k}'] = correct / len(expected)
                    sample[f'{label}_precision_at_{k}'] = correct / len(predicted[:k]) if predicted[:k] else 0.0
                sample[f'top_1_{label}_accuracy'] = float(bool(predicted) and predicted[0] in expected)
                if expected - set(predicted):
                    missing[label] = sorted(expected - set(predicted))
                if set(predicted) - expected:
                    unexpected[label] = sorted(set(predicted) - expected)
            sample['false_ambiguity_rate'] = float(predicted_behavior == 'ambiguous')
            sample['false_rejection_rate'] = float(not has_candidates)
        elif behavior == 'no_match':
            sample['false_link_rate'] = float(has_candidates)
            sample['no_match_accuracy'] = float(not has_candidates)
            for label in ('table', 'column'):
                sample[f'{label}_false_link_rate'] = float(bool(predictions[label]))
                if predictions[label]:
                    unexpected[label] = predictions[label]
        else:
            sample['ambiguity_detection_rate'] = float(predicted_behavior == 'ambiguous')
            sample['ambiguity_option_recall_at_10'] = mean(
                float(all(set(option[f'{label}s']) <= set(predictions[label]) for label in ('table', 'column')))
                for option in case['acceptable_interpretations']
            )
        samples.append(sample)
        if missing:
            failures.append(dict(id=case['id'], question=case['question'], missing=missing))
        if missing or unexpected or predicted_behavior != behavior or sample.get('ambiguity_option_recall_at_10', 1) < 1:
            errors.append(dict(id=case['id'], question=case['question'], expected_behavior=behavior,
                               predicted_behavior=predicted_behavior, predicted=predictions,
                               missing=missing, unexpected=unexpected, ambiguous_terms=result.ambiguous_terms))

    def aggregate(rows: list[dict]) -> tuple[dict, dict]:
        keys = sorted(set().union(*(row.keys() for row in rows)) - {'kind', 'behavior'})
        return (
            {key: round(mean(row[key] for row in rows if key in row), 6) for key in keys},
            {key: sum(key in row for row in rows) for key in keys},
        )

    metrics, denominators = aggregate(samples)
    return {
        'cases': len(cases), 'coverage': linker.coverage(),
        'tables_in_gold': len({t for c in cases for t in c['expected_tables']}),
        'domains': sorted({c['domain'] for c in cases}),
        'behavior_counts': dict(Counter(s['behavior'] for s in samples)),
        'metrics': metrics, 'metric_case_counts': denominators,
        'metrics_by_kind': {kind: aggregate([s for s in samples if s['kind'] == kind])[0] for kind in sorted({s['kind'] for s in samples})},
        'metrics_by_domain': {domain: aggregate([s for s, c in zip(samples, cases) if c['domain'] == domain])[0] for domain in sorted({c['domain'] for c in cases})},
        'latency_ms': {'median': round(median(times), 3), 'p95': round(sorted(times)[int((len(times)-1)*0.95)], 3), 'max': round(max(times), 3)},
        'failures_at_10': failures, 'errors': errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    dataset = parser.add_mutually_exclusive_group()
    dataset.add_argument('--cases', type=Path)
    dataset.add_argument('--split', choices=('dev', 'heldout'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    manifest = None
    if args.split:
        cases, manifest = load_benchmark(args.split)
        path = MANIFEST.parent / manifest['splits'][args.split]['file']
    else:
        path = args.cases or ROOT / 'evaluation/schema_linking_cases.json'
        cases = json.loads(path.read_text(encoding='utf-8'))
    start = perf_counter()
    linker = SchemaLinker()
    startup = (perf_counter() - start) * 1000
    report = evaluate(linker, cases, args.repeats)
    report.update(startup_ms=round(startup, 3), split=args.split or 'regression',
                  provenance=manifest['provenance'] if manifest else 'Internal development regression; not an independent benchmark.',
                  gold_status=manifest['gold_status'] if manifest else 'development_regression',
                  dataset_sha256=content_hash(path), ranking_config=asdict(linker.config))
    files = ('src/schema/linker.py', 'src/schema/registry.py', 'src/schema/relationship_graph.py',
             'src/nlp/normalizer.py', 'src/shared/models.py', 'schema/aliases.yaml',
             'schema/schema_spec.yaml', 'evaluation/evaluate_schema_linking.py')
    report['implementation_sha256'] = {file: content_hash(ROOT / file) for file in files}
    if manifest:
        report['baseline_unchanged'] = all(content_hash(ROOT / file) == value for file, value in manifest['baseline_sha256'].items())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = {k: v for k, v in report.items() if k not in ('errors', 'failures_at_10', 'metrics_by_domain', 'implementation_sha256', 'ranking_config')}
    summary['error_cases'] = len(report['errors'])
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

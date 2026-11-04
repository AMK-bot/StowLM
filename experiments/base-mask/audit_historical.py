"""Audit and correct the archived six-instance run without rechecking physics.

Run with ``uv run python experiments/base-mask/audit_historical.py --write``.
No model is loaded, weight file hashed, solver called, or checker executed.
Original config, input and per-instance record bytes are never rewritten.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from eval.harness_metrics import aggregate_results
from eval.splits import DEFAULT_MANIFEST, canonical_spec, load_inventory, spec_from_instance, spec_key

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN = ROOT / 'experiments/base-mask/runs/official-base-bo32-small-c2'
DEFAULT_INPUTS = ROOT / 'experiments/base-mask/instances-v3-val-c2-small'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text())


def _write(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')


def audit_run(run_dir=DEFAULT_RUN, *, inputs_dir=DEFAULT_INPUTS, split_manifest=DEFAULT_MANIFEST):
    """Inspect archived evidence; membership uses the pinned current inventory.

    Missing current report-binding hashes alone establish that a legacy report
    cannot pass the current binding requirement. Hash presence would require
    full current schema validation and report_matches_inputs; this audit never
    upgrades a report or an old shipping flag to a current passing verdict.
    """
    run_dir, inputs_dir = Path(run_dir), Path(inputs_dir)
    config_path = run_dir / 'config.json'
    config = _read(config_path)
    manifest_path = inputs_dir / 'manifest.json'
    manifest = _read(manifest_path)
    inventory = load_inventory(split_manifest)
    sources = defaultdict(list)
    for group_name, group in manifest['groups'].items():
        for source in group['shards']:
            sources[source.get('spec_key')].append((Path(group_name) / source['instance'], source))
    results = []
    for expected in config['inputs']:
        key = expected['spec_key']
        record_path = run_dir / 'instances' / f'{key}.json'
        record = _read(record_path)
        candidates = sources[key]
        if len(candidates) != 1:
            raise ValueError(f'expected one historical source for {key}, found {len(candidates)}')
        relative, source = candidates[0]
        path = (inputs_dir / relative).resolve()
        if not path.is_relative_to(inputs_dir.resolve()):
            raise ValueError('source path escapes historical input directory')
        raw = _read(path)
        actual = _sha(path)
        current_spec, member, membership_error = None, False, None
        try:
            # Only family/provenance generation metadata is adapted. Constructing
            # the current physical schema would require migrating missing voyage.
            identity = SimpleNamespace(family=raw['family'], seed=raw['seed'],
                size_class=raw['size_class'], provenance=SimpleNamespace(**raw['provenance']))
            current_spec = canonical_spec(spec_from_instance(identity))
            inventory.require_membership(current_spec, split=config['split'])
            member = True
        except (ValueError, KeyError, TypeError) as exc:
            membership_error = str(exc)
        report = record.get('report') or {}
        parents = report.get('provenance', {}).get('parents', [])
        has_hashes = all(any(parent.startswith(prefix) for parent in parents)
                         for prefix in ('checker.instance.sha256:', 'checker.plan.sha256:'))
        # Even if hashes were present, this metadata-only audit cannot validate
        # their relation to a current typed/migrated instance and plan.
        binding = 'not_verified' if has_hashes else ('missing_hashes' if report else 'no_report')
        results.append({
            'spec_key': key, 'source_relative_path': relative.as_posix(),
            'expected_input_sha256': expected['input_sha256'], 'actual_input_sha256': actual,
            'input_bytes_match_config': actual == expected['input_sha256'],
            'campaign_sha256_matches_config': source.get('sha256') == expected['input_sha256'],
            'record_sha256_matches_config': record.get('input_sha256') == expected['input_sha256'],
            'record_file_sha256': _sha(record_path),
            'source_spec_matches_config': source.get('split_spec') == expected['spec'],
            'has_declared_voyage': isinstance(raw.get('voyage'), dict),
            'current_v2_spec': current_spec, 'current_v2_member': member,
            'current_membership_error': membership_error,
            'historical_report_passed': report.get('passed'),
            'current_report_binding': binding,
            'current_binding_valid': None if has_hashes else False,
            'historical_component_versions': raw.get('provenance', {}).get('versions', {}),
        })
    return {
        'audit_version': 'historical-basemask@1',
        'scope': 'byte identities and metadata only; no migration or full checker rerun',
        'config_file_sha256': _sha(config_path),
        'campaign_manifest_sha256': _sha(manifest_path),
        'current_split_manifest_sha256': _sha(split_manifest),
        'current_split_version': inventory.version,
        'current_split_entries_sha256': inventory.entries_sha256,
        'historical_split_version': config.get('split_version'),
        'historical_versions': config.get('versions'),
        'n_inputs': len(results),
        'n_inputs_matching_config_bytes': sum(r['input_bytes_match_config'] for r in results),
        'n_inputs_with_voyage': sum(r['has_declared_voyage'] for r in results),
        'n_current_v2_members': sum(r['current_v2_member'] for r in results),
        'n_reports_with_current_binding': sum(r['current_binding_valid'] is True for r in results),
        'full_checker_rerun': False, 'model_weights_hashed': False,
        'current_rules_qualified': False,
        'limitations': [
            'Original v1 inputs have no voyage; D50 departure/arrival cannot be certified from archived reports.',
            'Current v2 membership is checked from generation identity, not inferred from historical val labels.',
            'Archived reports have no current content binding; historical checker passes/shipping flags are not current approvals.',
            'Historical rule_catalogue is unknown; no catalogue SHA-256 or model content digest was recorded. Current files cannot reconstruct the historical content.',
            'Historical component versions differ from the current epoch. No migration, full checker, C2 requalification, or GPU inference was run.',
            'Publish replacement evidence only after regenerating frozen-v2 validation inputs with voyage and rerunning C2-on inference and the full current checker.',
        ],
        'inputs': results,
    }


def correct_run(run_dir=DEFAULT_RUN, *, inputs_dir=DEFAULT_INPUTS, split_manifest=DEFAULT_MANIFEST):
    """Correct dead-sample statistics and annotate remaining legacy evidence."""
    run_dir = Path(run_dir)
    audit = audit_run(run_dir, inputs_dir=inputs_dir, split_manifest=split_manifest)
    summary_path = run_dir / 'summary.json'
    summary = _read(summary_path)
    historically_official = summary.get('historically_official', summary.get('official', False))
    records = [_read(p) for p in sorted((run_dir / 'instances').glob('*.json'))]
    if {r['spec_key'] for r in records} != {r['spec_key'] for r in audit['inputs']}:
        raise ValueError('record population differs from frozen historical config')
    metric_fields = ('mask_dead_end_count', 'mask_dead_end_rate', 'mask_dead_end_instance_rate',
                     'mask_sample_count', 'mask_dead_end_sample_rate')
    populations = [(summary, records)]
    for rung, child in summary.get('by_rung', {}).items():
        populations.append((child, [r for r in records if r['rung'] == rung]))
    for population, selected in populations:
        recomputed = aggregate_results(selected)
        population.update({key: recomputed[key] for key in metric_fields})
        population.update(official=False, current_rules_qualified=False)
    summary['historically_official'] = historically_official
    summary['evidence_status'] = 'historical; unqualified under current rules'
    summary['provenance_audit'] = 'provenance-audit.json'
    summary['historical_correction'] = {
        'scope': 'dead-sample statistics and evidence qualification only; other historical metrics preserved',
        'original_mask_dead_end_count': 32, 'original_mask_dead_end_rate': 1 / 6,
        'full_checker_rerun': False,
    }
    _write(summary_path, summary)
    _write(run_dir / 'provenance-audit.json', audit)
    row_path = run_dir / 'ROW.md'
    row = next(line for line in row_path.read_text().splitlines() if line.startswith('|'))
    cells = row.split('|')
    if 'HISTORICAL' not in cells[2]:
        cells[2] = cells[2].rstrip() + ' [HISTORICAL; current rules unqualified] '
    if len(cells) == 12:  # Archived ten columns, plus current D50 unknowns.
        cells[-1:-1] = [' departure N/A (historical; missing voyage) ',
                        ' arrival N/A (historical; missing voyage) ',
                        ' not evaluated under current rules ']
    row_path.write_text(
        '**HISTORICAL — not qualified for current-rules publication.** '
        'Original 2/6 passes are archived checker verdicts, not current approvals. '
        'No full checker rerun or provenance migration was performed; see [audit](provenance-audit.json).\n\n'
        'Corrected mask dead samples: **168/192 = 87.5%**; instances with any dead sample: **6/6 = 100%**. '
        'Other metrics below retain their original historical meaning.\n\n' + '|'.join(cells) + '\n')
    return audit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, default=DEFAULT_RUN)
    parser.add_argument('--inputs-dir', type=Path, default=DEFAULT_INPUTS)
    parser.add_argument('--split-manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--write', action='store_true', help='write correction and audit; never rewrite source evidence')
    args = parser.parse_args()
    operation = correct_run if args.write else audit_run
    result = operation(args.run_dir, inputs_dir=args.inputs_dir, split_manifest=args.split_manifest)
    print(json.dumps({key: value for key, value in result.items() if key != 'inputs'}, indent=2))

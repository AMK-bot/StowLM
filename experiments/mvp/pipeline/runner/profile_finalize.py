"""Publish measured profile settings only after a complete paired study."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .profile_analysis import analyze

HERE = Path(__file__).resolve().parent
SELECTION = HERE / 'fixtures/profile-selection.json'


def finalize(study, *, target=SELECTION, identity=None):
    study, target = Path(study).resolve(), Path(target).resolve()
    study.relative_to(HERE)
    target.relative_to(HERE)
    manifest = json.loads((study / 'jobs.json').read_text())
    checkpoints = sorted((study / 'rows').glob('*.json'))
    rows = [json.loads(p.read_text()) for p in checkpoints]
    from .profile_study import eligible_auth_failure
    pending_auth = [row['job_id'] for row in rows if eligible_auth_failure(row)]
    if pending_auth:
        return dict(status='pending',
                    message='Obsolete unconfirmed-brief failures must be repaired before publishing profiles.',
                    pending_confirmation_repairs=pending_auth)
    hatch_policies = sorted({row['evidence'].get('portfolio_policy_version') or 'legacy-single-mapping'
                             for row in rows if row.get('rule_mode') == 'one_port_per_hatch'
                             and isinstance(row.get('evidence'), dict) and row['evidence']})
    if len(hatch_policies) > 1:
        return dict(status='pending', hatch_policy_epochs=hatch_policies,
                    message='Hatch search policies changed during this study. Preserve existing rows and run a fresh quiet study for calibration.',
                    rerun_command='python -m experiments.mvp.pipeline.runner.profile_study '
                                  '--out experiments/mvp/pipeline/runner/measurements/profile-study-quiet-clean')
    # The coordinator's enriched rows carry the independent baseline-rule audit.
    if (study / 'measurements.json').is_file():
        rows = json.loads((study / 'measurements.json').read_text())['rows']
    analysis = analyze(rows, expected_jobs=manifest['jobs'])
    recommendation = analysis['recommendation']
    if (recommendation['provisional'] or recommendation['live'] is None or
            recommendation['standard'] is None):
        return dict(status='pending', message='Complete paired measurements are required before publishing profiles.',
                    recommendation=recommendation)
    binding = recommendation.get('binding_evidence', {})
    binding_passes = sum(r.get('full_pass') is True and r.get('baseline_violates_rule') is True
                         and r.get('rule_mode') == 'one_port_per_hatch' for r in rows)
    if not binding.get('baseline_plans_violating_rule') or not binding_passes:
        return dict(status='pending', message='No independently demonstrated binding-rule case; keep measuring.',
                    recommendation=recommendation)
    if identity is None:
        from experiments.mvp.pipeline.binding.adapter import verify_manifest, VERSION
        identity = dict(engine_version=VERSION, **verify_manifest())
    if not identity['passed']:
        raise ValueError('Pinned engine manifest failed verification; profiles cannot be published')
    epochs_path = study / 'source-epochs.json'
    epochs = json.loads(epochs_path.read_text()) if epochs_path.exists() else {}
    source_epochs = sorted(set(epochs.get('source_epochs', [])) | {
        row.get('execution_source_sha256', row.get('source_sha256')) for row in rows
        if row.get('execution_source_sha256', row.get('source_sha256'))})
    caveat = epochs.get('source_epoch_caveat')
    bridge_path = study / 'source-baseline-bridge.json'
    bridge = json.loads(bridge_path.read_text()) if bridge_path.exists() else None
    if bridge:
        source_epochs = sorted(set(source_epochs) | {bridge['from_source_sha256'], bridge['to_source_sha256']})
    if len(source_epochs) > 1 and not caveat:
        from .profile_study import SOURCE_CAVEAT
        caveat = SOURCE_CAVEAT
    limitations = list(recommendation['limitations'])
    if caveat and caveat not in limitations:
        limitations.append(caveat)
    if source_epochs:
        limitations.append('Execution source epochs: ' + ', '.join(source_epochs) + '.')
    if bridge and bridge.get('loaded_metadata_version_caveat'):
        limitations.append(bridge['loaded_metadata_version_caveat'])
    policy_path = study / 'policy-dependencies.json'
    policy = json.loads(policy_path.read_text()) if policy_path.exists() else None
    if policy and policy.get('source_epoch_caveat'):
        limitations.append(policy['source_epoch_caveat'])
    affected_pids = {worker['pid'] for worker in bridge.get('affected_workers', [])} if bridge else set()
    selected = dict(schema_version='stowlm.profile-selection/1', measured=True,
        published_utc=datetime.now(timezone.utc).isoformat(),
        engine_version=identity['engine_version'], engine_manifest_sha256=identity['manifest_sha256'],
        study=str(study.relative_to(HERE)), manifest_sha256=sha256_file(study / 'jobs.json'),
        data_sha256=analysis['data_sha256'], row_files={p.name: sha256_file(p) for p in checkpoints},
        profiles={name: recommendation[name] for name in ('live', 'standard')},
        reasoning=recommendation['reasoning'], completeness=recommendation['completeness'],
        binding_evidence=dict(binding, demonstrated_binding_passes=binding_passes), budget_table=analysis['budget_table'],
        paired_costs=analysis['paired_costs'], limitations=limitations,
        source_epochs=source_epochs, source_epoch_caveat=caveat,
        source_epochs_sidecar_sha256=sha256_file(epochs_path) if epochs_path.exists() else None,
        source_baseline_migration=bridge,
        policy_dependencies=policy, hatch_policy_epochs=hatch_policies,
        row_loaded_metadata_caveats={row['job_id']: bridge['loaded_metadata_version_caveat']
                                    for row in rows if row.get('worker_pid') in affected_pids})
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(selected, indent=2, allow_nan=False)+'\n')
    temporary.replace(target)
    return dict(status='published', selection_sha256=sha256_json(selected),
                profiles=selected['profiles'], reasoning=selected['reasoning'])


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--study', type=Path, required=True)
    args = cli.parse_args(argv)
    result = finalize(args.study)
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result['status'] == 'published' else 2


if __name__ == '__main__':
    raise SystemExit(main())

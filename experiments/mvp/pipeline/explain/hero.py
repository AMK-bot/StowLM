"""Prepare explanations only for the explicitly published, gated lattice hero.

No candidate search, engine run or paid call occurs here. The stored gates are
validated against their exact artifacts; this is not a fresh checker execution.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_file, sha256_json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
LATTICE = HERE.with_name('lattice')


def _json(path):
    return json.loads(Path(path).read_text())


def _lattice_path(name):
    path = Path(name)
    path = (path if path.is_absolute() else ROOT / path).resolve()
    if not path.is_relative_to(LATTICE.resolve()) or any(
            part in {'pytest-tmp', 'test-tmp', 'tmp', 'tests'} for part in path.relative_to(LATTICE).parts):
        raise ValueError('hero artifacts must be explicitly published inside lattice, outside test directories')
    return path


def _current_runtime(entry):
    from experiments.mvp.pipeline.lattice.gates import engine_identity
    from experiments.mvp.pipeline.lattice.store import _binding_source_matches
    from app.stage_physics import physics_drift
    from stowage.frozen import active_stubs
    identity = engine_identity()
    if (active_stubs() or physics_drift() or identity.get('passed') is not True
            or identity.get('engine_version') != '0.13.0'
            or entry['gate']['engine_identity'] != identity
            or not _binding_source_matches(entry)):
        raise ValueError('hero engine, runtime or physics binding is stale')


def load_hero(named_path=None):
    """Return JSON-able bound inputs, or None until the named hero is published.

    Only ``lattice/hero.json`` is considered by default. An explicit alternative
    must itself be a lattice hero manifest. Incumbents and test fixtures are
    never discovered or promoted by this loader. The local lattice catalogue
    authenticates bytes; releases must independently pin that catalogue.
    """
    manifest_path = _lattice_path(named_path or LATTICE / 'hero.json')
    if not manifest_path.exists():
        return None
    manifest = _json(manifest_path)
    if (manifest.get('schema_version') != 'stowlm.lattice-hero/1'
            or manifest.get('synthetic') is not True
            or manifest.get('selection_status') not in {'hero_checked_backup_pending', 'hero_and_backup_checked'}):
        raise ValueError('not a published checked lattice hero manifest')
    selected = manifest['hero']
    base_path, entry_path = (_lattice_path(selected[key]) for key in ('base_plan', 'base_entry'))
    catalogue_path = entry_path.parent / 'catalogue.json'
    catalogue = _json(catalogue_path)
    if catalogue.get(entry_path.stem) != sha256_file(entry_path):
        raise ValueError('named hero entry does not match the lattice catalogue')
    entry, base = _json(entry_path), _json(base_path)
    screen_path = _lattice_path(selected['screening_result'])
    screen = _json(screen_path)
    instance_path = (ROOT / selected['path']).resolve()
    if not instance_path.is_relative_to(ROOT.resolve()) or 'sealed' in instance_path.parts:
        raise ValueError('hero instance path is outside the permitted development sources')
    if sha256_file(instance_path) != selected['file_sha256']:
        raise ValueError('hero source instance file hash mismatch')
    if (entry.get('status') != 'checked_plan' or entry.get('synthetic', True) is not True
            or entry.get('canonical_rules') != [] or entry.get('key') != entry_path.stem):
        raise ValueError('named hero must be the checked synthetic base entry')
    gate = entry['gate']
    instance, plan, report = entry['instance'], entry['plan'], gate['checker']
    bindings = dict(instance_sha256=sha256_json(instance), plan_sha256=sha256_json(plan),
                    report_sha256=sha256_json(report))
    if (gate.get('passed') is not True
            or selected.get('instance_sha256') != bindings['instance_sha256']
            or entry.get('instance_sha256') != bindings['instance_sha256']
            or gate.get('instance_sha256') != bindings['instance_sha256']
            or gate.get('plan_sha256') != bindings['plan_sha256']
            or sha256_json(base.get('instance')) != bindings['instance_sha256']
            or sha256_json(base.get('plan')) != bindings['plan_sha256']
            or sha256_json(screen.get('plan')) != bindings['plan_sha256']
            or screen.get('gate', {}).get('passed') is not True
            or sha256_json(screen.get('gate', {}).get('checker')) != bindings['report_sha256']
            or sha256_json(base.get('gate', {}).get('checker')) != bindings['report_sha256']):
        raise ValueError('hero instance, plan or report binding mismatch')
    from .api import inputs
    checked_instance, checked_plan, checked_report = inputs(instance, plan, report)
    from stowage.io import load_instance
    if sha256_json(load_instance(instance_path).model_dump(mode='json')) != bindings['instance_sha256']:
        raise ValueError('hero source instance content binding mismatch')
    required = {'moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints', 'voyage'}
    if (not checked_report.passed or checked_report.count is None or checked_report.shut_out
            or not required <= checked_report.parts.keys()
            or any(not part.ran or part.error for part in checked_report.parts.values())
            or any(v.severity == 'hard' for v in checked_report.violations)
            or any(v.severity == 'hard' for p in checked_report.parts.values() for v in p.violations)):
        raise ValueError('hero report does not establish a complete checker pass')
    from experiments.mvp.pipeline.lattice.gates import gauge_verdict
    gauges = gate['gauges']
    if gauges.get('passed') is not True or not gauge_verdict(gauges['physics'], len(checked_plan.moves))['passed']:
        raise ValueError('hero every-frame gate is incomplete or failed')
    producer = entry.get('plan_producer')
    if producer == 'lattice.sequence':
        repair = gate.get('sequence_repair')
        if not repair or not repair.get('label') or not entry.get('producer_label'):
            raise ValueError('lattice sequence repair requires its explicit producer label')
    elif producer != '0.13.0':
        raise ValueError('hero producer must be Frontier Engine 0.13.0 or labelled lattice.sequence')
    if (entry.get('engine_version') != '0.13.0'
            or entry.get('engine_manifest_sha256') != gate['engine_identity']['engine_manifest_sha256']):
        raise ValueError('hero engine identity mismatch')
    _current_runtime(entry)
    return dict(instance=checked_instance.model_dump(mode='json'), plan=checked_plan.model_dump(mode='json'),
                report=checked_report.model_dump(mode='json'), binding=bindings,
                manifest_path=str(manifest_path), manifest_sha256=sha256_file(manifest_path),
                entry_path=str(entry_path), entry_sha256=sha256_file(entry_path),
                screening_path=str(screen_path), screening_sha256=sha256_file(screen_path),
                instance_path=str(instance_path), instance_file_sha256=sha256_file(instance_path),
                catalogue_sha256=sha256_file(catalogue_path), producer=producer,
                producer_label=entry.get('producer_label', 'Frontier Engine native output'),
                source_label='Stored lattice hero; recorded gates verified against exact artifacts',
                story_candidates=manifest.get('story_candidates', []))


def prepare_hero(named_path=None, out_dir=None):
    """Write readiness; a selected-box dollar projection belongs to the CLI."""
    out_dir = Path(out_dir or HERE / 'fixtures')
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        hero = load_hero(named_path)
        status = 'ready' if hero is not None else 'waiting_for_named_lattice_hero'
        readiness = dict(status=status, checked_utc=datetime.now(timezone.utc).isoformat(),
                         preparation_pid=os.getpid(),
                         manifest_path=str(named_path or LATTICE / 'hero.json'),
                         native_thai_reader_reviewed=False,
                         app_integration='integration.patch awaits application by app owner; app edits are outside explain fence')
        if hero is not None:
            from .cache import _boxes, CALLS_PER_BOX
            aboard = len(_boxes(hero['instance'], hero['plan'], hero['report']))
            readiness.update(binding=hero['binding'], producer=hero['producer'],
                             model_cost_projection=dict(status='pending_selected_boxes', label='Projection',
                                                        aboard_boxes=aboard, calls_per_model_box=CALLS_PER_BOX))
            (out_dir / 'hero-inputs.json').write_text(canonical_json(hero) + '\n')
    except (OSError, ValueError, KeyError, TypeError) as error:
        readiness = dict(status='hero_withheld', reason=str(error), native_thai_reader_reviewed=False)
    (out_dir / 'readiness.json').write_text(canonical_json(readiness) + '\n')
    return readiness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--out-dir', type=Path, default=HERE / 'fixtures')
    parser.add_argument('--wait-seconds', type=float, default=0)
    args = parser.parse_args()
    deadline = time.monotonic() + max(0, args.wait_seconds)
    while True:
        readiness = prepare_hero(args.manifest, args.out_dir)
        if readiness['status'] != 'waiting_for_named_lattice_hero' or time.monotonic() >= deadline:
            break
        time.sleep(min(5, max(0, deadline-time.monotonic())))
    print(canonical_json(readiness))


if __name__ == '__main__':
    main()

"""Prepare the frozen hero's single-rule coverage queue; never plans or gates."""
from __future__ import annotations

import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .acceptance import recorded_gate_pass
from .binding_sources import runtime_source_files
from .film_analysis import authenticated_snapshot
from .instructions import make_spec
from .store import canonical_rule_set, entry_key

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
DIRECTORY = HERE/'hero-coverage'
EXPECTED_HERO_SHA256 = 'cabff3d68d56e64ba9956761bcd0ca2abfa5077d41296db35b630efc11b04678'
PRIORITY = ('close', 'deck', 'hatch', 'dg', 'hold')


def _read(path):
    raw = Path(path).read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _engine_identity_from_disk(root):
    """Load the read-only verifier by filename, bypassing binding.__init__."""
    binding = root/'experiments/mvp/pipeline/binding'
    path = binding/'provenance.py'
    spec = importlib.util.spec_from_file_location('_lattice_coverage_provenance', path)
    verifier = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), 'exec'), verifier.__dict__)
    engine = root/'experiments/mvp/frontier/engine'
    identity = verifier.verify(engine, root, engine/'source-manifest-0.13.0-review-fixes.json')
    version = None
    for node in ast.parse((engine/'__init__.py').read_bytes()).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets):
            version = ast.literal_eval(node.value)
    if not identity.get('passed') or not version:
        raise ValueError('current engine/native source pins failed read-only verification')
    return dict(engine_version=version, engine_manifest_sha256=identity['manifest_sha256'], engine_manifest=identity)


def source_identity(root=ROOT):
    """Adapter-free source snapshot; this is not a pipeline freeze certificate."""
    root = Path(root).resolve()
    binding = root/'experiments/mvp/pipeline/binding'
    names = runtime_source_files(binding)
    binding_files = {name: sha256_file(binding/name) for name in names}
    reader_names = [f'experiments/mvp/reader/{name}.py' for name in
                    ('schema', 'pipeline', 'occupancy', 'constraints', 'refusal_evidence', 'select_binding')]
    reader_names += [f'src/llm/instructions/{name}.py' for name in ('schema', 'apply', 'mask_filter')]
    gate_names = [f'experiments/mvp/pipeline/lattice/{name}.py' for name in
                  ('batch', 'gates', 'acceptance', 'runtime', 'store', 'sequence', 'sequence_blocks', 'binding_sources')]
    feature = root/'experiments/mvp/pipeline/lattice'
    coverage_sources = sorted(feature.glob('hero_coverage*.py'))
    if not {'hero_coverage_queue.py', 'hero_coverage.py', 'hero_coverage_publish.py'} <= {path.name for path in coverage_sources}:
        raise ValueError('coverage producer modules must be final before source identity is captured')
    gate_names += [str(path.relative_to(root)) for path in coverage_sources]
    gate_names += [f'app/{name}.py' for name in ('stage_physics', 'stability', 'checker', 'check_cache')]
    gate_names += ['experiments/mvp/pipeline/runner/limits.py', 'experiments/mvp/pipeline/runner/profiles.py',
                   'experiments/mvp/pipeline/runner/fixtures/profile-selection.json']
    identity = _engine_identity_from_disk(root)
    result = dict(identity, binding_source_files=binding_files, binding_source_sha256=sha256_json(binding_files),
                  reader_source_files={name: sha256_file(root/name) for name in reader_names},
                  gate_source_files={name: sha256_file(root/name) for name in gate_names},
                  adapter_imported_for_identity=False, computed_on_frozen_code=False,
                  freeze_claim='Only a later pipeline freeze can certify these unchanged code hashes.')
    result['source_identity_sha256'] = sha256_json(result)
    if {name: sha256_file(binding/name) for name in runtime_source_files(binding)} != binding_files:
        raise ValueError('binding source changed while its identity was captured')
    if any(sha256_file(root/name) != digest for group in ('reader_source_files', 'gate_source_files')
           for name, digest in result[group].items()):
        raise ValueError('reader or gate source changed while its identity was captured')
    return result


def profile_snapshot(path, *, base):
    selection, digest = _read(path)
    if (selection.get('schema_version') != 'stowlm.profile-selection/1' or selection.get('measured') is not True
            or selection.get('decision_authority') != 'leader' or selection.get('user_authorized') is not True
            or selection.get('decision_kind') != 'explicit_partial_study_selection' or selection.get('stopped') is not True
            or selection.get('engine_version') != base['engine_version']
            or selection.get('engine_manifest_sha256') != base['engine_manifest_sha256']):
        raise ValueError('selected profile is not the authorized stopped-study choice for this engine')
    standard = selection['profiles']['standard']
    profile = dict(standard['engine_kwargs'], name='standard', engine_timeout_s=standard['engine_timeout_s'])
    if (profile['budget_s'], profile['finalize_s'], profile['engine_timeout_s']) != (300., 100., 1800.):
        raise ValueError('coverage requires the selected standard 300/100/1800 profile')
    return profile, dict(path=str(path), sha256=digest, selection_kind=selection['decision_kind'],
                         observed_completed_rows=selection['observed_completed_rows'], data_sha256=selection['data_sha256'],
                         full_selection=selection)


def requested_rules(context, classes):
    """All requested singles, ordered by category, not by existing sentence IDs."""
    bays = sorted(set(context['bays']))
    groups = {group: [] for group in PRIORITY}
    groups['close'] = [dict(type='close_bay', bay=bay) for bay in bays]
    groups['deck'] = [dict(type='max_tiers', bay=bay, zone='deck', max_tiers=cap)
                      for bay in [*bays, None] for cap in range(1, 5)]
    groups['hatch'] = [dict(type='one_port_per_hatch')]
    for cls in sorted(set(classes)):
        for anchor in [dict(kind='accommodation'), *[dict(kind='bay', bay=bay) for bay in bays]]:
            for distance in range(1, 4):
                groups['dg'].append(dict(type='dg_keep_away', imdg_class=cls, min_bays=distance, away_from=anchor))
    for bay in bays:
        maximum = context['max_tiers_by_bay'][str(bay)]['hold']
        for cap in (1, 2):
            if cap < maximum:
                groups['hold'].append(dict(type='max_tiers', bay=bay, zone='hold', max_tiers=cap))
    groups['hold'] += [dict(type='max_tiers', bay=None, zone='hold', max_tiers=cap) for cap in (1, 2)]
    return [(group, body) for group in PRIORITY for body in groups[group]]


def _cargo_classes(instance):
    active = set(instance['load_list']) | {p['container_id'] for p in instance.get('onboard', {}).get('placements', [])}
    classes = set()
    for box in instance['containers']:
        if box['id'] not in active:
            continue
        stack = [box['dg']] if box.get('dg') else []
        while stack:
            dg = stack.pop()
            classes.add(dg['imdg_class'])
            classes.update(dg.get('subsidiary_risks', []))
            stack.extend(dg.get('additional', []))
    return sorted(classes)


def _release_baseline(snapshot, digest):
    samples = []
    for source in snapshot['rows']:
        row = source['record']
        seconds = (row.get('gate') or {}).get('wall_s')
        if (row.get('status') == 'checked_plan' and row.get('instance_sha256') == digest and row.get('canonical_rules')
                and isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and math.isfinite(seconds) and seconds > 0
                and recorded_gate_pass(row['instance'], row['plan'], row['canonical_rules'], row['gate'])):
            samples.append(dict(key=source['key'], path=source['path'], raw_sha256=source['raw_sha256'],
                                computed_utc=row.get('computed_utc'), checked_utc=row['gate'].get('checked_utc'), seconds=seconds))
    if not samples:
        raise ValueError('no authenticated measured primary instruction release baseline is available')
    return dict(measurement='historical measured gate.wall_s, excluding its initial admission',
                statistic='median', seconds=statistics.median(row['seconds'] for row in samples), samples=samples,
                minimum_s=min(row['seconds'] for row in samples), maximum_s=max(row['seconds'] for row in samples),
                limitation='Loaded-machine historical native gates; not a measured duration for the new queue.')


def prepare(directory=DIRECTORY):
    """Return an immutable-input manifest; the supervisor owns persistence/jobs."""
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    directory = Path(directory).resolve()
    if not directory.is_relative_to(HERE.resolve()):
        raise ValueError('coverage directory must remain inside lattice')
    hero_path = HERE/'hero.json'
    hero, hero_pin = _read(hero_path)
    selected = hero['hero']
    if selected['instance_sha256'] != EXPECTED_HERO_SHA256:
        raise ValueError('hero identity differs from the frozen primary voyage')
    base_path = ROOT/selected['base_plan']
    base, base_pin = _read(base_path)
    digest = selected['instance_sha256']
    instance_path = ROOT/selected['path']
    instance_file_pin = sha256_file(instance_path)
    if instance_file_pin != selected['file_sha256']:
        raise ValueError('frozen hero instance input bytes changed')
    if (base['instance_sha256'] != digest or sha256_json(base['instance']) != digest or base['canonical_rules']
            or not recorded_gate_pass(base['instance'], base['plan'], [], base.get('gate', {}))):
        raise ValueError('frozen hero baseline is not bound to its complete recorded gate')
    snapshot = authenticated_snapshot(HERE/'store')
    base_key = entry_key(digest, [])
    pinned_base = next((source for source in snapshot['rows'] if source['key'] == base_key), None)
    if not pinned_base or sha256_json(pinned_base['record']['plan']) != sha256_json(base['plan']):
        raise ValueError('frozen baseline is absent from the authenticated catalogue')
    context = context_from_instance(base['instance'])
    if context != selected['context']:
        raise ValueError('frozen hero context differs from its original instance')
    classes = _cargo_classes(base['instance'])
    identity = source_identity(ROOT)
    if (identity['engine_version'] != base['engine_version'] or identity['engine_manifest_sha256'] != base['engine_manifest_sha256']):
        raise ValueError('frozen hero engine differs from current verified source identity')
    calibration = ROOT/'experiments/mvp/pipeline/runner/fixtures/profile-selection.json'
    profile, selection = profile_snapshot(calibration, base=base)
    existing = {source['key']: source for source in snapshot['rows']}
    requested, missing, skipped, seen = [], [], [], set()
    for group, body in requested_rules(context, classes):
        rules = canonical_rule_set([body])
        validate_answer(json.dumps(rules[0]), context)
        key = entry_key(digest, rules)
        if key in seen:
            continue
        seen.add(key)
        spec = make_spec(f'hero-coverage-{group}-{key[:12]}', [body])
        spec.update(key=key, coverage_group=group, priority=PRIORITY.index(group), rules=rules,
                    base_obedience_status='Not evaluated by queue preparation; the full release gate records it.')
        requested.append(spec)
        if key in existing:
            source = existing[key]
            skipped.append(dict(key=key, coverage_group=group, status=source['record'].get('status'),
                                path=source['path'], raw_sha256=source['raw_sha256']))
        else:
            missing.append(spec)
    release = _release_baseline(snapshot, digest)
    workers, per_rule_budget = 6, profile['budget_s']+profile['finalize_s']
    eta = dict(measurement='projection', workers=workers, queued_rows=len(missing), release_baseline=release,
        nominal_search_finalize_s=per_rule_budget, engine_wall_cap_s=profile['engine_timeout_s'],
        nominal_hours=len(missing)/workers*(per_rule_budget+release['seconds'])/3600,
        full_cap_hours=len(missing)/workers*(profile['engine_timeout_s']+release['seconds'])/3600,
        equation='missing / 6 * (search plus finalize, or engine wall cap, plus historical median release) / 3600',
        limitation='Balanced six-worker projection only; excludes quiet/STOP pauses, admission waits, repairs and load changes. No completion guarantee.')
    return dict(schema_version='stowlm.hero-coverage/1', campaign_id='hero-coverage', created_utc=datetime.now(timezone.utc).isoformat(),
        synthetic=True, hero_path=str(hero_path), hero_sha256=hero_pin,
        instance_path=str(instance_path), instance_file_sha256=instance_file_pin, instance_sha256=digest, base_plan=base['plan'],
        base_fixture=str(base_path), base_fixture_sha256=base_pin, base_plan_sha256=sha256_json(base['plan']),
        instance_fixture_sha256=sha256_json(base['instance']), classes=classes, context=context,
        existing_catalogue_path=str(HERE/'store/catalogue.json'), existing_catalogue_sha256=snapshot['catalogue_sha256'],
        existing_pins=[{key: value for key, value in source.items() if key != 'record'} | {'status': source['record'].get('status')}
                       for source in snapshot['rows']],
        existing_counts=dict(catalogue_entries=len(existing), checked_plans=sum(s['record'].get('status') == 'checked_plan' for s in snapshot['rows']),
                             proved_refusals=sum(s['record'].get('status') == 'proved_refusal' for s in snapshot['rows'])),
        requested=requested, instructions=missing, keys=[s['key'] for s in missing], skipped_existing=skipped,
        coverage_counts=dict(requested=len(requested), missing=len(missing), skipped=len(skipped),
            requested_by_group=dict(Counter(s['coverage_group'] for s in requested)),
            missing_by_group=dict(Counter(s['coverage_group'] for s in missing))),
        profile=profile, profile_comparison_fields=['name', 'budget_s', 'finalize_s', 'seed', 'config', 'engine_timeout_s'],
        profile_selection_path=str(calibration), profile_selection_sha256=selection['sha256'], profile_selection=selection,
        source_identity=identity, queue_source_sha256=sha256_file(Path(__file__)),
        run_dir=str(directory/'runs'), store_dir=str(directory/'store'), publish_store_dir=str(HERE/'store'),
        fixtures_dir=str(directory/'fixtures'), state_path=str(ROOT/'experiments/fleet/core-state.json'),
        quiet_flag=str(ROOT/'experiments/fleet/quiet.flag'), stop_flag=str(directory/'STOP'),
        concurrency=dict(max_workers=workers, admission='popcount(mask)>=8, load1<=45, telemetry age<120s; wait'), eta=eta,
        computed_on_frozen_code=False, freeze_claim='A later freeze must verify unchanged source hashes before making this claim.',
        prior_off_lattice_reservations='Explicitly superseded for this coverage expansion; only existing catalogue keys are skipped.')

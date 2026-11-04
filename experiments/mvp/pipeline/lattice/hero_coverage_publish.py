"""Add authenticated coverage results without replacing any existing entry."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_json
from .acceptance import recorded_gate_pass
from .store import DEFAULT_STORE, canonical_rule_set, entry_key, _atomic

HERE = Path(__file__).resolve().parent
IDENTITY_FIELDS = ('manifest_sha256', 'native_manifest_sha256', 'source_tree_sha256',
                   'pins_sha256', 'engine_file_map_sha256', 'native_file_map_sha256',
                   'identity_sha256', 'source_files', 'native_files')
SHA = re.compile('[0-9a-f]{64}')


def _inside(path):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE.resolve()):
        raise ValueError('coverage publication paths must remain inside lattice')
    return path


def _regular(path):
    if path.is_symlink():
        raise ValueError('coverage publication does not follow entry or lock symlinks')
    return path


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _catalogue(raw):
    value = json.loads(raw)
    if not isinstance(value, dict) or any(not isinstance(k, str) or not SHA.fullmatch(k)
            or not isinstance(v, str) or not SHA.fullmatch(v) for k, v in value.items()):
        raise ValueError('coverage catalogue is malformed')
    return value


def _immutable_bytes(path, raw):
    """Atomic create-only publication; even an unexpected writer cannot be replaced."""
    path = _inside(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.'+path.name+'.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _regular(path).read_bytes() != raw:
                raise ValueError('existing entry bytes differ; publication never replaces them')
            return False
        return True
    finally:
        Path(temporary).unlink(missing_ok=True)


def _same_engine(actual, expected):
    return isinstance(actual, dict) and actual.get('passed') is True and expected.get('passed') is True \
        and all(expected.get(key) is not None and actual.get(key) == expected[key] for key in IDENTITY_FIELDS)


def _source_digest(identity):
    if not isinstance(identity, dict):
        raise ValueError('coverage needs its precompute and postcompute source identities')
    value = deepcopy(identity)
    claimed = value.pop('source_identity_sha256', None)
    if claimed != sha256_json(value):
        raise ValueError('coverage source identity digest does not match its file maps')
    for field in ('binding_source_files', 'reader_source_files', 'gate_source_files'):
        if not isinstance(value.get(field), dict) or not value[field]:
            raise ValueError('coverage source identity is missing runtime/reader/producer maps')
    if value.get('binding_source_sha256') != sha256_json(value['binding_source_files']):
        raise ValueError('binding source file map does not match its digest')
    if value.get('adapter_imported_for_identity') is not False or value.get('computed_on_frozen_code') is not False:
        raise ValueError('coverage identity must be adapter-free and make no premature freeze claim')
    return claimed


def _profile_core(profile):
    if not isinstance(profile, dict):
        raise ValueError('coverage requires its selected standard profile')
    profile = dict(profile)
    if 'engine_kwargs' in profile:
        profile = dict(profile['engine_kwargs'], engine_timeout_s=profile['engine_timeout_s'], name=profile.get('name', 'standard'))
    config = profile.get('config')
    if is_dataclass(config):
        config = asdict(config)
    if profile.get('name') != 'standard' or (profile.get('budget_s'), profile.get('finalize_s'), profile.get('engine_timeout_s')) != (300., 100., 1800.):
        raise ValueError('coverage requires standard 300/100 with the separate 1800-second wall')
    return dict(budget_s=profile['budget_s'], finalize_s=profile['finalize_s'],
                engine_timeout_s=profile['engine_timeout_s'], seed=profile['seed'], config=config)


def _clean_publication(row):
    """No checker invocation: validate recorded artifacts and current hook state."""
    from stowage.model import Instance, MovePlan, CheckReport
    from stowage.frozen import active_stubs, require_clean
    if active_stubs():
        raise ValueError('active checker stubs block coverage publication')
    hooks = sys.modules.get('experiments.mvp.pipeline.binding.hooks')
    if hooks is not None and hooks.binding_active():
        raise ValueError('binding hooks must be inactive at coverage publication')
    items = [Instance.model_validate(row['instance'])]
    for field in ('plan', 'base_plan', 'original_binding_plan'):
        if row.get(field) is not None:
            items.append(MovePlan.model_validate(row[field]))
    if row.get('gate', {}).get('checker') is not None:
        items.append(CheckReport.model_validate(row['gate']['checker']))
    require_clean(*items, what='add-only hero coverage publication')
    return dict(active_stubs={}, binding_hooks_active=False, native_checker_called=False,
                scope='Current publication boundary plus clean recorded artifact provenance; not a repeated native gate.')


def _refusal_evidence(row):
    """Rebuild necessary static facts; never run a hatch allocator or search."""
    from stowage.model import Instance
    from experiments.mvp.reader.schema import context_from_instance, validate_answer, OnePortPerHatch
    from experiments.mvp.reader.refusal_evidence import build_static_proof
    from llm.instructions.apply import apply as constrain
    instance = Instance.model_validate(row['instance'])
    context = context_from_instance(instance)
    answers = [validate_answer(canonical_json(rule), context) for rule in row['canonical_rules']]
    if len(answers) == 1 and isinstance(answers[0], OnePortPerHatch):
        from .store import _refusal_proof
        started = time.monotonic()
        proof = _refusal_proof(row)
        if not isinstance(proof, dict) or proof.get('proven_impossible') is not True:
            raise ValueError('hatch refusal is not proved by the current necessary allocator')
        return dict(instance_sha256=row['instance_sha256'], canonical_rules=row['canonical_rules'],
            necessary_proof=proof, native_checker_called=False, planner_called=False,
            verification_source='store._refusal_proof: current necessary hatch allocation/capacity screen',
            necessary_proof_wall_s=time.monotonic()-started,
            timing_scope='Necessary-proof validation including its capacity admission; no checker or engine search.')
    if not answers or any(isinstance(answer, OnePortPerHatch) for answer in answers):
        raise ValueError('combined hatch refusal requires its own replayable necessary proof')
    proofs = [build_static_proof(instance, answer) for answer in answers]
    recorded = (row.get('proof') or {}).get('arithmetic')
    if len(proofs) == 1:
        if not proofs[0]['proven_impossible'] or recorded != proofs[0]:
            raise ValueError('recorded refusal arithmetic is not the exact current necessary proof')
        return dict(instance_sha256=row['instance_sha256'], canonical_rules=row['canonical_rules'],
                    necessary_proof=proofs[0], native_checker_called=False, planner_called=False)
    combined = None
    for answer in answers:
        combined = constrain(instance, answer, base=combined)
    active = set(instance.load_list)
    demand = sum(1 if box.length_ft == 20 else 2 for box in instance.containers if box.id in active)
    opened = len(combined.open_cells())
    if demand <= opened or not isinstance(recorded, dict) or recorded.get('proven_impossible') is not True \
            or recorded.get('demand_cells') != demand or recorded.get('open_cells') != opened:
        raise ValueError('combined refusal lacks matching necessary capacity arithmetic')
    return dict(instance_sha256=row['instance_sha256'], canonical_rules=row['canonical_rules'],
                necessary_proof=dict(proven_impossible=True, demand_cells=demand, open_cells=opened,
                    equation=f'{demand} needed > {opened} open'), native_checker_called=False, planner_called=False)


def _engine_evidence(row, identity, run_dir, key):
    expected = identity['engine_manifest']
    evidence = row.get('binding_evidence') or {}
    if row.get('engine_version') != identity['engine_version'] or row.get('engine_manifest_sha256') != identity['engine_manifest_sha256']:
        raise ValueError('coverage entry engine identity differs from current pinned engine')
    if evidence.get('engine_version') != identity['engine_version'] or evidence.get('engine_manifest_sha256') != identity['engine_manifest_sha256']:
        raise ValueError('original binding evidence engine identity differs from its entry')
    if evidence.get('engine_manifest') is not None:
        if evidence.get('native_manifest_sha256') != expected['native_manifest_sha256'] or \
                not _same_engine(evidence['engine_manifest'], expected) or not _same_engine(evidence.get('engine_manifest_after'), expected):
            raise ValueError('binding before/after native engine identities differ')
        return dict(kind='binding_before_and_after', engine_manifest=expected)
    if not evidence.get('recovered_incumbent'):
        raise ValueError('coverage is missing original native engine evidence')
    path = _regular(_inside(Path(run_dir)/(key+'.incumbent.json')))
    raw = path.read_bytes()
    checkpoint = json.loads(raw)
    event = checkpoint.get('event') or {}
    original = row.get('original_binding_plan', row.get('plan'))
    if checkpoint.get('instance_sha256') != row['instance_sha256'] or checkpoint.get('key') != key \
            or sha256_json(event.get('plan')) != sha256_json(original) or event.get('passed') is not True \
            or event.get('instruction_recheck_passed') is not True or not _same_engine(event.get('publication_identity'), expected):
        raise ValueError('timeout incumbent is not bound to the original instance, plan and native identity')
    event_rules = [audit['canonical_envelope'] for audit in event.get('rules', [])]
    if canonical_rule_set(event_rules) != row['canonical_rules'] or any((audit.get('instruction_recheck') or {}).get('passed') is not True for audit in event.get('rules', [])):
        raise ValueError('timeout incumbent instruction audits differ from the confirmed rules')
    return dict(kind='checked_native_incumbent_at_timeout', checkpoint_path=str(path),
                checkpoint_sha256=_hash(raw), engine_manifest=expected)


def _validate(row, key, identity, expected_profile, run_dir):
    if row.get('key') != key or entry_key(row['instance_sha256'], row['canonical_rules']) != key \
            or sha256_json(row.get('instance')) != row.get('instance_sha256'):
        raise ValueError('coverage original instance/rule key is not bound')
    if row.get('canonical_rules') != canonical_rule_set(row['canonical_rules']) or row.get('confirmation_source') != 'batch':
        raise ValueError('coverage rules must be canonical, explicitly batch-confirmed structured instructions')
    if row.get('rerun_required') or row.get('status') not in ('checked_plan', 'proved_refusal') or not row.get('computed_utc'):
        raise ValueError('coverage result is not a terminal accepted result')
    if row.get('binding_source_sha256') != identity['binding_source_sha256'] or row.get('binding_source_after_sha256') != identity['binding_source_sha256']:
        raise ValueError('new coverage must match current binding before and after computation')
    if _profile_core(row.get('profile')) != _profile_core(expected_profile):
        raise ValueError('coverage result differs from its selected profile/configuration')
    clean = _clean_publication(row)
    engine = _engine_evidence(row, identity, run_dir, key)
    proof = None
    if row['status'] == 'checked_plan':
        gate = row.get('gate') or {}
        if not recorded_gate_pass(row['instance'], row.get('plan'), row['canonical_rules'], gate):
            raise ValueError('coverage recorded full gate is not bound to the exact inputs')
        checked_identity = gate.get('engine_identity') or {}
        if checked_identity.get('passed') is not True or checked_identity.get('engine_version') != identity['engine_version'] or checked_identity.get('engine_manifest_sha256') != identity['engine_manifest_sha256'] \
                or not _same_engine(checked_identity.get('engine_manifest'), identity['engine_manifest']):
            raise ValueError('recorded gate native engine identity differs from current pins')
        if row.get('base_plan') is not None and gate.get('base_passed') is not True:
            raise ValueError('coverage recorded baseline did not pass')
    else:
        proof = _refusal_evidence(row)
    return clean, engine, proof


def publish_staged(key, *, staging_store, store_dir=DEFAULT_STORE,
                   expected_staging_catalogue_sha256=None, expected_profile=None,
                   coverage_start_identity=None, coverage_end_identity=None, run_dir=None):
    """Authenticate staged work, then append missing keys under one target lock.

    The raw staged result is retained. An immutable, augmented publication copy
    carries its exact source and computation identities. No native gate repeats.
    """
    if not isinstance(key, str) or not SHA.fullmatch(key):
        raise ValueError('coverage key must be a lowercase SHA256 digest')
    source, destination = _inside(staging_store), _inside(store_dir)
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError('staging and destination stores must be disjoint')
    run_dir = _inside(run_dir or source.parent/'runs')
    with _regular(source/'.catalogue.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        catalogue_raw = _regular(source/'catalogue.json').read_bytes()
        stage_pin = _hash(catalogue_raw)
        if expected_staging_catalogue_sha256 is not None and stage_pin != expected_staging_catalogue_sha256:
            raise ValueError('staging catalogue differs from its independent pin')
        catalogue = _catalogue(catalogue_raw)
        raw = _regular(source/(key+'.json')).read_bytes()
        if catalogue.get(key) != _hash(raw):
            raise ValueError('staged entry failed catalogue authentication')
        row = json.loads(raw)
    from .hero_coverage_queue import source_identity
    before = source_identity()
    source_digest = _source_digest(before)
    if _source_digest(coverage_start_identity) != source_digest or _source_digest(coverage_end_identity) != source_digest:
        raise ValueError('coverage producer/runtime sources differ from computation start/end')
    clean, engine, proof = _validate(row, key, before, expected_profile, run_dir)
    value = deepcopy(row)
    value['coverage_provenance'] = dict(schema_version='stowlm.hero-coverage-publication/1',
        staged_entry_sha256=_hash(raw), staging_store=str(source),
        coverage_start_identity=coverage_start_identity, coverage_end_identity=coverage_end_identity,
        publication_source_identity=before, profile=_profile_core(expected_profile),
        adapter_free_publication=clean, original_engine_evidence=engine, refusal_validation=proof,
        native_gates_repeated=False, computed_on_frozen_code=False,
        freeze_claim='Only the later pipeline freeze can establish unchanged computation source hashes.')
    candidate_raw = (canonical_json(value)+'\n').encode()
    candidate_sha = _hash(candidate_raw)
    candidate = source/'publication-candidates'/key/(candidate_sha+'.json')
    _immutable_bytes(candidate, candidate_raw)
    catalogue_copy = source/'publication-catalogues'/(stage_pin+'.json')
    _immutable_bytes(catalogue_copy, catalogue_raw)
    destination.mkdir(parents=True, exist_ok=True)
    with _regular(destination/'.catalogue.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = source_identity()
        if _source_digest(current) != source_digest:
            raise ValueError('coverage identity changed during publication')
        _clean_publication(row)
        index = _regular(destination/'catalogue.json')
        old_raw = index.read_bytes() if index.exists() else b'{}\n'
        target_catalogue = _catalogue(old_raw)
        target = _regular(destination/(key+'.json'))
        receipt = dict(key=key, staged_entry_sha256=_hash(raw), staging_catalogue_sha256=stage_pin,
            publication_candidate_path=str(candidate), publication_candidate_sha256=candidate_sha,
            catalogue_before_sha256=_hash(old_raw), path=str(target),
            native_gates_repeated=False, launch_catalogue_refresh_required=True,
            checked_utc=datetime.now(timezone.utc).isoformat())
        if key in target_catalogue:
            existing = target.read_bytes()
            if _hash(existing) != target_catalogue[key]:
                raise ValueError('existing target entry failed its catalogue authentication; preserved unchanged')
            return dict(receipt, status='already_present', published=False, added_to_catalogue=False,
                        entry_sha256=_hash(existing), catalogue_after_sha256=_hash(old_raw))
        if target.exists() and target.read_bytes() != candidate_raw:
            # Repeating a necessary hatch proof measures a different duration.
            # Recover the exact first publication bytes, never rewrite timing.
            orphan_raw = target.read_bytes()
            orphan = json.loads(orphan_raw)
            left, right = deepcopy(orphan), deepcopy(value)
            for comparison in (left, right):
                validation = comparison.get('coverage_provenance', {}).get('refusal_validation') or {}
                validation.pop('necessary_proof_wall_s', None)
            old_candidate = source/'publication-candidates'/key/(_hash(orphan_raw)+'.json')
            if left != right or not old_candidate.is_file() or old_candidate.read_bytes() != orphan_raw:
                raise ValueError('orphan entry differs from its immutable authenticated publication candidate')
            candidate_raw, candidate_sha, candidate = orphan_raw, _hash(orphan_raw), old_candidate
            receipt.update(publication_candidate_path=str(candidate), publication_candidate_sha256=candidate_sha)
        created = _immutable_bytes(target, candidate_raw)
        target_catalogue[key] = candidate_sha
        _atomic(index, target_catalogue)
        return dict(receipt, status='published' if created else 'recovered_orphan', published=True,
                    added_to_catalogue=True, entry_sha256=candidate_sha,
                    catalogue_after_sha256=_hash(index.read_bytes()))

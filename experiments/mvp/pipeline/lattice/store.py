"""Content-bound checkpoints and stored-plan lookup. Sentences never form keys."""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
import ast
import fcntl
import hashlib
import importlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from types import CodeType, MappingProxyType

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_file, sha256_json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
DEFAULT_STORE = HERE / 'store'
_pins = {}
_gated = {}
_STATE_LOCK = threading.RLock()
_generation = 0
_DELIVERY = threading.local()
_deliveries = OrderedDict()
_READER_LOCK = threading.RLock()
_READER_MODULES = {
    'experiments.mvp.reader.' + name: ROOT / 'experiments/mvp/reader' / (name + '.py')
    for name in ('schema', 'pipeline', 'occupancy', 'constraints')
} | {
    'llm.instructions.' + name: ROOT / 'src/llm/instructions' / (name + '.py')
    for name in ('schema', 'apply', 'mask_filter')
}
_READER_AT_IMPORT = {name: path.read_bytes() for name, path in _READER_MODULES.items()}
_reader_attested = {}


@dataclass(frozen=True, eq=False)
class _CatalogueSnapshot(Mapping):
    entries: Mapping
    sha256: str
    generation: int

    def __getitem__(self, key):
        return self.entries[key]

    def __iter__(self):
        return iter(self.entries)

    def __len__(self):
        return len(self.entries)


def _catalogue_path(store_dir):
    return str(Path(store_dir).resolve() / 'catalogue.json')


def _snapshot_current(store_dir, snapshot):
    return _pins.get(_catalogue_path(store_dir)) is snapshot


def _compiled_functions(code):
    found = {}
    for value in code.co_consts:
        if isinstance(value, CodeType):
            found[value.co_qualname] = value
            found.update(_compiled_functions(value))
    return found


def _attest_reader_module(module, path, raw):
    """Compare loaded implementations to disk, even after an earlier import."""
    compiled = _compiled_functions(compile(raw, str(path), 'exec', dont_inherit=True))
    functions = {}
    for name, value in vars(module).items():
        if inspect.isfunction(value) and value.__module__ == module.__name__:
            functions[name] = value
        elif inspect.isclass(value) and value.__module__ == module.__name__:
            for method, function in vars(value).items():
                if isinstance(function, (staticmethod, classmethod)):
                    function = function.__func__
                if inspect.isfunction(function) and function.__code__.co_qualname in compiled:
                    functions[name + '.' + method] = function
    for name, function in functions.items():
        expected = compiled.get(name)
        if expected is None or function.__code__ != expected:
            raise ValueError('reader loaded implementation differs from disk: ' + module.__name__ + '.' + name)
    def definitions(body, prefix=''):
        names = set()
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(prefix + node.name)
            elif isinstance(node, ast.ClassDef):
                names.update(definitions(node.body, prefix + node.name + '.'))
        return names
    if not definitions(ast.parse(raw).body).issubset(functions):
        raise ValueError('reader loaded definitions differ from disk: ' + module.__name__)
    values, models, envelopes = {}, {}, {}
    # Schema fields/decorators execute at class definition; function bytecode
    # alone cannot attest their bounds or union members. Rebuild only these
    # definition-only schema modules, never a planner or parser invocation.
    if module.__name__.endswith('.schema'):
        namespace = {'__name__': module.__name__, '__file__': str(path)}
        exec(compile(raw, str(path), 'exec', dont_inherit=True), namespace)
        for name, expected in namespace.items():
            actual = vars(module).get(name)
            if inspect.isclass(expected) and expected.__module__ == module.__name__ and hasattr(expected, 'model_json_schema'):
                if actual is None or actual.model_json_schema() != expected.model_json_schema():
                    raise ValueError('reader loaded schema differs from disk: ' + module.__name__ + '.' + name)
                models[name] = actual, expected.model_json_schema()
            elif isinstance(expected, (str, int, float, bool, tuple, list, dict, set, frozenset)) and not name.startswith('__'):
                if actual != expected:
                    raise ValueError('reader loaded schema constant differs from disk: ' + module.__name__ + '.' + name)
                values[name] = deepcopy(expected)
        for name in ('_ENVELOPE',):
            if name in namespace and vars(module)[name].json_schema() != namespace[name].json_schema():
                raise ValueError('reader loaded envelope differs from disk: ' + module.__name__)
            if name in namespace:
                envelopes[name] = vars(module)[name], namespace[name].json_schema()
    for node in ast.parse(raw).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            try:
                expected = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and vars(module).get(target.id) != expected:
                    raise ValueError('reader loaded constant differs from disk: ' + module.__name__ + '.' + target.id)
                if isinstance(target, ast.Name):
                    values[target.id] = deepcopy(expected)
    return dict(functions=tuple((name, function, function.__code__) for name, function in sorted(functions.items())),
                values=values, models=models, envelopes=envelopes)


def _reader_identity():
    """Process-loaded rule semantics, rejecting both disk and loaded-code drift."""
    with _READER_LOCK:
        digests = {}
        for name, path in _READER_MODULES.items():
            raw = path.read_bytes()
            if raw != _READER_AT_IMPORT[name]:
                raise ValueError('reader sources changed since lattice was imported: restart before regating')
            module = importlib.import_module(name)
            if name not in _reader_attested:
                _reader_attested[name] = _attest_reader_module(module, path, raw)
            attested = _reader_attested[name]
            for qualified, original, code in attested['functions']:
                value = vars(module)[qualified.split('.')[0]]
                for part in qualified.split('.')[1:]:
                    value = inspect.getattr_static(value, part)
                if isinstance(value, (staticmethod, classmethod)):
                    value = value.__func__
                if value is not original or value.__code__ != code:
                    raise ValueError('reader loaded implementation changed: ' + name + '.' + qualified)
            if any(vars(module).get(key) != value for key, value in attested['values'].items()):
                raise ValueError('reader loaded constants changed: ' + name)
            for key, (original, schema) in attested['models'].items():
                if vars(module).get(key) is not original or original.model_json_schema() != schema:
                    raise ValueError('reader loaded schema changed: ' + name + '.' + key)
            for key, (original, schema) in attested['envelopes'].items():
                if vars(module).get(key) is not original or original.json_schema() != schema:
                    raise ValueError('reader loaded envelope changed: ' + name + '.' + key)
            if path.read_bytes() != raw:
                raise ValueError('reader source changed during identity verification')
            digests[name] = hashlib.sha256(raw).hexdigest()
        return sha256_json(digests)


def binding_source_files():
    from .binding_sources import runtime_source_files
    return runtime_source_files(ROOT / 'experiments/mvp/pipeline/binding')


def binding_fingerprint():
    from .binding_sources import runtime_fingerprint
    return runtime_fingerprint(ROOT / 'experiments/mvp/pipeline/binding')


def binding_compatibility(row):
    from .binding_compat import nonhatch_compatibility
    from .measurement_compat import normalize_row, measurement_compatibility as measure
    root = ROOT / 'experiments/mvp/pipeline/binding'
    normalized, alias = normalize_row(row, binding_root=root)
    proof = nonhatch_compatibility(normalized, binding_root=root)
    if proof is not None:
        proof['historical_binding_source_sha256'] = row.get('binding_source_sha256')
        if alias is not None:
            proof['measurement_compatibility'] = alias
        return proof
    return measure(row, binding_root=root)


def measurement_compatibility(row):
    from .measurement_compat import measurement_compatibility as measure
    return measure(row, binding_root=ROOT / 'experiments/mvp/pipeline/binding')


def _binding_source_matches(row):
    try:
        current = binding_fingerprint()
        strict = row.get('binding_source_sha256') == current and row.get('binding_source_after_sha256', current) == current
        compatibility = None if strict else binding_compatibility(row)
        if not strict and compatibility is None:
            return False
        if row.get('source_screen'):
            checked = deepcopy(row)
            _screen_source_guard(checked)
            if checked['binding_source_sha256'] != current and not checked.get('binding_compatibility'):
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _rule_list(rules):
    from experiments.mvp.reader.schema import canonicalize
    if isinstance(rules, str):
        rules = json.loads(rules)
    if isinstance(rules, dict) or hasattr(rules, 'model_dump'):
        rules = [rules]
    out = []
    for rule in rules:
        if isinstance(rule, dict) and 'type' in rule:
            rule = dict(schema_version='stowlm.reader/2', constraint=rule)
        envelope = canonicalize(rule)
        if 'refusal' in envelope:
            raise ValueError('a reader refusal is not an executable lattice rule')
        out.append(envelope)
    return out


def canonical_rule_set(rules):
    """Sorted, duplicate-free envelopes. Schema validation rejects sentence keys."""
    return [json.loads(row) for row in sorted({canonical_json(r) for r in _rule_list(rules)})]


def entry_key(instance_sha256, rules):
    if not isinstance(instance_sha256, str) or not re.fullmatch('[0-9a-f]{64}', instance_sha256):
        raise ValueError('instance_sha256 must be a lowercase SHA256 digest')
    return sha256_json(dict(instance_sha256=instance_sha256, canonical_rules=canonical_rule_set(rules)))


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name+'.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(canonical_json(value)+'\n')
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _screen_source_guard(row):
    """Bind old screen workers to a PID-bound runtime-source snapshot."""
    if not row.get('source_screen'):
        return
    try:
        source = (ROOT / row['source_screen']).resolve()
        if not source.is_relative_to(HERE.resolve()):
            raise ValueError('screen source is outside lattice')
        if source.name.endswith('.preview.json'):
            source = source.with_name(source.name.removesuffix('.preview.json') + '.json')
        progress_path = source.with_suffix('.progress.json')
        sidecar_path = source.with_suffix('.progress.source.json')
        progress = json.loads(progress_path.read_text())
        sidecar = json.loads(sidecar_path.read_text())
        if not progress.get('pid') or sidecar.get('pid') != progress['pid']:
            raise ValueError('source snapshot PID does not match screen worker')
        started = datetime.fromisoformat(progress['started_utc'].replace('Z', '+00:00'))
        if started.tzinfo is None:
            raise ValueError('screen worker start lacks a timezone')
        started_unix = started.timestamp()
        if not math.isfinite(started_unix) or sidecar.get('worker_started_unix') != started_unix:
            raise ValueError('source snapshot start does not match screen worker')
        source_files = sidecar['source_files']
        hashes = {}
        compatibility = binding_compatibility(row)
        names = tuple(compatibility['historical_runtime_files']) if compatibility else binding_source_files()
        applied_init_delta = False
        if not names:
            raise ValueError('binding runtime source files are missing')
        for name in names:
            relative = f'experiments/mvp/pipeline/binding/{name}'
            recorded = source_files[relative]
            current = ROOT / relative
            recorded_mtime, current_mtime = float(recorded['mtime_s']), current.stat().st_mtime
            init_delta = bool(compatibility and compatibility.get('export_delta_sha256') and name == '__init__.py' and
                              recorded['sha256'] == compatibility['old_init_sha256'])
            if not math.isfinite(recorded_mtime) or recorded_mtime > started_unix or \
                    not math.isfinite(current_mtime) or current_mtime > started_unix and not init_delta:
                raise ValueError(f'{name} changed after screen worker start')
            digest = sha256_file(current)
            if digest != recorded['sha256'] and not (init_delta and digest == compatibility['new_init_sha256']):
                raise ValueError(f'{name} differs from screen source snapshot')
            hashes[name] = recorded['sha256']
            applied_init_delta = applied_init_delta or bool(init_delta and (current_mtime > started_unix or digest != recorded['sha256']))
        fingerprint = sha256_json(hashes)
        if compatibility:
            if fingerprint != compatibility.get('source_screen_runtime_sha256', compatibility['planning_origin_binding_source_sha256']):
                raise ValueError('screen snapshot does not reconstruct the approved historical runtime')
            row['binding_compatibility'] = compatibility
        else:
            row.update(binding_source_sha256=fingerprint, binding_source_after_sha256=fingerprint)
        row.update(
                   binding_source_files=hashes,
                   binding_source_scope='binding runtime modules imported by the confirmed-rule planning API',
                   source_screen_provenance=dict(pid=progress['pid'], worker_started_utc=progress['started_utc'],
                       snapshot_path=str(sidecar_path.relative_to(ROOT)), snapshot_sha256=sha256_file(sidecar_path),
                       runtime_mtimes_before_worker_start=not applied_init_delta,
                       approved_init_export_delta=bool(compatibility and compatibility.get('export_delta_sha256')),
                       basis='PID-bound runtime snapshot; only the pinned reviewed init export delta may postdate worker start'))
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ValueError(f'rerun_required: screen runtime source cannot be established: {error}') from error


def save_entry(entry, *, store_dir=DEFAULT_STORE):
    """Atomically checkpoint one result and update the local trust catalogue.

    The batch must call gate_plan before saving a success. Reading never trusts
    its verdict: launch regating recomputes the complete gate.
    """
    store_dir = Path(store_dir)
    row = deepcopy(entry)
    row['canonical_rules'] = canonical_rule_set(row['canonical_rules'])
    key = entry_key(row['instance_sha256'], row['canonical_rules'])
    if row.get('instance') is not None and sha256_json(row['instance']) != row['instance_sha256']:
        raise ValueError('original instance does not match instance_sha256')
    row.update(mode='stored', label='stored', key=key)
    store_dir.mkdir(parents=True, exist_ok=True)
    path = store_dir / (key+'.json')
    with (store_dir / '.catalogue.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _screen_source_guard(row)
        if compatibility := binding_compatibility(row):
            row['binding_compatibility'] = compatibility
        _atomic(path, row)
        catalogue_path = store_dir / 'catalogue.json'
        catalogue = json.loads(catalogue_path.read_text()) if catalogue_path.exists() else {}
        catalogue[key] = sha256_file(path)
        _atomic(catalogue_path, catalogue)
    return path


def _catalogue(store_dir, *, refresh=False, expected_sha256=None):
    global _generation
    path = Path(store_dir).resolve() / 'catalogue.json'
    token = str(path)
    with _STATE_LOCK:
        if refresh or token not in _pins:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if expected_sha256 is not None and digest != expected_sha256:
                raise ValueError('lattice catalogue does not match independently pinned SHA256')
            catalogue = json.loads(raw)
            if not isinstance(catalogue, dict) or any(
                    not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
                    for pair in catalogue.items() for value in pair):
                raise ValueError('lattice catalogue has malformed entry keys or digests')
            _generation += 1
            _pins[token] = _CatalogueSnapshot(MappingProxyType(catalogue), digest, _generation)
        return _pins[token]


def catalogue_sha256(*, store_dir=DEFAULT_STORE):
    """Release tooling pins this digest independently, e.g. in demo config."""
    return sha256_file(Path(store_dir) / 'catalogue.json')


def _read(key, store_dir, *, catalogue=None):
    path = Path(store_dir).resolve() / (key+'.json')
    snapshot = _catalogue(store_dir) if catalogue is None else catalogue
    expected = snapshot.get(key)
    if not expected:
        return None
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        return None
    return json.loads(raw)


def _regate_entry(row):
    from .gates import engine_identity, gate_plan
    if not _binding_source_matches(row):
        return None
    reader_identity = _reader_identity()
    identity = engine_identity()
    if (not identity['passed'] or row.get('engine_version') != identity['engine_version'] or
            row.get('engine_manifest_sha256') != identity['engine_manifest_sha256'] or
            not row.get('computed_utc') or row.get('status', 'checked_plan') != 'checked_plan' or
            not row.get('plan') or not row.get('instance') or
            sha256_json(row['instance']) != row.get('instance_sha256')):
        return None
    audit = gate_plan(row['instance'], row['plan'], row['canonical_rules'], base_plan=row.get('base_plan'))
    if not audit['passed'] or _reader_identity() != reader_identity:
        return None
    result = dict(row, gate=audit, checker=audit['checker'], lifts=audit['lifts'],
                  mode='stored', label='stored', rechecked_utc=audit['checked_utc'],
                  instruction_gate_identity=reader_identity,
                  stored_message='Stored plan, computed on '+row['computed_utc']+', rechecked at '+audit['checked_utc'])
    if compatibility := binding_compatibility(row):
        result['binding_compatibility'] = compatibility
    return result


def _current_token(row):
    from .gates import engine_identity
    from app.stage_physics import physics_drift, physics_epoch
    from stowage.frozen import active_stubs
    identity = engine_identity()
    if not identity['passed'] or physics_drift() or active_stubs():
        return None
    return sha256_json(row), identity['engine_manifest_sha256'], physics_epoch(), _reader_identity()


def _lookup_label(result, *, fresh):
    from .sequence_provenance import frame_provenance
    result['frame_provenance'] = frame_provenance(result)
    result['gate_source'] = 'fresh' if fresh else 'memo'
    result['freshly_rechecked'] = fresh
    result['stored_message'] = ('Stored plan, computed on '+result['computed_utc']+
                                ', rechecked at '+result['rechecked_utc']+
                                ('' if fresh else ' (memoized in this process)')+
                                '; '+result['frame_provenance']['label'])
    if result.get('budget_status') == 'timeout_with_checked_incumbent' and result.get('engine_wall_s') is None:
        result['historical_nominal_timing'] = dict(wall_s=result.get('wall_s'),
            native_search_wall_s=result.get('native_search_wall_s'), label='historical nominal timeout values; elapsed time unavailable')
        result.update(wall_s=None, native_search_wall_s=None,
                      timeout_allowance_s=(result.get('profile') or {}).get('engine_timeout_s'),
                      historical_timeout_elapsed='unavailable')
    if result.get('plan_producer') == 'lattice.standard_plan_pool' and \
            result.get('release_timing_scope') != 'after_initial_admission':
        result.update(historical_release_including_admission_s=result.get('release_wall_s'),
                      release_wall_s=None, release_timing_scope='historical_mixed_scope',
                      native_gate_wall_s=result.get('native_gate_wall_s', (result.get('gate') or {}).get('wall_s')))
    return result


def lookup(instance_sha256, canonical_rules, *, store_dir=DEFAULT_STORE):
    """Return a freshly regated stored candidate, or None. No live search."""
    try:
        key = entry_key(instance_sha256, canonical_rules)
        catalogue = _catalogue(store_dir)
        row = _read(key, store_dir, catalogue=catalogue)
        if row is None or not _binding_source_matches(row) or row.get('instance_sha256') != instance_sha256 or \
                canonical_rule_set(row.get('canonical_rules', [])) != canonical_rule_set(canonical_rules):
            return None
        token = _current_token(row)
        if token is None:
            return None
        cache_key = str(Path(store_dir).resolve()), key, catalogue.sha256, token
        with _STATE_LOCK:
            if not _snapshot_current(store_dir, catalogue):
                return None
            fresh = cache_key not in _gated
            result = deepcopy(_gated.get(cache_key))
        if fresh:
            result = _regate_entry(row)
            if _current_token(row) != token:
                return None
            with _STATE_LOCK:
                if not _snapshot_current(store_dir, catalogue):
                    return None
                _gated[cache_key] = deepcopy(result)
        if result is not None:
            # The runner compares its ordered confirmed readings exactly. The
            # lattice key still treats this as a set, independent of word order.
            result['canonical_rules'] = _rule_list(canonical_rules)
            if compatibility := binding_compatibility(row):
                result['binding_compatibility'] = compatibility
            result = _lookup_label(result, fresh=fresh)
        with _STATE_LOCK:
            if not _snapshot_current(store_dir, catalogue):
                return None
            if result is not None:
                digest = sha256_json(result)
                location = str(Path(store_dir).resolve())
                _deliveries[digest] = (location, key, catalogue, token)
                _deliveries.move_to_end(digest)
                while len(_deliveries) > 128:
                    _deliveries.popitem(last=False)
                _DELIVERY.last = (location, key, sha256_json(_rule_list(canonical_rules)), digest)
            return result
    except Exception:
        return None


def candidate_sha256(instance_sha256, canonical_rules, *, store_dir=DEFAULT_STORE, candidate=None):
    """Digest passed explicitly to runner.run_packet(stored_sha256=...).

    Pin catalogue_sha256 separately at release. A catalogue from an untrusted
    input is never an independent authentication source.
    """
    try:
        key = entry_key(instance_sha256, canonical_rules)
        location = str(Path(store_dir).resolve())
        ordered = sha256_json(_rule_list(canonical_rules))
        if candidate is not None:
            if candidate.get('instance_sha256') != instance_sha256 or candidate.get('canonical_rules') != _rule_list(canonical_rules):
                return None
            digest = sha256_json(candidate)
        else:
            previous = getattr(_DELIVERY, 'last', None)
            digest = previous[3] if previous and previous[:3] == (location, key, ordered) else None
        with _STATE_LOCK:
            receipt = _deliveries.get(digest)
        if receipt is None:
            if candidate is not None:
                return None
            returned = lookup(instance_sha256, canonical_rules, store_dir=store_dir)
            return sha256_json(returned) if returned is not None else None
        origin, origin_key, snapshot, token = receipt
        if origin != location or origin_key != key:
            return None
        row = _read(key, store_dir, catalogue=snapshot)
        if not row or not _binding_source_matches(row) or _current_token(row) != token:
            return None
        with _STATE_LOCK:
            return digest if _snapshot_current(store_dir, snapshot) else None
    except Exception:
        return None


def _capacity_proof(reason, scope):
    proof = dict(proven_impossible=True, reason=reason, scope=scope)
    match = re.search(r'needs (\d+(?:\.\d+)?) TEU of cells but only (\d+) remain open', reason)
    if match:
        demand, available = float(match[1]), int(match[2])
        proof.update(demand_cells=demand, open_cells=available,
                     equation=f'{demand:g} needed > {available} open')
    return proof


def _refusal_proof(row):
    from stowage.model import Instance
    from stowage.frozen import active_stubs, require_clean
    from experiments.mvp.reader.schema import context_from_instance, validate_answer, OnePortPerHatch
    from experiments.mvp.reader.refusal_evidence import build_static_proof
    from experiments.mvp.pipeline.binding.adapter import Intersection, compile_constraint
    from experiments.mvp.reader.constraints import InfeasibleInstruction
    from experiments.mvp.reader.select_binding import per_box_slot_screen
    from experiments.mvp.pipeline.binding.admission import wait_for_capacity
    from .runtime import isolated_binding
    instance = Instance.model_validate(row['instance'])
    require_clean(instance, what='lattice refusal proof')
    if active_stubs():
        return None
    wait_for_capacity()
    context = context_from_instance(instance)
    answers = [validate_answer(json.dumps(rule), context) for rule in row['canonical_rules']]
    with isolated_binding():
        if len(answers) == 1 and not isinstance(answers[0], OnePortPerHatch):
            proof = build_static_proof(instance, answers[0])
            return proof if proof['proven_impossible'] else None
        parts = []
        try:
            for answer in sorted(answers, key=lambda a: isinstance(a, OnePortPerHatch)):
                part, _ = compile_constraint(instance, answer, base=Intersection(instance, parts))
                parts.append(part)
            combined = Intersection(instance, parts)
            if reason := combined.capacity_shortfall():
                return _capacity_proof(reason, 'necessary combined cell-capacity screen')
            if no_slots := per_box_slot_screen(instance, combined):
                return dict(proven_impossible=True, nofit_ids=no_slots, scope='necessary allowed-footprint screen')
        except InfeasibleInstruction as error:
            reason = str(error)
            # UNKNOWN/timeout/unavailability is not a mathematical refusal.
            if 'INFEASIBLE' in reason or 'needs ' in reason and 'remain open' in reason:
                return _capacity_proof(reason, 'necessary instruction-completion screen')
    return None


def refusal_lookup(instance_sha256, canonical_rules, *, store_dir=DEFAULT_STORE):
    """Authenticated stored refusal, with its necessary-condition proof rerun."""
    from .gates import engine_identity
    try:
        key = entry_key(instance_sha256, canonical_rules)
        catalogue = _catalogue(store_dir)
        row = _read(key, store_dir, catalogue=catalogue)
        if not row or row.get('status') != 'proved_refusal' or row.get('instance_sha256') != instance_sha256 \
                or not _binding_source_matches(row) \
                or canonical_rule_set(row.get('canonical_rules', [])) != canonical_rule_set(canonical_rules) \
                or not row.get('instance') or sha256_json(row['instance']) != instance_sha256:
            return None
        identity = engine_identity()
        if not identity['passed'] or row.get('engine_version') != identity['engine_version'] or \
                row.get('engine_manifest_sha256') != identity['engine_manifest_sha256']:
            return None
        proof = _refusal_proof(row)
        if proof is None or not _snapshot_current(store_dir, catalogue):
            return None
        return dict(row, proof=proof, canonical_rules=_rule_list(canonical_rules), mode='stored',
                    label='stored', checker=None, planning_permitted=False, proof_rechecked=True)
    except Exception:
        return None


def _invalidate_gate_cache(store_dir, key=None):
    location = str(Path(store_dir).resolve())
    with _STATE_LOCK:
        for cached in list(_gated):
            if cached[0] == location and (key is None or cached[1] == key):
                _gated.pop(cached, None)


def regate(instance_sha256=None, *, store_dir=DEFAULT_STORE, expected_catalogue_sha256=None):
    """Demo launch hook: refresh/pin catalogue, full-check every stored success."""
    summary = dict(passed=[], rejected=[], skipped=[], errors={}, mode='stored')
    with _STATE_LOCK:
        try:
            catalogue = _catalogue(store_dir, refresh=True, expected_sha256=expected_catalogue_sha256)
        except (OSError, ValueError, TypeError) as error:
            # A rejected launch snapshot must not retain the previous trust anchor.
            _pins[_catalogue_path(store_dir)] = None
            _invalidate_gate_cache(store_dir)
            summary['errors']['catalogue'] = str(error)
            return summary
    summary['catalogue_sha256'] = catalogue.sha256
    for key in sorted(catalogue):
        try:
            with _STATE_LOCK:
                if not _snapshot_current(store_dir, catalogue):
                    raise ValueError('launch catalogue snapshot was superseded; retry launch regating')
            row = _read(key, store_dir, catalogue=catalogue)
            if row is None:
                with _STATE_LOCK:
                    if _snapshot_current(store_dir, catalogue):
                        _invalidate_gate_cache(store_dir, key)
                summary['rejected'].append(key)
                continue
            if instance_sha256 is not None and row.get('instance_sha256') != instance_sha256:
                continue
            # Invalidate before native work: an exception must not resurrect a
            # successful gate from a previous launch at the same source epoch.
            with _STATE_LOCK:
                if not _snapshot_current(store_dir, catalogue):
                    raise ValueError('launch catalogue snapshot was superseded; retry launch regating')
                _invalidate_gate_cache(store_dir, key)
            if row.get('status', 'checked_plan') != 'checked_plan':
                summary['skipped'].append(key)
                continue
            token = _current_token(row)
            result = _regate_entry(row) if token is not None else None
            if token is not None and _current_token(row) != token:
                result = None
            with _STATE_LOCK:
                if not _snapshot_current(store_dir, catalogue):
                    raise ValueError('launch catalogue snapshot was superseded; retry launch regating')
                if token is not None:
                    _gated[str(Path(store_dir).resolve()), key, catalogue.sha256, token] = result
            summary['passed' if result is not None else 'rejected'].append(key)
        except Exception as error:
            with _STATE_LOCK:
                if _snapshot_current(store_dir, catalogue):
                    _invalidate_gate_cache(store_dir, key)
            summary['errors'][key] = f'{type(error).__name__}: {error}'
    with _STATE_LOCK:
        if not _snapshot_current(store_dir, catalogue):
            summary['errors']['catalogue'] = 'launch catalogue snapshot was superseded; retry launch regating'
            summary['passed'] = []
    return summary

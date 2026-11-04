"""Read planner artifacts without trusting a saved safety verdict.

Evaluation run layouts are not frozen yet. Discover versioned Instance,
MovePlan and PlannerResult JSON artifacts by their data contracts, rather than
guessing which planner produced them. A fresh full checker run owns the verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import os
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any

from stowage.io import load_instance, load_plan, load_result
from stowage.model import Instance, MovePlan
from app.identity import instance_binding_matches, instance_content_digest, record_file_digest, record_instance_digest

INSTANCE_ROOT = Path(__file__).resolve().parents[1] / "experiments/instances-v2"


def _retain_adapter_taint(metadata):
    """Adapter lineage participates in the canonical result's shipping gate."""
    model = metadata.get('model_run') or {}
    diagnostic = (model.get('adapter_clean') is False or bool(model.get('adapter_stubs')) or
                  model.get('label') == 'StowLM (diagnostic adapter, not evidence)')
    if not diagnostic or not metadata.get('planner_result'):
        return
    from stowage.model import PlannerResult, Provenance
    result = PlannerResult.model_validate(metadata['planner_result'])
    stubs = model.get('adapter_stubs') or {'stowlm.adapter': 'Diagnostic adapter, not evidence'}
    result = result.model_copy(update={'provenance': result.provenance.merge(Provenance(stubs=stubs))})
    metadata['planner_result'] = result.model_dump(mode='json')
    metadata['provenance'] = result.provenance.model_dump(mode='json')


@dataclass
class LoadedInputs:
    instance: Instance
    plan: MovePlan | None
    metadata: dict[str, Any] = field(default_factory=dict)


def _json(path: Path) -> dict:
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


# Run-local numerical/physics cache mirrors are never plan, instance or config artifacts. Start-up fix (30 Oct,
# mvp-ui/startup): showcase-v2-twostep/physics-numerical-cache/cache/linear/vessel alone holds ~81k JSON files; parsing
# and holding them all as candidate records cost ~85-200 s and a 16.7 GB RSS peak per rival load (measured).
_SKIP_DIRS = frozenset({"physics-numerical-cache", "timeline-cache", "__pycache__"})


def _files(folder: Path) -> list[Path]:
    found = set()
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        found.update(Path(root) / n for n in names if n.endswith((".json", ".json.gz")))
    return sorted(found)


def _plan_payload(data: dict) -> dict:
    for key in ("result", "planner_result"):
        if isinstance(data.get(key), dict):
            return data[key]
    return data


def _is_harness_record(data: dict) -> bool:
    """src/eval harness per-instance record: identity + outcome, no plan (it names the result by instance id)."""
    return (isinstance(data, dict) and isinstance(data.get('instance_id'), str) and 'input_sha256' in data
            and 'status' in data and 'config_sha256' in data and 'plan' not in data
            and re.fullmatch(r'[A-Za-z0-9._-]+', data['instance_id']) is not None)


def _is_plan(data: dict) -> bool:
    value = _plan_payload(data)
    return ("moves" in value and "instance_id" in value) or (
        "planner" in value and "status" in value)


def instance_roots(root: Path | None = None) -> tuple[Path, ...]:
    """Current fix-wave/simulator and v3 corpora plus readable historical v2.

    An explicit root retains the original single-corpus lookup API. Deriving
    siblings from INSTANCE_ROOT also keeps isolated callers/tests isolated.
    """
    if root is not None:
        return (Path(root),)
    parent = Path(INSTANCE_ROOT).parent
    # experiments/mvp/corpus is the current MVP corpus (29 Oct); run folders under experiments/mvp/runs bind to it.
    return (parent / 'mvp/corpus', parent / 'instances-sim/fix-wave', parent / 'instances-sim',
            parent / 'instances-v3', Path(INSTANCE_ROOT))


def _instance_from_dict(data: dict) -> Instance:
    with tempfile.TemporaryDirectory(prefix='stowlm-ui-instance-') as directory:
        path = Path(directory) / 'instance.json'
        path.write_text(json.dumps(data))
        return load_instance(path)


def _full_instance(data: object) -> bool:
    return isinstance(data, dict) and 'vessel' in data and 'load_list' in data


def _corpus_candidates(instance_id, root=None, *, file_digest=None, record=None, binding_digest=None):
    visited = set()
    summary = (record or {}).get('instance') or {}
    family = summary.get('family') or {} if isinstance(summary, dict) else {}
    seed = family.get('seed')
    if seed is None:
        match = re.search(r'\.(\d+)$', instance_id)
        seed = match.group(1) if match else None
    hints = {f'seed-{int(seed):08d}.json', f'seed-{int(seed)}.json'} if str(seed).isdigit() else set()
    # MVP corpus ids embed the seed mid-id (random.small.40100007.0.5 -> seed-040100007.json); ordering hint only.
    for long_seed in re.findall(r'(?:^|\.)(\d{5,})(?=\.|$)', instance_id):
        hints |= {f'seed-{int(long_seed):09d}.json', f'seed-{int(long_seed):08d}.json', f'seed-{int(long_seed)}.json'}
    paths = [(index, path) for index, folder in enumerate(instance_roots(root))
             if folder.is_dir() for path in _files(folder)]
    # Startup fix (29 Oct, mvp-ui/stowlm-run): a run record that names its input file (eval-harness ``input`` or the
    # config-borrowed ``input_path``) is tried first. Only the search ORDER changes: the same SHA-256 + id + content
    # gates below still decide. Without it, a wrong seed hint hashed the whole corpus (~8.5k files, 36 s measured).
    repo = Path(__file__).resolve().parents[1]
    for key in ('input_path', 'input'):
        hint = (record or {}).get(key)
        if isinstance(hint, str) and hint:
            candidate = Path(hint) if Path(hint).is_absolute() else repo / hint
            if candidate.is_file():
                paths.insert(0, (-1, candidate))
    # Seed names only order the search. Every match still needs complete content
    # identity, including across current and historical corpus roots.
    paths.sort(key=lambda item: (item[0] != -1, item[1].name not in hints, item[0], str(item[1])))
    for _, path in paths:
        resolved = path.resolve()
        if resolved in visited or path.name.endswith(('.audit.json', '.report.json', '.plan.json')):
            continue
        visited.add(resolved)
        try:
            # Eval checksums describe original serialized bytes, not the
            # normalized current model. Check them before normalization.
            raw = path.read_bytes()
            if file_digest and hashlib.sha256(raw).hexdigest() != file_digest:
                continue
            decoded = gzip.decompress(raw) if path.suffix == '.gz' else raw
            data = json.loads(decoded)
            if _full_instance(data) and data.get('id') == instance_id:
                instance = _instance_from_dict(data)
                if binding_digest is None or instance_binding_matches(instance, binding_digest, data):
                    yield path, instance
        except (ValueError, OSError, EOFError):
            continue


def find_instance_path(instance_id: str, *, root: Path | None = None,
                       record: dict | None = None) -> Path | None:
    """Resolve a corpus world by verified complete content, never a unique name.

    Legacy summary-only artifacts need an explicit operator-selected instance.
    Saved checker verdicts are ignored; their content-binding hashes may locate
    inputs, but a fresh full check still owns safety approval.
    """
    record = record or {}
    digest, checksum = record_instance_digest(record), record_file_digest(record)
    if digest is None and checksum is None:
        raise ValueError('Instance resolution has no content binding; supply --instance')
    for path, instance in _corpus_candidates(instance_id, root, file_digest=checksum,
                                            record=record, binding_digest=digest):
        return path  # Verified SHA-256 content, not first matching name.
    return None


def _resolve_instance(plan, selected_record, records, *, instance_path, selected_instance, witness_paths=None):
    witness_paths = witness_paths if witness_paths is not None else []
    if selected_record.get('instance_id', plan.instance_id) != plan.instance_id:
        raise ValueError('Selected record instance_id conflicts with its plan')
    digest = record_instance_digest(selected_record)
    checksum = record_file_digest(selected_record)
    embedded_raw = selected_record.get('instance')
    embedded = _instance_from_dict(embedded_raw) if _full_instance(embedded_raw) else None
    explicit = load_instance(instance_path) if instance_path else selected_instance
    if instance_path and selected_instance is not None and instance_content_digest(explicit) != instance_content_digest(selected_instance):
        raise ValueError('Explicit instance conflicts with the selected instance content')
    if embedded is not None and explicit is not None and instance_content_digest(embedded) != instance_content_digest(explicit):
        raise ValueError('Explicit instance conflicts with the selected embedded world')
    chosen = embedded if embedded is not None else explicit
    if chosen is not None and chosen.id != plan.instance_id:
        raise ValueError('Plan instance_id must match the selected instance')
    if chosen is not None and digest is not None:
        sources = [embedded_raw] if embedded is not None else []
        if instance_path:
            sources.append(_json(Path(instance_path)))
        if not (instance_binding_matches(chosen, digest) or
                any(instance_binding_matches(chosen, digest, raw) for raw in sources)):
            raise ValueError('Selected instance conflicts with the bound instance content')
    if instance_path and checksum is not None and hashlib.sha256(Path(instance_path).read_bytes()).hexdigest() != checksum:
        raise ValueError('Explicit instance original input_sha256 checksum mismatch')
    local = []
    for path, data in records:
        raw = data.get('instance') if _full_instance(data.get('instance')) else data
        if _full_instance(raw) and raw.get('id') == plan.instance_id:
            candidate = _instance_from_dict(raw)
            raw_checksum = hashlib.sha256(path.read_bytes()).hexdigest() if raw is data else None
            local.append((path, candidate, raw_checksum, raw))
    if embedded is not None and digest is None and checksum is None:
        if any(instance_content_digest(world) != instance_content_digest(embedded) for _, world, _, _ in local):
            raise ValueError('Conflicting same-ID embedded instance contents; a verified content binding is required')
    if checksum is not None and not instance_path:
        witnesses = [(path, world) for path, world, raw_sha, raw in local if raw_sha == checksum
                     and (digest is None or instance_binding_matches(world, digest, raw))]
        witnesses = [(path, world) for path, world in witnesses
                     if chosen is None or instance_content_digest(world) == instance_content_digest(chosen)]
        if not witnesses:
            witness = next(((path, world) for path, world in _corpus_candidates(
                plan.instance_id, file_digest=checksum, record=selected_record, binding_digest=digest)
                if chosen is None or instance_content_digest(world) == instance_content_digest(chosen)), None)
            witnesses = [witness] if witness else []
        if not witnesses:
            raise ValueError('No instance matches the original input_sha256 checksum and selected content; supply --instance')
        chosen = chosen if chosen is not None else witnesses[0][1]
        witness_paths.append(witnesses[0][0])
    if chosen is None and digest is not None:
        matching = [(path, world) for path, world, _, raw in local if instance_binding_matches(world, digest, raw)]
        if matching:
            path, chosen = matching[0]
            witness_paths.append(path)
        else:
            path = find_instance_path(plan.instance_id, record=selected_record)
            if path is not None:
                chosen = load_instance(path)
                witness_paths.append(path)
    if chosen is None:
        raise ValueError(f'Instance {plan.instance_id!r} has no verified content binding; supply --instance')
    return chosen


def _sibling_binding(selected: Path, record: dict, instance_id: str) -> dict:
    """Borrow the content binding of an unbound ``result.json`` from its run's sibling ``record.json``.

    MVP run folders (w2 run contract) store the PlannerResult and the per-instance record side by side; only the
    record carries ``input_sha256`` (the corpus file's SHA-256). The borrowed value is a *locator*: the instance is
    still accepted only when a file with exactly those bytes and that id is found.
    """
    if record_instance_digest(record) is not None or record_file_digest(record) is not None:
        return record
    sibling = selected.with_name('record.json')
    if sibling == selected or not sibling.is_file():
        return _config_binding(selected, record, instance_id)
    try:
        data = _json(sibling)
    except (ValueError, OSError, EOFError):
        return record
    if not isinstance(data, dict) or data.get('instance_id') != instance_id or record_file_digest(data) is None:
        return record
    return {**record, 'input_sha256': data['input_sha256'], 'instance_id': instance_id}


def _config_binding(selected: Path, record: dict, instance_id: str) -> dict:
    """Borrow ``input_sha256`` from the run root's ``config.json`` ``inputs`` list (eval-c1 loose runs, e.g.
    ``runs/base-mask/tiny``), which has no per-instance ``record.json``.

    Only an input whose file still has exactly the declared SHA-256 and the plan's instance id is used, and the
    value is again only a locator: ``_resolve_instance`` accepts an instance only from a file with those bytes.
    """
    config = selected.parent.parent.parent / 'config.json'
    if selected.name != 'result.json' or selected.parent.parent.name != 'instances' or not config.is_file():
        return record
    try:
        inputs = json.loads(config.read_text()).get('inputs') or []
    except (ValueError, OSError, AttributeError):
        return record
    repo = Path(__file__).resolve().parents[1]
    for item in inputs if isinstance(inputs, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not isinstance(item.get('sha256'), str):
            continue
        path = (repo / item['path']).resolve()
        if not path.is_relative_to(repo) or not path.is_file():
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            continue
        try:
            if json.loads(raw).get('id') != instance_id:
                continue
        except (ValueError, AttributeError):
            continue
        return {**record, 'input_sha256': item['sha256'], 'instance_id': instance_id, 'input_path': item['path']}
    return record


def _read_plan(path: Path) -> tuple[MovePlan, dict]:
    original = _json(path)
    value = _plan_payload(original)
    # The model-run layout keeps the raw result alongside report/input identity
    # evidence. Only these envelope keys are separated; unknown result fields
    # still reach the canonical validator and are rejected.
    envelope_keys = {'instance', 'instance_id', 'input_sha256', 'report', 'check_report'}
    if 'planner' in value and value is original and envelope_keys.intersection(value):
        value = {key: item for key, item in value.items() if key not in envelope_keys}
    if "planner" in value and value.get("plan") is None:
        raise ValueError(f"Planner result has no plan ({value.get('status', 'unknown')}): {path}")
    # Embedded harness artifacts still go through the canonical stowage.io
    # validators. PlannerResult itself has no schema field in the core model;
    # its I/O envelope inherits the embedded plan's declared schema version.
    if value is not original or ("planner" in value and "schema_version" not in value):
        value = dict(value)
        if "planner" in value:
            value.setdefault("schema_version", original.get("schema_version") or
                             (value.get("plan") or {}).get("schema_version"))
        with tempfile.TemporaryDirectory(prefix="stowlm-ui-input-") as directory:
            normalized = Path(directory) / "artifact.json"
            normalized.write_text(json.dumps(value))
            plan, metadata = _read_plan(normalized)
        return plan, {**metadata, "source_path": str(path)}
    if "moves" in value:
        return load_plan(path), {"source_path": str(path), "source_kind": "MovePlan"}
    result = load_result(path)
    if result.plan is None:
        raise ValueError(f"Planner result has no plan ({result.status}): {path}")
    # A clean embedded plan does not erase planner/adapter or later-plan taint.
    # Keep the complete result too: status and required later calls participate
    # in PlannerResult.shippable after a fresh full check.
    plan = result.plan.model_copy(update={
        "provenance": result.plan.provenance.merge_nested(result),
    })
    result = result.model_copy(update={"plan": plan})
    return plan, {"source_path": str(path), "source_kind": "PlannerResult",
                         "planner": result.planner, "planner_version": result.planner_version,
                         "planner_status": result.status,
                         "planner_result": result.model_dump(mode="json"),
                         "provenance": result.provenance.model_dump(mode="json"),
                         "label": f"{result.planner} · {result.planner_version}"}


def _failed_model_inputs(folder, selected, record, *, instance_path, selected_instance):
    """Import actual failed model output without manufacturing a replay plan.

    Config paths are lookup hints only. Original SHA-256 bytes and normalized
    complete-world equality determine identity; a reused instance name cannot.
    """
    from app.model_runs import manifest_folder, model_run_metadata
    from stowage.model import PlannerResult
    root = manifest_folder(selected, folder)
    if root is None:
        raise ValueError('Planner result has no plan and no model run manifest')
    config_path = root / 'config.json'
    config = _json(config_path) if config_path.is_file() else {}
    entries = config.get('inputs', [])
    if not isinstance(entries, list) or any(not isinstance(item, dict) or
            not isinstance(item.get('path'), str) or not re.fullmatch(r'[0-9a-f]{64}', str(item.get('sha256', '')))
            for item in entries):
        raise ValueError('Model run config input SHA256 metadata is malformed')
    value = _plan_payload(record)
    clean = {key: item for key, item in value.items()
             if key not in {'schema_version', 'instance', 'instance_id', 'input_sha256', 'report', 'check_report'}}
    result = PlannerResult.model_validate(clean)
    if result.plan is not None or result.status == 'ok':
        raise ValueError('No-plan model diagnostic requires a failed planner result')
    target_id = selected.parent.name
    explicit = load_instance(instance_path) if instance_path else selected_instance
    if explicit is not None and explicit.id != target_id:
        raise ValueError('Explicit instance conflicts with the model result instance directory')
    if instance_path and selected_instance is not None and instance_content_digest(explicit) != instance_content_digest(selected_instance):
        raise ValueError('Explicit instance conflicts with the selected instance content')
    matches = []
    for item in entries:
        hint = Path(item['path'])
        paths = [hint] if hint.is_absolute() else [Path(__file__).resolve().parents[1] / hint, root / hint]
        if instance_path:
            paths.insert(0, Path(instance_path))
        for path in dict.fromkeys(paths):
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                continue
            candidate = load_instance(path)
            if candidate.id == target_id:
                matches.append((candidate, path, item['sha256']))
    unique = {instance_content_digest(world) for world, _, _ in matches}
    if len(unique) > 1:
        raise ValueError('Ambiguous model run input contents share the same instance id')
    if explicit is not None and matches and instance_content_digest(explicit) not in unique:
        raise ValueError('Explicit instance content conflicts with model run input SHA256')
    if entries and not matches:
        raise ValueError('No original instance matches the model run config input SHA256 checksum')
    if explicit is None and not matches:
        raise ValueError('Failed model run has no verified instance content; supply --instance')
    instance = explicit if explicit is not None else matches[0][0]
    binding_record = dict(record)
    declared_checksum = record_file_digest(record)
    if declared_checksum is not None:
        witnesses = [path for _, path, _ in matches]
        if instance_path:
            witnesses.append(Path(instance_path))
        if not any(hashlib.sha256(path.read_bytes()).hexdigest() == declared_checksum for path in witnesses):
            raise ValueError('Model result input_sha256 has no original file checksum witness')
    if matches:
        checksum = matches[0][2]
        if record.get('input_sha256') not in {None, checksum}:
            raise ValueError('Result input_sha256 conflicts with config input SHA256')
        binding_record['input_sha256'] = checksum
    digest = record_instance_digest(record)
    raw = _json(Path(instance_path)) if instance_path else (_json(matches[0][1]) if matches else None)
    if digest and not instance_binding_matches(instance, digest, raw):
        raise ValueError('Selected instance conflicts with the bound instance content')
    metadata = {'source_path': str(selected), 'source_kind': 'PlannerResult',
        'planner': result.planner, 'planner_version': result.planner_version,
        'planner_status': result.status, 'planner_result': result.model_dump(mode='json'),
        'provenance': result.provenance.model_dump(mode='json'), 'run_folder': str(folder),
        'instance_content_sha256': instance_content_digest(instance)}
    model_metadata = model_run_metadata(folder, selected, binding_record, instance)
    if 'model_run' not in model_metadata:
        raise ValueError('Failed result has no Glimmer model run manifest')
    metadata.update(model_metadata)
    _retain_adapter_taint(metadata)
    witnesses = {selected, *(path for _, path, _ in matches)}
    if config_path.is_file():
        witnesses.add(config_path)
    if instance_path:
        witnesses.add(Path(instance_path))
    metadata['source_files'] = [*metadata.get('source_files', []), *[
        {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(witnesses)]]
    return LoadedInputs(instance, None, metadata)


def load_inputs(*, instance_path: str | Path | None = None,
                plan_path: str | Path | None = None,
                run_folder: str | Path | None = None,
                entry: str | Path | None = None,
                selected_instance: Instance | None = None) -> LoadedInputs:
    """Load CLI-selected artifacts; ambiguous runs require an explicit entry.

    ``entry`` is a run-relative plan/result path. A run may contain nested
    results and instances, or content-bound references in current/historical
    corpora. Explicit instances declare a legacy plan's world but cannot override
    selected embedded content or verified hashes. Saved verdicts are ignored.
    """
    from app.backend import demo_instance, make_demo_plan

    folder = Path(run_folder).resolve() if run_folder is not None else None
    if folder is not None and not folder.is_dir():
        raise ValueError(f"Run folder does not exist: {folder}")
    if entry is not None and folder is None:
        raise ValueError("--entry requires --run-folder")
    if plan_path is not None and entry is not None:
        raise ValueError("Choose --plan or --entry, not both")
    records: list[tuple[Path, dict]] = []
    if folder:
        for path in _files(folder):
            try:
                data = _json(path)
            except (ValueError, OSError, EOFError):
                continue  # Unrelated metrics/log files are not input artifacts.
            records.append((path, data))
    selected = Path(plan_path) if plan_path is not None else None
    if entry is not None:
        selected = (folder / entry).resolve()
        if not selected.is_relative_to(folder):
            raise ValueError("--entry must stay inside --run-folder")
    if folder and selected is None:
        candidates = [(path, data) for path, data in records if _is_plan(data)]
        # Prefer the richer result when the run also stores its identical plan.
        results = [(path, data) for path, data in candidates if "planner" in _plan_payload(data)]
        candidates = results or candidates
        if not candidates:
            raise ValueError(f"No MovePlan or PlannerResult artifacts found in {folder}")
        if len(candidates) > 1:
            choices = ", ".join(str(path.relative_to(folder)) for path, _ in candidates[:12])
            raise ValueError(f"Run contains multiple plans; select --entry: {choices}")
        selected = candidates[0][0]
    if selected is None and instance_path is None and selected_instance is None and folder is None:
        from app.backend import default_inputs
        default_instance, default_plan = default_inputs()
        if default_instance is not None:
            # Same identity gates as an explicit --instance/--plan selection.
            return load_inputs(instance_path=default_instance, plan_path=default_plan)
    if selected is None:
        instance = load_instance(instance_path) if instance_path else selected_instance or demo_instance()
        sources = [{'path': str(Path(instance_path).resolve()),
                    'sha256': hashlib.sha256(Path(instance_path).read_bytes()).hexdigest()}] if instance_path else []
        return LoadedInputs(instance, make_demo_plan(instance), {'source_files': sources})
    selected_record = _json(selected)
    value = _plan_payload(selected_record)
    if folder and 'planner' not in value and _is_harness_record(selected_record):
        # Eval-harness per-instance record (``instances/<spec_key>.json``): open its run-contract result instead.
        selected = (folder / 'instances' / selected_record['instance_id'] / 'result.json').resolve()
        if not selected.is_relative_to(folder) or not selected.is_file():
            raise ValueError(f"Harness record {selected_record['instance_id']!r} has no instances/<id>/result.json")
        selected_record = _json(selected)
        value = _plan_payload(selected_record)
    partial = None
    if (folder and 'planner' in value and value.get('plan') is not None and value.get('status') == 'dead_end'
            and value is selected_record):
        from app.model_runs import manifest_folder
        if manifest_folder(selected, folder) is not None:
            # A model dead end keeps the decoded prefix as ``plan``; it is not a plan and is never replayed as one
            # (stage dead-end card, app/stage_stowlm.py). Only the number of dropped prefix moves is retained.
            partial = len((value.get('plan') or {}).get('moves') or [])
            selected_record = {**selected_record, 'plan': None, 'later_plans': []}
            value = selected_record
    if folder and 'planner' in value and value.get('plan') is None:
        loaded = _failed_model_inputs(folder, selected, selected_record,
            instance_path=instance_path, selected_instance=selected_instance)
        if partial is not None and loaded.metadata.get('model_run') is not None:
            loaded.metadata['model_run']['dead_end_partial_moves'] = partial
        return loaded
    plan, metadata = _read_plan(selected)
    if not any(path.resolve() == selected.resolve() for path, _ in records):
        records.append((selected, _json(selected)))
    selected_record = _sibling_binding(selected, _json(selected), plan.instance_id)
    witness_paths = [Path(instance_path)] if instance_path else []
    instance = _resolve_instance(plan, selected_record, records,
        instance_path=instance_path, selected_instance=selected_instance, witness_paths=witness_paths)
    from app.model_runs import model_run_metadata
    metadata.update(model_run_metadata(folder or selected.parent, selected, selected_record, instance))
    _retain_adapter_taint(metadata)
    metadata['source_files'] = [*metadata.get('source_files', []), *[
        {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(set(witness_paths))]]
    metadata['instance_content_sha256'] = instance_content_digest(instance)
    if plan.instance_id != instance.id or plan.call_index != instance.call_index:
        raise ValueError("Plan instance_id and call_index must match the selected instance")
    if folder:
        metadata["run_folder"] = str(folder)
    return LoadedInputs(instance, plan, metadata)

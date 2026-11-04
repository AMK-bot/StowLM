"""Offline demo storyboard and bounded background planning.

The fixed planner pipeline owns finalization, repair and safety approval (see
``stowage.planner.run_planner``). This service only schedules that pipeline and
materializes engine replay; elapsed time and named stages are real observations,
never estimates of search completion. No remote model or paid service is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import threading
from time import monotonic
from uuid import uuid4

from app import check_cache
from app.backend import build_replay, withdraw_approval
from app.inputs import LoadedInputs, _files, _is_plan, _json, _plan_payload, instance_roots, load_inputs
from app.v6_fixtures import Withheld
from app.identity import instance_binding_matches, instance_content_digest, record_instance_digest, record_file_digest
from stowage.io import load_instance
from stowage.model import Instance, PlannerResult
from stowage.planner import run_planner

ROOT = Path(__file__).resolve().parents[1]
SOURCE_KEYS = {"planner", "planner_version", "planner_status", "label", "source_path", "source_kind", "run_folder", "model_run", "storyboard_selection"}
PLANNERS = [{"id": "solver.scalable", "label": "Scalable solver", "live": True},
            {"id": "solver.exact", "label": "Exact solver", "live": True},
            {"id": "baseline.twostep", "label": "Two-step baseline", "live": True},
            {"id": "stowlm", "label": "StowLM · imported plans", "live": False}]


class ConflictError(ValueError):
    """A bounded live job already occupies the demo's worker."""


# D79 (leader decision, 29 Oct 2026): the MVP runs with C2 (the feasibility engine's global completion screen) OFF
# for the mask and the scalable solver. C2 is only an early screen; the full checker remains the acceptance gate, so
# no limit changes. APPROXIMATION: the two-step baseline uses the same feasibility engine and gets the same C2-off
# setting so the baseline and solver rows are built on equal terms. The exact solver keeps its default (G1-verified)
# configuration because its tiny instances do not need the speed-up.
MVP_FEASIBILITY_EXTRA = {"global_screen": 0}


def mvp_feasibility_config():
    """``FeasibilityConfig`` for live MVP solves (D79: C2 off)."""
    from stowage.feasibility import FeasibilityConfig
    return FeasibilityConfig(extra=dict(MVP_FEASIBILITY_EXTRA))


def _make_planner(planner_id):
    if planner_id == "solver.scalable":
        from stowage.solver.scalable import ScalableConfig, ScalableSolver
        return ScalableSolver(ScalableConfig(feasibility_config=mvp_feasibility_config()))
    if planner_id == "baseline.twostep":
        from stowage.solver.twostep import TwoStepPlanner
        return TwoStepPlanner(feasibility_config=mvp_feasibility_config())
    if planner_id == "solver.exact":
        from stowage.solver.exact import ExactPlanner
        return ExactPlanner()
    raise ValueError("Live solve supports scalable/exact solvers and the two-step baseline only")


@dataclass
class _Record:
    inputs: LoadedInputs
    replay: dict | None = None
    instance_digest: str = ''
    plan_digest: str = ''


def _plan_digest(plan):
    if plan is None:
        return 'no-plan'
    return hashlib.sha256(json.dumps(plan.model_dump(mode='json'),sort_keys=True,separators=(',', ':')).encode()).hexdigest()


def _source_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_source_files_unchanged(metadata):
    """Consumer-verified run files stay bound to every subsequent replay."""
    sources = metadata.get('source_files', [])
    if not isinstance(sources, list):
        raise ValueError('Malformed artifact source fingerprints')
    for source in sources:
        if (not isinstance(source, dict) or not isinstance(source.get('path'), str) or
                not isinstance(source.get('sha256'), str) or
                re.fullmatch(r'[0-9a-f]{64}', source['sha256']) is None):
            raise ValueError('Malformed artifact source fingerprint')
        path = Path(source['path'])
        if not path.is_file() or _source_digest(path) != source['sha256']:
            raise ValueError(f'Artifact source changed after import: {path}')


def _run_name(metadata) -> str | None:
    """Run folder of a registered plan, relative to experiments/mvp/runs when inside it (video shot selectors)."""
    folder = metadata.get("run_folder")
    if not folder and metadata.get("source_path"):
        parts = Path(metadata["source_path"]).parts
        folder = str(Path(*parts[:parts.index("instances")])) if "instances" in parts[1:] else None
    if not folder:
        return None
    parts = Path(folder).parts
    for i in range(len(parts) - 1):
        if parts[i] == "mvp" and parts[i + 1] == "runs":
            return "/".join(parts[i + 2:]) or None
    return Path(folder).name


class DemoService:
    """One bounded worker, an allowlisted artifact catalogue, and cached replay.

    The instance picker deliberately offers a compact deterministic sample (one
    instance per family/rung). CLI-selected instances and plans are always kept.
    Filesystem paths never come from HTTP requests. A time limit is passed to the
    planner pipeline; individual checker/oracle calls are cooperative and may
    finish after it. Two-planner comparison runs serially on the single worker.
    """
    def __init__(self, instance, plan, source_metadata=None, comparison_inputs=None,
                 *, instance_root=None, run_root=ROOT / "experiments/eval-runs",
                 initial_replay=None, discover: bool = True):
        if type(discover) is not bool:
            raise ValueError('discover must be a boolean')
        self._lock = threading.RLock()
        self._records = {}
        self._plan_sources = {}
        self._instances = {}
        self._instance_digests = {}
        self._instance_source_paths = {}
        self._source_fingerprints = {}
        self._conflicts = set()
        self._artifact_bindings = {}
        self._instance_paths = {}
        self._instance_sources = {}
        self._instance_info = {}
        self._jobs = {}
        self._active_job = None
        self._builds = {}  # plan id -> lock: the threaded server asks for one plan many times, it is built once
        self.plan_gate = None  # --v6: plan id -> why it can't be shown, or None. Every read below goes through _require_shown.
        self.active_plan_id = self.register(LoadedInputs(instance, plan, source_metadata or {}), initial_replay)
        for inputs in comparison_inputs or []:
            self.register(inputs)
        if discover:
            for root in instance_roots(instance_root):
                self._discover_instances(root)
            self._discover_runs(Path(run_root))

    @staticmethod
    def _info(instance, source):
        return {"id": instance.id, "label": f"{instance.size_class} · {instance.id}",
                "size_class": instance.size_class, "source": source,
                "content_digest": instance_content_digest(instance),
                "live": instance.size_class in {"tiny", "small"}}

    def _observe_instance(self, instance, source, path=None):
        """Catalogue content identities; a reused name never chooses a world."""
        digest = instance_content_digest(instance)
        previous = self._instance_digests.get(instance.id)
        if path is not None:
            self._source_fingerprints[path] = _source_digest(path)
            self._instance_source_paths.setdefault(instance.id, set()).add(path)
        if previous is not None and previous != digest:
            self._conflicts.add(instance.id)
            self._instance_info[instance.id].update(conflict=True, live=False,
                conflict_note='Different instance contents share the same id')
            return False
        self._instance_digests[instance.id] = digest
        self._instance_info.setdefault(instance.id, self._info(instance, source))
        return True

    def _assert_instance_unchanged(self, instance_id):
        if instance_id in self._conflicts:
            raise ValueError('Conflicting instance contents share the same id; select an unambiguous source')
        instance = self._instances.get(instance_id)
        if instance is not None and instance_content_digest(instance) != self._instance_digests.get(instance_id):
            raise ValueError('Cached instance contents changed after registration')
        for path in self._instance_source_paths.get(instance_id, ()):
            if not path.is_file() or _source_digest(path) != self._source_fingerprints[path]:
                raise ValueError(f'Instance source changed after discovery: {path}')

    def _discover_instances(self, root):
        if not root.is_dir():
            return
        # Loading every fleet-scale file just to draw a picker is unnecessary.
        # Validate one representative per directory to bind its content, while
        # retaining only its path until a live job needs the full model.
        candidates = set()
        for pattern in ('*.json','*/*.json','*/*/*.json','*.json.gz','*/*.json.gz','*/*/*.json.gz'):
            candidates.update(root.glob(pattern))
        seen_groups = set()
        for path in sorted(candidates):
            try:
                name = path.name.removesuffix('.gz').removesuffix('.json')
                if name.endswith(('.audit','.plan','.report')) or name in {'manifest','screening-probe'}:
                    continue
                if path.stat().st_size > 32 * 1024 * 1024:
                    continue
                # v2/v3 use family/rung/seed-N; current simulator waves also
                # use flat rung-seed-N and vessel-rung[-seed-N] names.
                representative = re.sub(r'-seed-\d+$','',name)
                representative = re.sub(r'^seed-\d+$','seed',representative)
                group = (path.parent,representative)
                if group in seen_groups:
                    continue
                data = _json(path)
                if "vessel" not in data or "load_list" not in data:
                    continue
                instance = load_instance(path)
                seen_groups.add(group)
                if self._observe_instance(instance, str(path.relative_to(root)), path):
                    self._instance_paths.setdefault(instance.id, path)
            except (OSError, ValueError, KeyError):
                continue

    def _discover_runs(self, root):
        if not root.is_dir():
            return
        # Saved verdicts are never trusted. Read labels now and defer canonical
        # loading/replay until selection; do not scan the 1+ GB instance corpus.
        for path in _files(root):
            try:
                if path.stat().st_size > 32 * 1024 * 1024:
                    continue
                data = _json(path)
                if "vessel" in data and "load_list" in data and data.get("id"):
                    instance = load_instance(path)
                    if self._observe_instance(instance, str(path.relative_to(root)), path):
                        self._instance_paths.setdefault(instance.id, path)
                if not _is_plan(data):
                    continue
                result = _plan_payload(data)
                plan = result.get("plan") if "planner" in result else result
                if not isinstance(plan, dict) or not plan.get("instance_id"):
                    continue
                identity = f"artifact-{len(self._plan_sources) + 1}"
                run = root / path.relative_to(root).parts[0]
                if not run.is_dir():
                    run = root
                from app.model_runs import UNVERIFIED, manifest_folder
                model_metadata = {}
                model_folder = manifest_folder(path, root)
                if model_folder is not None:
                    # Model attribution requires both the complete manifest and
                    # the selected result's resolved content witness. Labels are
                    # never taken from an artifact's self-claimed planner string.
                    run = model_folder
                    model_metadata = load_inputs(plan_path=path, run_folder=run).metadata
                elif isinstance(result.get('planner'), str) and any(token in result['planner'].lower() for token in ('stowlm', 'glimmer')):
                    model_metadata = {'label': UNVERIFIED}
                embedded = data.get("instance")
                content_digest = record_instance_digest(data)
                file_digest = record_file_digest(data)
                if isinstance(embedded,dict) and 'vessel' in embedded and 'load_list' in embedded:
                    world = Instance.model_validate(deepcopy(embedded))
                    if world.id != plan['instance_id']:
                        continue
                    if content_digest is not None and not self._discovery_binding_matches(
                            world, content_digest, file_digest, embedded, run):
                        self._conflicts.add(world.id)
                    content_digest = instance_content_digest(world)
                    self._observe_instance(world, str(path.relative_to(root)), path)
                elif content_digest is not None:
                    previous = self._instance_digests.get(plan['instance_id'])
                    if previous is not None and previous != content_digest:
                        known = self._instances.get(plan['instance_id'])
                        if known is None and plan['instance_id'] in self._instance_paths:
                            known = load_instance(self._instance_paths[plan['instance_id']])
                        if known is not None and self._discovery_binding_matches(
                                known, content_digest, file_digest, None, run):
                            content_digest = instance_content_digest(known)
                        else:
                            self._conflicts.add(plan['instance_id'])
                    self._instance_digests.setdefault(plan['instance_id'],content_digest)
                if content_digest or file_digest:
                    binding = ('content',content_digest) if content_digest else ('file',file_digest)
                    previous = self._artifact_bindings.setdefault(plan['instance_id'],binding)
                    if previous[0] == binding[0] and previous != binding:
                        self._conflicts.add(plan['instance_id'])
                    self._instance_sources[plan['instance_id']] = {'path':path,'run':run}
                else:
                    self._instance_sources.setdefault(plan['instance_id'], {'path':path,'run':run})
                family = embedded.get("family", {}) if isinstance(embedded, dict) else {}
                size = (embedded.get("size_class") if isinstance(embedded, dict) else None) or data.get("rung") or family.get("size")
                if size not in {"tiny", "small", "medium", "large", "full"}:
                    size = "imported"
                self._instance_info.setdefault(plan["instance_id"], {
                    "id": plan["instance_id"], "label": f"{size} · {plan['instance_id']}",
                    "size_class": size, "source": str(path.relative_to(root)),
                    "live": bool(content_digest or file_digest) and size in {'tiny','small'},
                    "content_digest": content_digest})
                if plan['instance_id'] in self._conflicts:
                    self._instance_info[plan['instance_id']].update(conflict=True,live=False,
                        conflict_note='Different instance contents or bindings share the same id')
                self._source_fingerprints[path] = _source_digest(path)
                self._plan_sources[identity] = {
                    "path": path, "run": run, "id": identity,
                    "instance_id": plan["instance_id"],
                    "content_digest": content_digest,
                    "source_digest": self._source_fingerprints[path],
                    "label": model_metadata.get('label', f"{result.get('planner', 'Imported plan')} · {run.name}"),
                    "planner": result.get("planner", "supplied-plan"),
                    "source_files": model_metadata.get('source_files', [])}
            except (OSError, ValueError, EOFError):
                continue

    def _discovery_binding_matches(self, world, digest, checksum, embedded, run):
        """Normalize verified legacy source identities, retaining every constraint.

        Original files and embedded payloads may omit defaults added by later
        readers. Bind the raw legacy content before comparing the normalized
        catalogue world. A file checksum still needs its original file witness.
        Replay independently resolves the artifact and runs fresh checking.
        """
        matched = instance_binding_matches(world, digest, embedded)
        if matched and checksum is None:
            return True
        paths = set(self._instance_source_paths.get(world.id, ())) | set(_files(run))
        for path in sorted(paths):
            try:
                raw = _json(path)
                if 'vessel' not in raw or 'load_list' not in raw or raw.get('id') != world.id:
                    continue
                source = Instance.model_validate(deepcopy(raw))
                if instance_content_digest(source) != instance_content_digest(world):
                    continue
                source_matches = instance_binding_matches(world, digest, raw)
                if checksum is None and source_matches:
                    return True
                if checksum is not None and _source_digest(path) == checksum and (matched or source_matches):
                    return True
            except (OSError, ValueError, EOFError):
                continue
        return False

    def register(self, inputs, replay=None):
        with self._lock:
            if inputs.plan is None and not inputs.metadata.get('model_run'):
                raise ValueError('No plan available outside a model run diagnostic')
            if inputs.plan is not None and (inputs.plan.instance_id != inputs.instance.id or inputs.plan.call_index != inputs.instance.call_index):
                raise ValueError('Plan instance_id and call_index must match the selected instance')
            digest = instance_content_digest(inputs.instance)
            existing = self._instance_digests.get(inputs.instance.id)
            if existing is not None and existing != digest:
                raise ValueError("Different instance contents share the same id")
            self._assert_instance_unchanged(inputs.instance.id)
            _assert_source_files_unchanged(inputs.metadata)
            snapshot = LoadedInputs(inputs.instance.model_copy(deep=True),inputs.plan.model_copy(deep=True) if inputs.plan is not None else None,deepcopy(inputs.metadata))
            self._instances[inputs.instance.id] = snapshot.instance.model_copy(deep=True)
            self._observe_instance(snapshot.instance, 'Imported artifact')
            source_path = snapshot.metadata.get('source_path')
            if source_path is not None and Path(source_path).is_file():
                path = Path(source_path)
                self._source_fingerprints[path] = _source_digest(path)
                self._instance_source_paths.setdefault(inputs.instance.id, set()).add(path)
            identity = f"plan-{len(self._records) + 1}"
            self._records[identity] = _Record(snapshot, deepcopy(replay), digest, _plan_digest(snapshot.plan))
            return identity

    def _require_shown(self, plan_id):
        reason = self.plan_gate(plan_id) if self.plan_gate is not None else None
        if reason:
            raise Withheld(reason)

    def catalog(self):
        with self._lock:
            plans = [{"id": key, "instance_id": rec.inputs.instance.id,
                      "content_digest": rec.instance_digest,
                      "label": rec.inputs.metadata.get("label", rec.inputs.plan.id if rec.inputs.plan is not None else 'No plan produced'),
                      "planner": rec.inputs.metadata.get("planner", "supplied-plan"),
                      "run": _run_name(rec.inputs.metadata)}
                     for key, rec in self._records.items()]
            plans.extend({k: v for k, v in source.items() if k not in {"path", "run"}}
                         for key, source in self._plan_sources.items() if key not in self._records)
            view = deepcopy({"instances": list(self._instance_info.values()), "plans": plans,
                    "planners": PLANNERS, "active_plan_id": self.active_plan_id})
        if self.plan_gate is not None:  # outside the lock: the gate reads this catalogue too
            for index, row in enumerate(view["plans"]):
                reason = self.plan_gate(row["id"])
                if reason:
                    view["plans"][index] = {"id": row["id"], "label": row["label"], "withheld": reason}
        return view

    def assert_unchanged(self, plan_id):
        """The source gates of ``replay``, for a memo of something built from it."""
        self._require_shown(plan_id)
        with self._lock:
            record = self._records[plan_id]
            _assert_source_files_unchanged(record.inputs.metadata)
            self._assert_instance_unchanged(record.inputs.instance.id)

    def replay(self, plan_id):
        """The one way a plan leaves this service. A plan built while it was cleared is checked again on the way out."""
        self._require_shown(plan_id)
        response = self.gate_replay(plan_id)
        self._require_shown(plan_id)
        return response

    def gate_replay(self, plan_id):
        """``replay`` for the gate deciding this plan, and nobody else: it reads what it hasn't cleared yet."""
        with self._lock:
            record = self._records.get(plan_id)
            source = self._plan_sources.get(plan_id)
            if source is not None:
                _assert_source_files_unchanged(source)
                if not source['path'].is_file() or _source_digest(source['path']) != source['source_digest']:
                    raise ValueError('Plan source changed after discovery')
                self._assert_instance_unchanged(source['instance_id'])
            elif record is not None:
                self._assert_instance_unchanged(record.inputs.instance.id)
        if record is None:
            if source is None:
                raise KeyError(plan_id)
            if _source_digest(source['path']) != source['source_digest']:
                raise ValueError('Plan source changed after discovery')
            inputs = load_inputs(plan_path=source["path"], run_folder=source["run"])
            digest = instance_content_digest(inputs.instance)
            if source.get('content_digest') and digest != source['content_digest']:
                raise ValueError('Selected artifact instance contents changed after discovery')
            if _source_digest(source['path']) != source['source_digest']:
                raise ValueError('Plan source changed while loading')
            record = _Record(inputs, instance_digest=digest, plan_digest=_plan_digest(inputs.plan))
            with self._lock:
                self._cache_instance(inputs.instance, "Imported artifact")
                self._records[plan_id] = record
        with self._lock:
            _assert_source_files_unchanged(record.inputs.metadata)
            if (instance_content_digest(record.inputs.instance) != record.instance_digest or
                    _plan_digest(record.inputs.plan) != record.plan_digest):
                raise ValueError('Registered replay inputs changed after registration')
            cached = record.replay
            build = self._builds.setdefault(plan_id, threading.Lock())
        if cached is None:
            with build:
                with self._lock:
                    record = self._records.get(plan_id, record)
                    cached = record.replay
                if cached is None:
                    source_result = record.inputs.metadata.get("planner_result")
                    cached = build_replay(record.inputs.instance, record.inputs.plan,
                        planner_result=PlannerResult.model_validate(source_result) if source_result else None)
                    cached["metadata"].update({k: v for k, v in record.inputs.metadata.items() if k in SOURCE_KEYS})
                    with self._lock:
                        record.replay = cached
        from app.explanations import explanation_for_replay
        response = deepcopy(cached)
        # A memo holds evidence from this process's loaded epoch; it is only served as approved while that is current.
        drift = check_cache.epoch_drift()
        if drift:
            withdraw_approval(response, drift)
        response['explanation'] = explanation_for_replay(response,
            source_path=record.inputs.metadata.get('source_path'))
        # A source can change while the checker/replay is being materialized.
        # Revalidate even cached results: an old approval never survives drift.
        with self._lock:
            _assert_source_files_unchanged(record.inputs.metadata)
            self._assert_instance_unchanged(record.inputs.instance.id)
            if source is not None and (not source['path'].is_file() or
                    _source_digest(source['path']) != source['source_digest']):
                raise ValueError('Plan source changed while loading')
        return response

    def _cache_instance(self, instance, source):
        """Retain canonical identity; artifact labels never change the world."""
        with self._lock:
            self._assert_instance_unchanged(instance.id)
            existing = self._instance_digests.get(instance.id)
            if existing is not None and existing != instance_content_digest(instance):
                raise ValueError("Different instance contents share the same id")
            self._instances[instance.id] = instance.model_copy(deep=True)
            self._observe_instance(instance,source)
            previous = self._instance_info.get(instance.id, {})
            self._instance_info[instance.id] = self._info(instance, previous.get("source", source))

    def comparison(self, left, right):
        from app.comparison import build_comparison
        left_replay, right_replay = self.replay(left), self.replay(right)
        if left_replay['plan'] is None or right_replay['plan'] is None:
            raise ValueError('No plan available for comparison')
        result = build_comparison(left_replay, right_replay)
        result["left"]["replay_id"] = left
        result["right"]["replay_id"] = right
        return result

    def start_solve(self, instance_id, planner="solver.scalable", compare=False, seed=0, time_limit_s=15):
        if planner not in {"solver.scalable", "solver.exact", "baseline.twostep"}:
            raise ValueError("Unknown or imported-only planner")
        if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
            raise ValueError("seed must be an integer between 0 and 2147483647")
        if type(compare) is not bool:
            raise ValueError("compare must be a boolean")
        if type(time_limit_s) not in {int, float} or not math.isfinite(time_limit_s) or not 0 < time_limit_s <= 30:
            raise ValueError("time_limit_s must be finite and between 0 and 30 seconds")
        with self._lock:
            info = self._instance_info[instance_id]
            self._assert_instance_unchanged(instance_id)
            if info["size_class"] not in {"tiny", "small"}:
                raise ValueError("Live solve is limited to tiny and small instances; import larger plans")
            if self._active_job is not None:
                raise ConflictError("A live solve is already running")
            identity = uuid4().hex
            self._jobs[identity] = {"id": identity, "status": "queued", "stage": "Queued",
                                    "started": monotonic(), "plan_ids": [], "results": []}
            self._active_job = identity
        thread = threading.Thread(target=self._solve, args=(identity, instance_id, planner, compare, seed, float(time_limit_s)),
                                  name=f"stowlm-demo-{identity[:8]}", daemon=True)
        thread.start()
        return self.job(identity)

    def job(self, identity):
        with self._lock:
            value = self._jobs[identity]
            return {k: (list(v) if isinstance(v, list) else v) for k, v in value.items() if k not in {"started", "finished"}} | {
                "elapsed_s": round(value.get("finished", monotonic()) - value["started"], 2)}

    def _update(self, identity, **values):
        with self._lock:
            self._jobs[identity].update(values)

    def _solve(self, identity, instance_id, planner_id, compare, seed, limit):
        try:
            self._update(identity, status="running", stage="Loading selected instance")
            with self._lock:
                self._assert_instance_unchanged(instance_id)
                instance = self._instances.get(instance_id)
            if instance is None:
                path = self._instance_paths.get(instance_id)
                if path is not None:
                    instance = load_instance(path)
                else:
                    source = self._instance_sources[instance_id]
                    if _source_digest(source['path']) != self._source_fingerprints[source['path']]:
                        raise ValueError('Selected artifact source changed after discovery')
                    instance = load_inputs(plan_path=source["path"], run_folder=source["run"]).instance
                self._assert_instance_unchanged(instance_id)
                if (instance_id in self._instance_digests and
                        instance_content_digest(instance) != self._instance_digests[instance_id]):
                    raise ValueError('Lazy instance contents changed after discovery')
                if instance.size_class not in {"tiny", "small"}:
                    raise ValueError("Resolved instance is too large for live solving")
                self._cache_instance(instance, "Imported artifact")
            # The planner cannot mutate the catalogue's bound snapshot.
            instance = instance.model_copy(deep=True)
            expected_digest = instance_content_digest(instance)
            order = [planner_id]
            if compare:
                order.append("solver.scalable" if planner_id == "baseline.twostep" else "baseline.twostep")
            for selected in order:
                self._update(identity, stage=f"{selected}: planning → ballast → checker → repair")
                result, report = run_planner(_make_planner(selected), instance, seed=seed, time_limit_s=limit,
                                             allow_repair=selected != 'baseline.twostep')
                if instance_content_digest(instance) != expected_digest:
                    raise ValueError('Planner changed the selected instance contents')
                summary = {"planner": selected, "status": result.status, "message": result.message,
                           "passed": False, "shippable": False,
                           "validation_status": "NOT VALIDATED"}
                if result.plan is not None:
                    self._update(identity, stage=f"{selected}: preparing engine replay and fresh checker panels")
                    inputs = LoadedInputs(instance, result.plan, {"planner": selected, "planner_version": result.planner_version,
                        "planner_status": result.status, "label": next(p["label"] for p in PLANNERS if p["id"] == selected),
                        "source_kind": "Live run_planner", "planner_result": result.model_dump(mode="json")})
                    replay = build_replay(instance, result.plan, planner_result=result)
                    replay["metadata"].update({k: v for k, v in inputs.metadata.items() if k in SOURCE_KEYS})
                    # Job summaries use the same fresh, bound completeness and
                    # shipping gates as the displayed replay, never raw flags
                    # from the pipeline's potentially incomplete report.
                    summary.update(passed=replay['checker']['passed'],
                                   shippable=replay['metadata']['shippable'],
                                   validation_status=replay['metadata']['validation_status'])
                    plan_id = self.register(inputs, replay)
                    summary["plan_id"] = plan_id
                    with self._lock:
                        self._jobs[identity]["plan_ids"].append(plan_id)
                with self._lock:
                    self._jobs[identity]["results"].append(summary)
            with self._lock:
                has_replay = bool(self._jobs[identity]['plan_ids'])
            self._update(identity, status="done", stage=("Complete · review fresh checker verdicts" if has_replay
                else "Complete · no replayable output"), finished=monotonic())
        except Exception as error:
            self._update(identity, status="error", stage="Stopped", error=f"{type(error).__name__}: {error}", finished=monotonic())
        finally:
            with self._lock:
                self._active_job = None

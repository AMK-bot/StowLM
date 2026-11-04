"""Serve stage bundles: cached physics, one bounded background worker, baseline/lower-bound side data.

``/api/stage`` must answer in a few seconds, so anything slow (physics keyframes, a two-step baseline run) is
done on ONE background thread and reported as pending; the browser polls ``/api/stage/physics``. Results are kept
in small bounded dictionaries (oldest entries dropped) so a long demo session cannot grow memory without limit.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from pathlib import Path
import threading
from time import monotonic
from typing import Any

from stowage.model import Instance, MovePlan

from app import check_cache, stage_physics
from app.explanation_links import explanation_links
from app.stage import TWO_STEP_LABEL, build_stage_bundle, lower_bound_entry, stage_counts

ROOT = Path(__file__).resolve().parents[1]
BASELINE_ROOTS = (ROOT / "experiments/mvp/ui/dev-runs", ROOT / "experiments/mvp/smoke")
MAX_ENTRIES = 8
BASELINE_TIME_LIMIT_S = 60.0
EXPLANATION_WAIT_S = 2.0  # a bundle request waits this long for the re-binding, then shows the template
LIVE_BASELINE_SIZES = {"tiny", "small"}


class _Bounded(OrderedDict):
    def put(self, key, value):
        self[key] = value
        self.move_to_end(key)
        while len(self) > MAX_ENTRIES:
            self.popitem(last=False)


class StageService:
    """Wrap a ``DemoService``; never touches files named by HTTP requests (registry ids only)."""

    def __init__(self, demo, *, cache_dir=stage_physics.CACHE_DIR, live_physics: bool = True,
                 live_baseline: bool = False, physics_workers: int = 1, extra_cache_dirs=(), memo_bundles: bool = False):
        self.demo = demo
        self.memo_bundles = memo_bundles  # --v6: a settled bundle is served again while nothing it was built from changed
        self.cache_dir = cache_dir
        # Read-only fallbacks (never written): --timeline-cache dirs, then each plan's run-local
        # ``<run folder>/timeline-cache``. Same digest binding as the main cache (stage_physics.load_cached).
        self.extra_cache_dirs = tuple(Path(d) for d in extra_cache_dirs)
        self.live_physics = live_physics
        self.live_baseline = live_baseline
        self.physics_workers = physics_workers
        self._lock = threading.RLock()
        self._physics = _Bounded()      # digest key -> {"status", "done", "total", "physics", "error"}
        self._baselines = _Bounded()    # instance digest -> (entry | None, reason)
        self._lower = _Bounded()        # instance digest -> (entry | None, reason)
        self._bundles = _Bounded()      # plan_id -> bundle without physics/baseline (cheap to refresh)
        self._queue: deque = deque()
        self._queued: set = set()
        self._worker: threading.Thread | None = None
        self._current: str | None = None

    # ------------------------------------------------------------ worker
    def _submit(self, key: str, fn) -> None:
        with self._lock:
            if key in self._queued or len(self._queue) >= MAX_ENTRIES:
                return
            self._queued.add(key)
            self._queue.append((key, fn))
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name="stowlm-stage-worker", daemon=True)
                self._worker.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._worker = None
                    self._current = None
                    return
                key, fn = self._queue.popleft()
                self._current = key
            try:
                fn()
            except Exception:  # each task records its own error state
                pass
            finally:
                with self._lock:
                    self._queued.discard(key)

    def wait_idle(self, timeout: float = 600.0) -> bool:
        """Block until the background queue is empty (CLI/tests only; never from an HTTP handler)."""
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            with self._lock:
                worker = self._worker
            if worker is None:
                return True
            worker.join(timeout=0.2)
        return False

    # ------------------------------------------------------------ inputs
    def _inputs(self, replay: dict) -> tuple[Instance, MovePlan | None]:
        instance = Instance.model_validate(replay["instance"])
        plan = MovePlan.model_validate(replay["plan"]) if replay.get("plan") else None
        return instance, plan

    # ------------------------------------------------------------ physics
    def physics_status(self, plan_id: str, *, start: bool = True, replay: dict | None = None) -> dict:
        replay = replay if replay is not None else self.demo.replay(plan_id)
        instance, plan = self._inputs(replay)
        if plan is None:
            return {"status": "unavailable", "done": 0, "total": 0, "physics": None, "source": None,
                    "reason": "no plan"}
        # Memoised frames are from this process's loaded epoch: not served once that is no longer current.
        drift = stage_physics.physics_drift()
        if drift:
            return {"status": "unavailable", "done": 0, "total": 0, "physics": None, "source": None, "reason": drift}
        digests = stage_physics.input_digests(instance, plan)
        key = digests["key"]
        with self._lock:
            entry = self._physics.get(key)
        if entry and entry["status"] in {"ready", "running", "queued"}:
            return self._physics_view(entry)
        cached, source = stage_physics.load_cached(instance, plan, self.cache_dir), "cache"
        if cached is None:
            for directory in self.read_only_cache_dirs(replay):
                cached = stage_physics.load_cached(instance, plan, directory)
                if cached is not None:
                    source = f"cache (read-only {directory.relative_to(ROOT) if directory.is_relative_to(ROOT) else directory})"
                    break
        if cached is not None:
            entry = {"status": "ready", "done": len(cached["frames"]), "total": len(cached["frames"]),
                     "physics": cached, "source": source, "reason": None}
            with self._lock:
                self._physics.put(key, entry)
            return self._physics_view(entry)
        if entry and entry["status"] == "unavailable":
            return self._physics_view(entry)
        if not self.live_physics or not start:
            return {"status": "unavailable", "done": 0, "total": 0, "physics": None, "source": None,
                    "reason": "no precomputed physics cache for this exact instance+plan at the current checker "
                              "and physics epoch; live compute off"}
        frames, policy = stage_physics.select_keyframes(instance, plan)
        entry = {"status": "queued", "done": 0, "total": len(frames), "physics": None, "source": "live",
                 "reason": None, "started": monotonic()}
        with self._lock:  # two requests can get here at once: the first one's entry is the one its task updates
            other = self._physics.get(key)
            if other and other["status"] in {"ready", "running", "queued"}:
                return self._physics_view(other)
            self._physics.put(key, entry)

        def task():
            entry["status"] = "running"
            done = stage_physics.load_partial(instance, plan, self.cache_dir)
            entry["done"] = sum(k in done for k in frames)

            def progress(k, frame):
                done[k] = frame
                entry["done"] = sum(f in done for f in frames)
                try:
                    stage_physics.write_cache(instance, plan, None, partial=done, cache_dir=self.cache_dir)
                except OSError:
                    pass
            try:
                stage_physics.compute_physics(instance, plan, frames=[k for k in frames if k not in done],
                                              workers=self.physics_workers, done={}, on_frame=progress)
                physics = stage_physics.compute_physics(instance, plan, frames=frames, done=done)
                physics["keyframes"] = policy
                try:
                    stage_physics.write_cache(instance, plan, physics, cache_dir=self.cache_dir)
                except OSError:
                    pass
                entry.update(status="ready", physics=physics, done=len(frames))
            except Exception as error:
                entry.update(status="unavailable", reason=f"{type(error).__name__}: {error}")
        self._submit("physics:" + key, task)
        return self._physics_view(entry)

    def read_only_cache_dirs(self, replay: dict) -> list[Path]:
        """Extra cache directories for this plan: configured ones, then its run folder's ``timeline-cache``.

        Server-side paths only (the registry's own run folder, never a request path); nothing is written there.
        """
        dirs = list(self.extra_cache_dirs)
        folder = (replay.get("metadata") or {}).get("run_folder")
        if folder:
            local = Path(folder).resolve() / "timeline-cache"
            if local.is_dir():
                dirs.append(local.resolve())
        return [d for d in dict.fromkeys(dirs) if d.is_dir() and d.resolve() != Path(self.cache_dir).resolve()]

    @staticmethod
    def _physics_view(entry: dict) -> dict:
        return {"status": "running" if entry["status"] == "queued" else entry["status"], "done": entry["done"],
                "total": entry["total"], "physics": entry.get("physics"), "source": entry.get("source"),
                "reason": entry.get("reason")}

    # ------------------------------------------------------------ baseline / lower bound
    def _registered_baseline_id(self, instance_digest: str, exclude: str) -> str | None:
        for plan in self.demo.catalog()["plans"]:
            if (plan["id"] != exclude and plan.get("planner") == "baseline.twostep"
                    and plan.get("content_digest") == instance_digest):
                return plan["id"]
        return None

    def _baseline_from_registry(self, baseline_id: str) -> tuple[dict | None, str | None]:
        replay = self.demo.replay(baseline_id)  # may build replay + full check (slow): run on the worker
        if not replay.get("frames"):
            return None, "registered baseline has no replayable plan"
        return baseline_entry(replay, plan_id=baseline_id, source="registered plan, replayed and freshly checked"), None

    def baseline(self, plan_id: str, replay: dict, instance: Instance) -> tuple[dict | None, str | None]:
        from app.identity import instance_content_digest
        digest = instance_content_digest(instance)
        if (replay.get("metadata") or {}).get("planner") == "baseline.twostep":
            return None, "this plan is the baseline"
        drift = check_cache.epoch_drift()  # a memoised rival's verdict is from the loaded epoch
        if drift:
            return None, drift
        with self._lock:
            if digest in self._baselines:
                return self._baselines[digest]
        registered = self._registered_baseline_id(digest, plan_id)
        if registered is not None:
            pending = (None, "registered two-step baseline is being replayed and checked in the background")
            with self._lock:
                self._baselines.put(digest, pending)

            def replay_task():
                try:
                    value = self._baseline_from_registry(registered)
                except Exception as error:
                    value = (None, f"baseline replay failed: {type(error).__name__}: {error}")
                with self._lock:
                    self._baselines.put(digest, value)
            self._submit("baseline:" + digest, replay_task)
            return pending
        saved = find_saved_baseline(instance.id)
        if saved is not None:
            pending = (None, "saved two-step baseline result is being replayed and checked in the background")
            with self._lock:
                self._baselines.put(digest, pending)

            def saved_task():
                from app.backend import build_replay
                from app.inputs import load_inputs
                from stowage.model import PlannerResult
                try:
                    loaded = load_inputs(plan_path=saved, selected_instance=instance)
                    if instance_content_digest(loaded.instance) != digest or loaded.plan is None:
                        value = (None, f"saved baseline {saved.name} does not bind to this instance")
                    else:
                        raw = loaded.metadata.get("planner_result")
                        rep = build_replay(loaded.instance, loaded.plan,
                                           planner_result=PlannerResult.model_validate(raw) if raw else None)
                        value = (baseline_entry(rep, plan_id=None, source=f"saved result {saved.relative_to(ROOT)}, "
                                                                          "replayed and freshly checked"), None)
                except Exception as error:
                    value = (None, f"saved baseline unusable: {type(error).__name__}: {error}")
                with self._lock:
                    self._baselines.put(digest, value)
            self._submit("baseline:" + digest, saved_task)
            return pending
        if not self.live_baseline:
            return None, "no two-step baseline plan registered for this instance"
        if instance.size_class not in LIVE_BASELINE_SIZES:
            return None, "no two-step baseline plan registered; live baseline limited to tiny/small"
        with self._lock:
            self._baselines.put(digest, (None, "two-step baseline is running in the background"))

        def task():
            from stowage.planner import run_planner
            from app.backend import build_replay
            from app.flow import _make_planner
            try:
                # D79: C2 off, same construction as the live solve path.
                result, _ = run_planner(_make_planner("baseline.twostep"), instance.model_copy(deep=True), seed=0,
                                        time_limit_s=BASELINE_TIME_LIMIT_S, allow_repair=False)
                if result.plan is None:
                    value = (None, f"two-step baseline produced no plan (status {result.status}: {result.message})")
                else:
                    rep = build_replay(instance, result.plan, planner_result=result)
                    value = (baseline_entry(rep, plan_id=None, source="measured live: run_planner(TwoStepPlanner, "
                                            "allow_repair=False) in this session (explicit --live-baseline fallback)"), None)
            except Exception as error:
                value = (None, f"two-step baseline failed: {type(error).__name__}: {error}")
            with self._lock:
                self._baselines.put(digest, value)
        self._submit("baseline:" + digest, task)
        return None, "two-step baseline is running in the background"

    def lower_bound(self, instance: Instance) -> tuple[dict | None, str | None]:
        from app.identity import instance_content_digest
        digest = instance_content_digest(instance)
        drift = check_cache.epoch_drift()
        if drift:
            return None, drift
        with self._lock:
            if digest not in self._lower:
                self._lower.put(digest, lower_bound_entry(instance))
            return self._lower[digest]

    # ------------------------------------------------------------ bundle
    def bundle(self, plan_id: str | None = None) -> dict[str, Any]:
        plan_id = plan_id or self.demo.active_plan_id
        if self.memo_bundles:
            with self._lock:
                hit = self._bundles.get(plan_id)
            if hit is not None and not stage_physics.physics_drift():
                self.demo.assert_unchanged(plan_id)
                return hit
        bundle = self._build_bundle(plan_id)
        settled = bundle.get("physics") and not any("background" in str(reason or "") for reason in bundle["comparison"]["reasons"].values())
        if self.memo_bundles and settled and "being re-bound" not in str(bundle["explanation"].get("cache_reason")):
            with self._lock:
                self._bundles.put(plan_id, bundle)
        return bundle

    def _build_bundle(self, plan_id: str) -> dict[str, Any]:
        replay = self.demo.replay(plan_id)
        if replay.get("plan") is None:
            if not (replay.get("metadata") or {}).get("model_run"):
                raise ValueError("No plan available for the stage view")
            # StowLM run without a plan (dead end): the bundle carries the dead-end card over the starting world only.
            replay = dict(replay)
            world = Instance.model_validate(replay["instance"])
            replay["frames"] = [initial_frame(world)]
            if not (replay.get("metadata") or {}).get("vessel_label"):
                from app.backend import vessel_display_label
                replay["metadata"] = {**(replay.get("metadata") or {}), "vessel_label": vessel_display_label(world)}
        instance, _ = self._inputs(replay)
        from app.stage_instruction import live_replan_refusal
        if live_replan_refusal(replay) is not None:  # never animated, measured or priced as that result
            status = {"status": "unavailable", "physics": None, "done": 0, "total": 0, "source": None,
                      "reason": "instruction replan not shown as a plan"}
        else:
            status = self.physics_status(plan_id, replay=replay)
        baseline, baseline_reason = self.baseline(plan_id, replay, instance)
        lower, lower_reason = self.lower_bound(instance)
        bundle = build_stage_bundle(replay, plan_id=plan_id, physics=status["physics"],
                                    physics_status=status["status"], baseline=baseline,
                                    baseline_reason=baseline_reason, lower_bound=lower,
                                    lower_bound_reason=lower_reason)
        bundle["physics_progress"] = {k: status.get(k) for k in ("done", "total", "source", "reason")}
        self._add_real_data(bundle, plan_id, replay, instance)
        return bundle

    # ------------------------------------------------------------ recorded runs (stage_extras; additive fields)
    def _add_real_data(self, bundle: dict, plan_id: str, replay: dict, instance: Instance) -> None:
        from app import stage_extras as ex
        from app.stage import build_xray
        # Muse Spark as a third racer (replayed + freshly checked; its file's counts are never used).
        spark, spark_reason = self.spark(plan_id, replay, instance)
        bundle["comparison"]["spark"] = spark
        bundle["comparison"]["reasons"]["spark"] = spark_reason if spark is None else None
        # Mask x-ray: legal slots recomputed from the action space at each decoded state (tiny/small).
        model = (replay.get("metadata") or {}).get("model_run") or {}
        xray = bundle["xray"]
        if xray.get("available") and model.get("trace"):
            import hashlib, json
            key = "legal:" + hashlib.sha256(json.dumps([replay.get("plan"), model["trace"]], sort_keys=True,
                                                        default=str).encode()).hexdigest()
            with self._lock:
                cached = self._lower.get(key)
            if cached is None:
                _, plan = self._inputs(replay)
                cached = ex.legal_slots_for_trace(instance, plan, model["trace"])
                with self._lock:
                    self._lower.put(key, cached)
            slots, note = cached
            if slots:
                xray = build_xray(replay, bundle["steps"], legal_slots=slots)
                xray["legal_slots_note"] = ("recomputed by the app: feasibility engine (D79, C2 off) replayed to each "
                                            "decoded state; legal-set size equals the trace's n_legal at every row")
            else:
                xray["legal_slots_note"] = note
        else:
            alt = self._xray_alternate(plan_id, instance)
            if alt:
                xray["alternate"] = alt
        xray["highlights"] = ex.spark_highlights(instance.id)
        bundle["xray"] = xray
        # Cached Spark explanation, only when it re-renders from the current reports of both plans.
        drift = check_cache.epoch_drift()  # the paragraph quotes verdicts from the loaded epoch
        cached_ex, cache_reason = ((None, drift) if drift else
                                   self._stage_explanation(plan_id, replay, instance, bundle["comparison"]["baseline"]))
        cached_ex = dict(cached_ex) if cached_ex is not None else None
        if cached_ex is not None:
            cached_ex["live_available"] = False
            cached_ex["live_reason"] = bundle["explanation"].get("live_reason")
            bundle["explanation"] = cached_ex
        else:
            bundle["explanation"]["cache_reason"] = cache_reason
            if bundle["explanation"].get("source") == "template":
                bundle["explanation"]["label"] = "Deterministic template (no bound Spark text)"
        bundle["explanation"]["links"] = explanation_links(replay, bundle["explanation"])

    def _stage_explanation(self, plan_id: str, replay: dict, instance: Instance,
                           baseline: dict | None) -> tuple[dict | None, str | None]:
        """Memo keyed by the evidence itself, so a changed report, baseline or explainer never reuses an old answer."""
        from app import stage_extras as ex
        data, reason = ex.explanation_entry(replay)
        if data is None:
            return None, reason
        key = "explain:" + plan_id + ":" + ex.explanation_evidence_key(replay, data, baseline)
        with self._lock:
            hit = self._lower.get(key)
        if hit is not None:
            return hit
        pending = (None, "cached explanation is being re-bound to fresh checker reports in the background")
        with self._lock:
            self._lower.put(key, pending)

        done = threading.Event()

        def task():
            try:
                value = ex.cached_explanation(replay, baseline_replay=ex.explanation_baseline_replay(data, instance),
                                              displayed_baseline=baseline)
            except Exception as error:
                value = (None, f"cached explanation not bound: {type(error).__name__}: {error}")
            with self._lock:
                self._lower.put(key, value)
            done.set()
        self._submit(key, task)
        with self._lock:
            queued = key in self._queued
            if not queued and self._lower.get(key) is pending:  # queue full: retry on the next bundle
                self._lower.pop(key, None)
        if queued:  # the page fetches its bundle once: a tiny plan's checks are memoised, so this is usually enough
            done.wait(EXPLANATION_WAIT_S)
        with self._lock:
            return self._lower.get(key, pending)

    def _registered(self, planner: str, instance_digest: str, exclude: str) -> str | None:
        for plan in self.demo.catalog()["plans"]:
            if plan["id"] != exclude and plan.get("planner") == planner and plan.get("content_digest") == instance_digest:
                return plan["id"]
        return None

    def _xray_alternate(self, plan_id: str, instance: Instance) -> dict | None:
        from app.identity import instance_content_digest
        other = self._registered("stowlm.base", instance_content_digest(instance), plan_id)
        if other is None:
            return None
        return {"plan_id": other, "label": "x-ray: base Glimmer + mask run on this instance",
                "note": "Same instance, the untuned base model's own plan decoded under the mask (its moves differ "
                        "from this plan); replayed and fully re-checked when opened."}

    def spark(self, plan_id: str, replay: dict, instance: Instance) -> tuple[dict | None, str | None]:
        from app import stage_extras as ex
        from app.identity import instance_content_digest
        if (replay.get("metadata") or {}).get("planner") == ex.SPARK_PLANNER:
            return None, "this plan is the Muse Spark plan"
        drift = check_cache.epoch_drift()
        if drift:
            return None, drift
        digest = instance_content_digest(instance)
        key = "spark:" + digest
        with self._lock:
            if key in self._baselines:
                return self._baselines[key]
        registered = self._registered(ex.SPARK_PLANNER, digest, plan_id)
        path = ex.spark_result_path(instance.id)
        if registered is None and path is None:
            value = (None, "no Muse Spark zero-shot run recorded for this instance")
            with self._lock:
                self._baselines.put(key, value)
            return value
        pending = (None, "Muse Spark plan is being replayed and checked in the background")
        with self._lock:
            self._baselines.put(key, pending)

        def task():
            try:
                if registered is not None:
                    rep = self.demo.replay(registered)
                    value = (ex.spark_entry_from_replay(rep, plan_id=registered,
                             source="recorded Spark plan (runs/spark-zeroshot), replayed by the move engine and freshly checked"), None)
                else:
                    from app.backend import build_replay
                    from app.inputs import load_inputs
                    from stowage.model import PlannerResult
                    loaded = load_inputs(plan_path=path, selected_instance=instance)
                    if instance_content_digest(loaded.instance) != digest or loaded.plan is None:
                        value = (None, "recorded Spark plan does not bind to this instance")
                    else:
                        raw = loaded.metadata.get("planner_result")
                        rep = build_replay(loaded.instance, loaded.plan,
                                           planner_result=PlannerResult.model_validate(raw) if raw else None)
                        value = (ex.spark_entry_from_replay(rep, plan_id=None,
                                 source="recorded Spark plan (runs/spark-zeroshot), replayed by the move engine and freshly checked"), None)
            except Exception as error:
                value = (None, f"Spark replay failed: {type(error).__name__}: {error}")
            with self._lock:
                self._baselines.put(key, value)
        self._submit(key, task)
        return pending

    def explain(self, plan_id: str | None, box: str | None, live: bool = False) -> dict:
        plan_id = plan_id or self.demo.active_plan_id
        replay = self.demo.replay(plan_id)
        explanation = replay.get("explanation") or {}
        live_reason = ("Live Muse Spark calls are disabled in the stage view; cached or template text only"
                       if live else None)
        if not box:
            return {"plan_id": plan_id, "box": None, "source": explanation.get("source") or "none",
                    "text": explanation.get("text"), "live_used": False, "live_reason": live_reason}
        boxes = {c["id"]: c for c in replay["instance"]["containers"]}
        if box not in boxes:
            raise KeyError(box)
        return {"plan_id": plan_id, "box": box, "source": "template", "text": box_explanation(replay, box),
                "live_used": False, "live_reason": live_reason}


def baseline_entry(replay: dict, *, plan_id: str | None, source: str) -> dict:
    """The baseline racer: engine counts + fresh verdict + the shipping/repair/provenance evidence a saving needs."""
    from app.comparison import racer_evidence
    from app.stage_extras import plan_digest
    return {"label": TWO_STEP_LABEL, "plan_id": plan_id,
            "counts": stage_counts(replay["frames"][-1]["counts"]),
            "checker_passed": bool(replay["checker"].get("passed")), "source": source,
            "plan_sha256": plan_digest(replay["plan"]), **racer_evidence(replay)}


def initial_frame(instance: Instance) -> dict:
    """Frame zero (the starting world) for a plan-less model run; no move, no counts."""
    from stowage.moves import initial_state
    return {"state": initial_state(instance).model_dump(mode="json"), "move": None, "counts": {},
            "highlight": {"container_id": None, "blockers": [], "target_id": None}}


def find_saved_baseline(instance_id: str, roots=None) -> Path | None:
    """A saved ``baseline.twostep`` PlannerResult for this instance id in the known demo run folders.

    Server-side directories only (never a request path). Content binding is enforced when it is loaded.
    """
    import json
    for root in roots or BASELINE_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.json")):
            try:
                if "instance" in path.name or path.stat().st_size > 4 * 1024 * 1024:
                    continue
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            value = data.get("result") or data.get("planner_result") or data if isinstance(data, dict) else None
            if (isinstance(value, dict) and value.get("planner") == "baseline.twostep" and value.get("status") == "ok"
                    and isinstance(value.get("plan"), dict) and value["plan"].get("instance_id") == instance_id):
                return path
    return None


def box_explanation(replay: dict, box_id: str) -> str:
    """Deterministic per-box sentence from replayed facts only (where it went, when, and why it moved)."""
    from stowage.conventions import parse_slot
    box = next(c for c in replay["instance"]["containers"] if c["id"] == box_id)
    frames = replay.get("frames") or []
    lifts = [(i, f["move"]) for i, f in enumerate(frames[1:], start=1) if (f["move"] or {}).get("container_id") == box_id]
    final = next((p["slot"] for p in frames[-1]["state"]["ship"]["placements"] if p["container_id"] == box_id),
                 None) if frames else None
    mass = box.get("gross_mass_kg")
    parts = [f"{box_id}: {box.get('length_ft')}' {'empty' if box.get('empty') else 'full'}"
             f"{' reefer' if box.get('reefer') else ''}{' DG class ' + str(box['dg'].get('imdg_class')) if box.get('dg') else ''}"
             f", {mass / 1000:.1f} t, discharging at {box.get('pod')}."]
    if final:
        addr = parse_slot(final)
        parts.append(f"Stowed in bay {addr.bay}, row {addr.row}, tier {addr.tier} "
                     f"({'on deck' if addr.tier >= 80 else 'in the hold'}).")
    else:
        parts.append("Not aboard at departure.")
    names = {"load": "loaded", "relocate": "rehandled in the yard", "restow_off": "restowed off to the quay",
             "restow_on": "restowed back on", "restow_shift": "shifted on board", "discharge": "discharged"}
    if lifts:
        parts.append("Crane lifts: " + ", ".join(f"#{i} {names.get(m['kind'], m['kind'])}" for i, m in lifts) +
                     f" (of {len(frames) - 1}).")
    return " ".join(parts)

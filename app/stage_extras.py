"""Real recorded data around the stage page (task 6.5 data pass, 29 Oct): default demo instance, companion runs,
mask x-ray legal slots, the Muse Spark racer, Spark-without-mask highlights and cached Spark explanations.

Everything here reads frozen run folders on the server side (never a path from an HTTP request) and re-derives what
it shows: plans are replayed through the move engine and re-checked by the full checker before any count is used;
explanation caches are shown only when they bind to the displayed plan; highlights quote saved files verbatim.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "experiments/mvp/runs"
EXPLAIN_CACHE = ROOT / "experiments/mvp/explain-cache"

# Default demo instance (29 Oct data pass). random.tiny.8000004.0.2 (feeder_800 slice, random yard): exact solver
# proved optimal at 5 weighted lifts = the lower bound (5); frozen two-step 7; Muse Spark zero-shot 7; base Glimmer +
# mask 17 (test-references.md, scores.jsonl, base-mask/tiny-summary.json). One yard rehandle and hatch work now and
# later. No tiny test instance carries DG or reefer boxes (checked over all 15).
DEFAULT_INSTANCE = "random.tiny.8000004.0.2"
PRIMARY_RUN = RUNS / "test-exact"
BASELINE_RUN = RUNS / "test-twostep"
BASE_MASK_RUNS = (RUNS / "base-mask/tiny", RUNS / "base-mask/tiny-rest")
SPARK_RUN = RUNS / "spark-zeroshot"
SPARK_ABLATION = SPARK_RUN / "ablation-compact-only-test"
SPARK_LABEL = "Muse Spark zero-shot (Meta API)"
SPARK_PLANNER = "muse-spark-zero-shot"
BASE_MASK_PLANNER = "stowlm.base"
LEGAL_SLOT_RUNGS = {"tiny", "small"}

# Video showcase (task 6.5 showcase swap, 29 Oct; gold swap 29 Oct evening): frozen EVAL v2.1 small Laem Chabang
# calls, D97 gold solver (two-step warm start + LNS; experiments/mvp/runs/showcase-v2-gold, the StowLM stand-in, labelled
# in-frame per D99) against the frozen two-step v4.4 rival (showcase-v2-twostep). Measured (GOLD-REPORT.md): gold ties
# two-step on both calls (feeder_1700-small 37 = 37 weighted, bound 15; feeder_500-small 29 = 29, bound 9). Both
# checker/mask-approved by their owners and re-checked here on load. First = active plan (feeder_1700-small, 3 DG +
# 1 reefer, 15 yard rehandles); the rest are registered for the plan picker. feeder_800-small has no approved plan
# and is left out. The earlier scalable-solver stand-in (showcase-v2-solver, 63 weighted) is no longer registered.
SHOWCASE_SOLVER_RUN = RUNS / "showcase-v2-gold"
SHOWCASE_TWOSTEP_RUN = RUNS / "showcase-v2-twostep"
VIDEO_SHOWCASE = tuple(
    {"run_folder": str(SHOWCASE_SOLVER_RUN), "entry": f"instances/{iid}/result.json",
     "compare_run_folder": str(SHOWCASE_TWOSTEP_RUN), "compare_entry": f"instances/{iid}/result.json"}
    for iid in ("random.small.29000201.0.7", "random.small.29000022.0.7"))  # feeder_1700-small, feeder_500-small

# D144: recorded solver replan of feeder_1700-small under one DG instruction (never StowLM; D99 label in every mode).
# Registered by --video-showcase when the folder exists; built by experiments/mvp/ui/make_dg_replan.py.
INSTRUCTION_RUN = RUNS / "instruction-replan-dg"
INSTRUCTION_ENTRY = "instances/random.small.29000201.0.7/result.json"
INSTRUCTION_LABEL = "Search software replan under the instruction"
# D170: the pre-registered StowLM decode under a Spark-parsed sentence, on the same call. Its label rule is in app/stage_instruction.py.
BINDING_RUN = RUNS / "stowlm-instruction-binding"
BINDING_LABEL = "StowLM replan under the instruction"


def _entry(instance_id: str) -> str:
    return f"instances/{instance_id}/result.json"


def default_selection(instance_id: str = DEFAULT_INSTANCE) -> dict | None:
    """Run-folder selection for a no-argument launch, or None when the frozen runs are missing."""
    primary, baseline = PRIMARY_RUN / _entry(instance_id), BASELINE_RUN / _entry(instance_id)
    if not primary.is_file():
        return None
    return {"run_folder": str(PRIMARY_RUN), "entry": _entry(instance_id),
            "compare_run_folder": str(BASELINE_RUN) if baseline.is_file() else None,
            "compare_entry": _entry(instance_id) if baseline.is_file() else None}


def instruction_inputs() -> tuple[list, list[str]]:
    """The recorded instruction replans as registry inputs (replayed and re-checked when shown), or notes."""
    from app.inputs import load_inputs
    out, notes = [], []
    for run, label in ((INSTRUCTION_RUN, INSTRUCTION_LABEL), (BINDING_RUN, BINDING_LABEL)):
        if not (run / INSTRUCTION_ENTRY).is_file():
            notes.append(f"instruction replan not registered: {run.name} not on disk")
            continue
        try:
            loaded = load_inputs(run_folder=run, entry=INSTRUCTION_ENTRY)
        except (ValueError, OSError) as error:
            notes.append(f"instruction replan not registered: {error}")
            continue
        loaded.metadata["label"] = label
        out.append(loaded)
    return out, notes


def base_mask_folder(instance_id: str) -> Path | None:
    for run in BASE_MASK_RUNS:
        if (run / _entry(instance_id)).is_file() and (run / "manifest.json").is_file():
            return run
    return None


def spark_result_path(instance_id: str) -> Path | None:
    path = SPARK_RUN / _entry(instance_id)
    return path if path.is_file() else None


def companion_inputs(instance, *, exclude_planners=()) -> tuple[list, list[str]]:
    """Base Glimmer + mask run and the Muse Spark plan for the same instance, content-bound (else skipped + note).

    The plans enter the registry like any import: replayed and fully re-checked when first shown.
    """
    from app.inputs import load_inputs
    out, notes = [], []
    folder = base_mask_folder(instance.id)
    if folder is not None and BASE_MASK_PLANNER not in exclude_planners:
        try:
            loaded = load_inputs(run_folder=folder, entry=_entry(instance.id), selected_instance=instance)
            if loaded.instance != instance:
                raise ValueError("instance differs")
            loaded.metadata["label"] = (loaded.metadata.get("label") or "Base Glimmer + mask") + " (x-ray run)"
            out.append(loaded)
        except (ValueError, OSError) as error:
            notes.append(f"base-mask run not attached: {error}")
    path = spark_result_path(instance.id)
    if path is not None and SPARK_PLANNER not in exclude_planners:
        try:
            loaded = load_inputs(plan_path=path, selected_instance=instance)
            if loaded.instance != instance:
                raise ValueError("instance differs")
            loaded.metadata["label"] = SPARK_LABEL
            out.append(loaded)
        except (ValueError, OSError) as error:
            notes.append(f"Spark plan not attached: {error}")
    return out, notes


# ---------------------------------------------------------------- x-ray legal slots
def legal_slots_for_trace(instance, plan, trace: list[dict]) -> tuple[dict | None, str | None]:
    """Recompute, per decoded action, the slots the mask allowed for the chosen box.

    Replays the plan's actions on the feasibility engine with the run's D79 setting (C2 off) and reads the engine's
    legal set before each action: exactly the set the mask projects tokens from. The recomputed legal-set size must
    equal the trace's recorded ``n_legal`` at every row, else nothing is returned (never a guess).
    """
    if instance.size_class not in LEGAL_SLOT_RUNGS:
        return None, f"legal slots computed for tiny/small only (this is {instance.size_class})"
    from stowage.actions import get_action_space
    from stowage.actions.space import Place
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    space = get_action_space("boxslot")
    try:
        actions = space.from_plan(instance, plan)
        rows = [r for r in trace if r.get("event", "action") == "action" and isinstance((r.get("chosen") or {}).get("action_id"), dict)]
        if len(rows) != len(actions):
            return None, f"trace has {len(rows)} action rows, plan has {len(actions)} actions"
        engine = make_feasibility_engine(instance, space, config=FeasibilityConfig(extra={"global_screen": 0}))
        out = {}
        for row, action in zip(rows, actions):
            chosen = row["chosen"]["action_id"]
            if space.encode(action) != chosen:
                return None, f"trace step {row['step']} action differs from the plan's action"
            legal = list(engine.legal_actions())
            if row.get("n_legal") is not None and len(legal) != row["n_legal"]:
                return None, f"recomputed legal set ({len(legal)}) differs from trace n_legal ({row['n_legal']}) at step {row['step']}"
            if isinstance(action, Place):
                out[row["step"]] = sorted({a.slot for a in legal if isinstance(a, Place) and a.box == action.box})
            engine.push(action)
        return out, None
    except Exception as error:  # report, never guess
        return None, f"legal slots not recomputed: {type(error).__name__}: {error}"


# ---------------------------------------------------------------- Spark as a racer
def spark_entry_from_replay(replay: dict, *, plan_id: str | None, source: str) -> dict:
    from app.comparison import racer_evidence
    from app.stage import stage_counts
    frames = replay.get("frames") or []
    evidence = racer_evidence(replay)
    return {"label": SPARK_LABEL, "plan_id": plan_id,
            "counts": stage_counts(frames[-1]["counts"]) if frames else None,
            "checker_passed": bool((replay.get("checker") or {}).get("passed")),
            "source": source, "file_counts_trusted": False, **evidence,
            "diagnostic": not evidence["eligible"]}  # a racer column, never a source of priced savings


# ---------------------------------------------------------------- Spark without the mask (recorded highlights)
SLOT_RE = re.compile(r"slot (\d{7})")


@lru_cache(maxsize=1)
def _highlights_cached(root: str, stamp: tuple) -> tuple:
    out = []
    base = Path(root)
    appendix = {}
    scores = SPARK_RUN / "scores.jsonl"
    if scores.is_file():
        for line in scores.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            appendix[row.get("instance_id")] = row
    for result in sorted(base.glob("instances/*/result.json")):
        try:
            data = json.loads(result.read_text())
        except (OSError, ValueError):
            continue
        scoring = data.get("scoring") or {}
        failure = scoring.get("failure") or ""
        if not failure.startswith("move engine refused"):
            continue
        response = result.with_name("response.txt")
        if not response.is_file():
            continue
        lines = [x for x in response.read_text().splitlines() if x.strip() and not x.startswith("<|")]
        slot = (SLOT_RE.search(failure) or [None, None])[1]
        offending = [x for x in lines if slot and slot in x]
        with_app = appendix.get(scoring.get("instance_id")) or {}
        moves = with_app.get("moves") or {}
        out.append({
            "instance_id": scoring.get("instance_id"),
            "label": "Muse Spark without the mask (recorded, no rules appendix)",
            "spark_output": lines, "offending_line": offending[0] if offending else None, "slot": slot,
            "engine_refusal": failure,
            "point": (f"Slot {slot} isn't in the mask's legal set. StowLM cannot write a placement the mask forbids · "
                      "the checker still decides." if slot else
                      "StowLM cannot write a placement the mask forbids · the checker still decides."),
            "with_appendix": {"checker_passed": with_app.get("checker_passed"),
                              "weighted_nonproductive": moves.get("weighted_nonproductive")} if with_app else None,
            "source": str(response.relative_to(ROOT)),
            "refusal_source": str(result.relative_to(ROOT)) + " (scoring.failure)",
        })
    return tuple(out)


def spark_highlights(instance_id: str | None = None) -> list[dict]:
    """Recorded move-engine refusals of Spark plans written without the rules appendix (verbatim)."""
    if not SPARK_ABLATION.is_dir():
        return []
    files = sorted(SPARK_ABLATION.glob("instances/*/result.json"))
    stamp = tuple((str(p), p.stat().st_mtime_ns) for p in files)
    rows = [dict(x, same_instance=x["instance_id"] == instance_id) for x in _highlights_cached(str(SPARK_ABLATION), stamp)]
    return sorted(rows, key=lambda x: not x["same_instance"])


# ---------------------------------------------------------------- cached Spark explanations
METHOD_OF_RUN = {"test-exact": "exact", "test-twostep": "twostep", "test-solver": "solver",
                 "tiny": "base-mask", "tiny-rest": "base-mask", "stowlm-showcase": "stowlm",
                 "stowlm-instruction-dg": "stowlm-instruction"}


def plan_method(metadata: dict) -> str | None:
    """Cache method key from the displayed plan's run folder (server-side metadata only)."""
    source = metadata.get("source_path")
    if not source:
        return None
    path = Path(source).resolve()
    if path.name != "result.json" or path.parent.parent.name != "instances":
        return None
    run = path.parent.parent.parent
    if run.parent.name == "base-mask":
        return "base-mask"
    return METHOD_OF_RUN.get(run.name)


def _plan_digest(plan_dict: dict) -> str:
    return hashlib.sha256(json.dumps(plan_dict, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def plan_digest(plan_dict: dict) -> str:
    """SHA-256 of the canonical plan (moves, ballast; provenance excluded)."""
    from stowage.model import MovePlan
    return _plan_digest(MovePlan.model_validate(plan_dict).model_dump(mode="json", exclude={"provenance"}))


def explanation_entry(replay: dict, cache_dir: Path = EXPLAIN_CACHE) -> tuple[dict | None, str | None]:
    """The stored entry for this plan's method and instance id, or (None, reason). Not yet a binding."""
    instance = replay.get("instance") or {}
    method = plan_method(replay.get("metadata") or {})
    if method is None:
        return None, "no explain-cache method for this plan source"
    path = Path(cache_dir) / f"{instance.get('id')}__{method}.json"
    if not path.is_file():
        return None, f"no cached explanation {path.name}"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        return None, f"cache unreadable: {type(error).__name__}: {error}"
    if not isinstance(data, dict):
        return None, "cache unreadable: not an object"
    return {**data, "_path": str(path)}, None


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def explanation_evidence_key(replay: dict, data: dict, baseline: dict | None) -> str:
    """Everything a cached explanation depends on; any change gives a new memo key."""
    from llm.spark.explain import EXPLAINER_VERSION
    metadata = replay.get("metadata") or {}
    return _sha([EXPLAINER_VERSION, data, (replay.get("checker") or {}).get("report"), replay.get("plan"),
                 metadata.get("shippable"), metadata.get("departure_approved"), metadata.get("repaired"),
                 (baseline or {}).get("plan_sha256"), (baseline or {}).get("eligible")])


def explanation_baseline_replay(data: dict, instance) -> dict | None:
    """The entry's own baseline plan, replayed and freshly checked now; stored verdicts are never used."""
    from app.backend import build_replay
    from app.inputs import load_inputs
    from stowage.model import PlannerResult
    run = (ROOT / data["baseline"]["run"]).resolve()
    if not run.is_relative_to(ROOT) or not (run / "result.json").is_file():
        return None
    loaded = load_inputs(plan_path=run / "result.json", selected_instance=instance)
    if loaded.plan is None or loaded.instance != instance:
        return None
    raw = loaded.metadata.get("planner_result")
    return build_replay(loaded.instance, loaded.plan,
                        planner_result=PlannerResult.model_validate(raw) if raw else None)


def _stored_plan_digest(run_text: str) -> str | None:
    run = (ROOT / run_text).resolve()
    if not run.is_relative_to(ROOT) or not (run / "result.json").is_file():
        return None
    stored = json.loads((run / "result.json").read_text())
    return plan_digest((stored.get("result") or stored).get("plan"))


def cached_explanation(replay: dict, cache_dir: Path = EXPLAIN_CACHE, *, baseline_replay: dict | None = None,
                       displayed_baseline: dict | None = None) -> tuple[dict | None, str | None]:
    """The explain-cache entry for this exact plan and baseline, or (None, reason): fail closed to the template.

    Binding (all must hold): same explainer version; same instance id and content; the cache's plan equals the
    displayed plan; the plan and its baseline both pass the ordinary comparison eligibility on fresh evidence; the
    baseline is the entry's own plan on the same instance, and the one the page shows, if any; both complete report
    SHA-256 digests match; and every statement and the whole text re-render unchanged from those reports
    (``stored_explanations.recorded_selection_problem``).
    """
    from llm.spark import explain as spark_explain
    from app.comparison import racer_evidence
    from app.identity import instance_content_digest
    from app.stored_explanations import _digest, recorded_selection_problem
    from stowage.checker import report_matches_inputs
    from stowage.io import load_instance
    from stowage.model import CheckReport, Instance, MovePlan
    data, reason = explanation_entry(replay, cache_dir)
    if data is None:
        return None, reason
    path = Path(data["_path"])
    instance = replay.get("instance") or {}
    try:
        if data.get("instance_id") != instance.get("id"):
            return None, "cache instance id differs"
        if data.get("explainer_version") != spark_explain.EXPLAINER_VERSION:
            return None, f"cached explanation is from explainer {data.get('explainer_version')}"
        inst_file = (ROOT / data["instance_path"]).resolve()
        current = Instance.model_validate(instance)
        if not inst_file.is_relative_to(ROOT) or instance_content_digest(load_instance(inst_file)) != \
                instance_content_digest(current):
            return None, "cache instance content differs from the displayed instance"
        stored_plan = _stored_plan_digest(data["plan"]["run"])
        if stored_plan is None:
            return None, "cache plan run folder missing"
        if stored_plan != plan_digest(replay.get("plan")):
            return None, "cached explanation is about a different plan (plan digest mismatch)"
        evidence = racer_evidence(replay)
        if not evidence["eligible"]:
            return None, "cached text refused: this plan: " + "; ".join(evidence["ineligible_reasons"])
        if baseline_replay is None or not baseline_replay.get("plan"):
            return None, "cached explanation needs its baseline replayed and freshly checked"
        if baseline_replay.get("instance") != instance:
            return None, "cached baseline is on a different instance"
        baseline_digest = plan_digest(baseline_replay["plan"])
        if (baseline_digest != _stored_plan_digest(data["baseline"]["run"])
                or baseline_replay["plan"].get("id") != data["baseline"].get("plan_id")):
            return None, "baseline plan differs from the one the cached explanation quotes"
        if displayed_baseline is not None and displayed_baseline.get("plan_sha256") != baseline_digest:
            return None, "the page's baseline differs from the one the cached explanation quotes"
        base_evidence = racer_evidence(baseline_replay)
        if not base_evidence["eligible"]:
            return None, "cached text refused: baseline: " + "; ".join(base_evidence["ineligible_reasons"])
        plan = MovePlan.model_validate(replay["plan"])
        report = CheckReport.model_validate(replay["checker"]["report"])
        base_plan = MovePlan.model_validate(baseline_replay["plan"])
        base_report = CheckReport.model_validate(baseline_replay["checker"]["report"])
        if data.get("report_sha256") != _digest(report):
            return None, "cached explanation report digest differs from the complete current report"
        if data.get("baseline_report_sha256") != _digest(base_report):
            return None, "cached explanation baseline report digest differs from the complete current report"
        if not (report_matches_inputs(report, current, plan) and report_matches_inputs(base_report, current, base_plan)):
            return None, "cached text refused: a current report is not bound to its plan"
        problem = recorded_selection_problem(data, current, plan, report, (data["baseline"]["label"], base_plan, base_report))
        if problem is not None:
            return None, problem
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return None, f"cache unreadable: {type(error).__name__}: {error}"
    spark = data.get("mode") == "spark"
    return ({"source": "spark-cached" if spark else "template",
             "label": "Muse Spark (cached)" if spark else "Template (cached; Spark not used)",
             "text": data.get("text"), "model": (data.get("spark") or {}).get("model") if spark else None,
             "per_box": {}, "statements": data.get("statements"),
             "cache_file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
             "created_utc": data.get("created_utc"),
             "binding": "explainer version + instance content + both plans (SHA-256; provenance excluded) + shipping "
                        "eligibility of both + both complete reports (SHA-256) + every statement and the text "
                        "re-rendered from both current reports"}, None)

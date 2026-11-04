"""Replay the D64 in-port evaluation of one saved plan IN PLAN ORDER and write a JSON result.

    uv run python experiments/inport-order/measure.py <case_dir> <method> [--resequence] [--out PATH]

Steps (all inside ``rule_freeze_v1(enabled=True)``):
1. Load ``instance.json`` + ``plan.<method>.json`` (raw planner MovePlan, method's own lift order).
2. ``--resequence`` (optional): ``stowage.planner_resequence.resequence(instance, plan)`` -> ``.plan``/``.report``,
   imported lazily. The move multiset fingerprint before/after is recorded so callers can assert moves unchanged.
3. Departure ballast, in this order:
   a. ``planner`` - the plan already carries ballast (exact solver); used as is.
   b. ``finisher`` - ``stowage.solver.ballast.finish_ballast`` (what ``planner.finalize`` calls). Under the switch its
      acceptance already includes the D64 in-port replay, so a plan whose lift order fails in port is normally
      ``not_found`` here.
   c. ``d57_only`` - DIAGNOSTIC: ``stowage.solver.ballast._search_ballast(..., _in_port=None)``, a departure ballast
      passing the D57 departure/arrival physics samples without the in-port acceptance. This is not a production
      path; it only lets us run the full two-pass replay (pass 2 departure fills from k) on plans the finisher
      rejects, so the first failure is located with the same machinery the checker uses.
   d. ``pass1_only`` - no ballast at all: arrival fills + HEEL transfer per state only (``fallback_pass1_only``).
4. ``stowage.checker.in_port.replay(instance, plan_with_ballast)`` (a-c), or the pass-1 evaluator loop (d).
5. Parse per-state margins/metrics into a profile and the first in-port failure.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

common.single_thread_env()

from stowage.rules.catalogue import rule_freeze_v1  # noqa: E402

LIST_KEY = "list_rad"  # D64 P3 list criterion key in stability._in_port_criteria margins/metrics
_MSG = re.compile(r"^\[in_port state=(\d+) k=(\d+) phase=([\w.]+)\] (.*)$", re.S)
HEEL_EPS_KGM = 1.0  # |heel_residual_moment_kgm| above this = HEEL transfer was clipped (authority exhausted)


def _criterion(text, phase):
    if phase.endswith(".strength"):
        return "strength"
    if phase.endswith(".static"):
        return "static_stack"
    if text.startswith("P1:"):
        return "P1_equilibrium"
    head = text.split(" ", 1)[0]
    return head if head.endswith(("_m", "_rad", "_kg")) else head[:40]


def _final_ship(instance, plan):
    from stowage.checker.stability_states import InPortStateBuilder
    from stowage.moves import IllegalMoveError
    builder = InPortStateBuilder(instance)
    states, illegal = [builder.state.ship], None
    for index, move in enumerate(plan.moves):
        try:
            states.append(builder.push(move, move_index=index).ship)
        except IllegalMoveError as exc:
            illegal = dict(move_index=index, message=str(exc)[:500])
            break
    return states, illegal


def _ballast(instance, plan, record, modes):
    """Return (plan_with_ballast or None, mode)."""
    if plan.ballast is not None:
        record["ballast"] = dict(mode="planner", source=plan.ballast.source)
        return plan, "planner"
    info = record.setdefault("ballast", {})
    if "finisher" in modes:
        from stowage.solver.ballast import finish_ballast
        started = time.monotonic()
        try:
            outcome = finish_ballast(instance, plan, time_limit_s=common.FINISHER_LIMIT_S, seed=0)
            in_port = (outcome.parts or {}).get("in_port")
            info["finisher"] = dict(status=outcome.status, wall_s=time.monotonic() - started,
                                    proof=(outcome.proof or "")[:500] if hasattr(outcome, "proof") else None,
                                    in_port_error=(in_port.error or "")[:500] if in_port is not None else None,
                                    in_port_violations=len(in_port.violations) if in_port is not None else None)
            if outcome.status in ("ok", "kept") and outcome.plan is not None and outcome.plan.ballast is not None:
                info["mode"] = "finisher"
                return outcome.plan, "finisher"
        except Exception as exc:
            info["finisher"] = dict(status="exception", message=f"{type(exc).__name__}: {exc}"[:1000],
                                    wall_s=time.monotonic() - started)
    if "d57" in modes:
        from stowage.solver.ballast import _search_ballast
        started = time.monotonic()
        try:
            outcome = _search_ballast(instance, plan, time_limit_s=common.D57_ONLY_LIMIT_S, seed=0)
            info["d57_only"] = dict(status=outcome.status, wall_s=time.monotonic() - started)
            if outcome.status in ("ok", "kept") and outcome.plan is not None and outcome.plan.ballast is not None:
                info["mode"] = "d57_only"
                return outcome.plan, "d57_only"
        except Exception as exc:
            info["d57_only"] = dict(status="exception", message=f"{type(exc).__name__}: {exc}"[:1000],
                                    wall_s=time.monotonic() - started)
    info["mode"] = "pass1_only"
    return None, "pass1_only"


def _rows_from_replay(report):
    """Per-state stability rows {i: {phase: {'margins':..,'metrics':..}}} from a replay PartReport."""
    rows = {}
    pat = re.compile(r"^state:(\d+)\.(arrival|departure)\.(.+)$")
    for source, kind in ((report.metrics, "metrics"), (report.margins, "margins")):
        for key, value in source.items():
            m = pat.match(key)
            if not m:
                continue
            i, phase, name = int(m.group(1)), m.group(2), m.group(3)
            if name.startswith(("strength.", "static.")):
                continue
            rows.setdefault(i, {}).setdefault(phase, {"metrics": {}, "margins": {}})[kind][name] = value
    return rows


def _pass1(instance, states):
    from stowage.checker.in_port import prepare_context
    from stowage.checker.in_port_evaluator import InPortEvaluator
    context = prepare_context(instance, states[-1])
    arrival_context = context.for_phase("arrival")
    ships, metadata = zip(*(context.transferred_ship(ship, phase="arrival") for ship in states))
    with InPortEvaluator(instance, arrival_context, workers=1, warm_start=False) as evaluator:
        reports = list(evaluator.evaluate(ships).reports)
    rows, violations = {}, []
    k = None
    for i, rep in enumerate(reports):
        metrics = dict(rep.metrics)
        metrics.update(metadata[i])
        rows[i] = {"arrival": {"metrics": metrics, "margins": dict(rep.margins)}}
        failed = (not rep.ran) or rep.error is not None or any(v.severity == "hard" for v in rep.violations)
        if failed and k is None:
            k = i
        for v in rep.violations:
            violations.append(dict(state=i, phase="arrival", move_index=i - 1 if i else None,
                                   rule=v.rule_id, message=v.message[:300],
                                   criterion=_criterion(v.message, "arrival")))
        if rep.error and not rep.violations:
            violations.append(dict(state=i, phase="arrival", move_index=i - 1 if i else None,
                                   rule="checker.internal_error", message=rep.error[:300], criterion="internal"))
    return rows, violations, (len(states) if k is None else k)


def _heel_exhausted(metrics):
    resid = metrics.get("heel_residual_moment_kgm")
    remaining = metrics.get("heel_authority_remaining_kgm")
    if resid is None and remaining is None:
        return None
    return bool((resid is not None and abs(resid) > HEEL_EPS_KGM) or (remaining is not None and remaining <= 0.0))


def _profile(rows, k, n_states):
    """Effective series: arrival for i<k, departure for i>=k (if present), else arrival."""
    eff, arr = [], []
    for i in range(n_states):
        r = rows.get(i, {})
        a = r.get("arrival")
        e = (r.get("departure") if i >= k and "departure" in r else a)
        arr.append(a)
        eff.append((("departure" if (i >= k and "departure" in r) else "arrival"), e))

    def list_stats(series):
        vals = [(i, s["margins"].get(LIST_KEY)) for i, s in enumerate(series) if s is not None]
        vals = [(i, v) for i, v in vals if v is not None and math.isfinite(v)]
        if not vals:
            return dict(min_list_margin_rad=None, min_list_state=None, negative_list_states=0, list_states=0)
        i_min, v_min = min(vals, key=lambda t: t[1])
        return dict(min_list_margin_rad=v_min, min_list_state=i_min,
                    negative_list_states=sum(1 for _, v in vals if v < -1e-10), list_states=len(vals))

    def heel_stats(series):
        max_resid, first_exh, n_exh = 0.0, None, 0
        for i, s in enumerate(series):
            if s is None:
                continue
            resid = s["metrics"].get("heel_residual_moment_kgm")
            if resid is not None:
                max_resid = max(max_resid, abs(resid))
            if _heel_exhausted(s["metrics"]):
                n_exh += 1
                if first_exh is None:
                    first_exh = i
        return dict(max_abs_heel_residual_kgm=max_resid, first_heel_exhausted_state=first_exh,
                    heel_exhausted_states=n_exh)

    eff_series = [s for _, s in eff]
    out = {"effective": {**list_stats(eff_series), **heel_stats(eff_series)},
           "arrival": {**list_stats(arr), **heel_stats(arr)}}
    # Arrival-phase criteria that fail at k (pass-1 changeover trigger), from margins.
    if k < n_states and rows.get(k, {}).get("arrival"):
        m = rows[k]["arrival"]["margins"]
        out["arrival_at_k_negative"] = {key: v for key, v in m.items() if v < -1e-10}
        out["arrival_at_k_list_margin_rad"] = m.get(LIST_KEY)
        out["arrival_at_k_heel_exhausted"] = _heel_exhausted(rows[k]["arrival"]["metrics"])
    return out, eff


def _load_resequence():
    """Lazy: the module is written in parallel. Prefer the importable one (snapshot or live tree); if the snapshot
    predates it, load the live repo's file against the snapshot's stowage package and record that."""
    try:
        from stowage.planner_resequence import resequence
        return resequence
    except ImportError:
        import importlib.util
        live = common.ROOT.parent.parent / "src/stowage/planner_resequence.py"
        spec = importlib.util.spec_from_file_location("stowage.planner_resequence", live)
        module = importlib.util.module_from_spec(spec)
        sys.modules["stowage.planner_resequence"] = module
        spec.loader.exec_module(module)
        print(f"note: loaded resequence from live tree {live}", file=sys.stderr)
        return module.resequence


def measure(case_dir, method, *, resequence=False, modes=("finisher", "d57")):
    from stowage.model import Instance, MovePlan
    case_dir = Path(case_dir)
    record = {"case_id": case_dir.name, "method": method, "resequence": bool(resequence)}
    gen = json.loads((case_dir / "generation.json").read_text()) if (case_dir / "generation.json").exists() else {}
    spec = gen.get("spec", {})
    record.update(ship=spec.get("vessel"), rung=spec.get("profile"), port=spec.get("port"),
                  generator=spec.get("generator"), seed=spec.get("seed"), noise=spec.get("noise"))
    started = time.monotonic()
    with rule_freeze_v1(enabled=True):
        instance = Instance.model_validate_json((case_dir / "instance.json").read_text())
        plan = MovePlan.model_validate_json((case_dir / f"plan.{method}.json").read_text())
        record["fingerprint"] = common.fingerprint(plan)
        record["order_sha256"] = common.order_sha(plan)
        if resequence:
            _reseq = _load_resequence()
            t0 = time.monotonic()
            res = _reseq(instance, plan)
            plan = res.plan
            record["resequence_report"] = res.report
            record["resequence_wall_s"] = time.monotonic() - t0
            after = common.fingerprint(plan)
            record["fingerprint_after"] = after
            record["order_sha256_after"] = common.order_sha(plan)
            record["moves_unchanged"] = after["multiset_sha256"] == record["fingerprint"]["multiset_sha256"]
        record["n_moves"] = len(plan.moves)
        kinds = [m.kind for m in plan.moves]
        loaded = {m.container_id for m in plan.moves if m.kind == "load"}
        record["completeness"] = dict(
            loads=len(loaded), load_list=len(instance.load_list),
            discharges=kinds.count("discharge"), discharge_list=len(instance.discharge_list),
            restow_off=kinds.count("restow_off"), restow_on=kinds.count("restow_on"),
            covers_off=kinds.count("hatch_cover_off"), covers_on=kinds.count("hatch_cover_on"))
        c = record["completeness"]
        record["complete"] = bool(c["loads"] == c["load_list"] and c["discharges"] == c["discharge_list"]
                                  and c["restow_off"] == c["restow_on"] and c["covers_off"] == c["covers_on"])
        states, illegal = _final_ship(instance, plan)
        record["illegal_move"] = illegal
        n_states = len(states)
        record["n_states"] = n_states
        t_ballast = time.monotonic()
        ballasted, mode = _ballast(instance, plan, record, modes)
        record["ballast_wall_s"] = time.monotonic() - t_ballast
        record["fallback_pass1_only"] = mode == "pass1_only"
        t_replay = time.monotonic()
        if ballasted is not None:
            from stowage.checker.in_port import replay
            report = replay(instance, ballasted)
            k = int(report.metrics.get("k", n_states)) if report.metrics else n_states
            rows = _rows_from_replay(report)
            violations = []
            for v in report.violations:
                m = _MSG.match(v.message)
                state, phase, text = (int(m.group(1)), m.group(3), m.group(4)) if m else (None, "?", v.message)
                violations.append(dict(state=state, phase=phase, move_index=v.move_index, rule=v.rule_id,
                                       message=text[:300], criterion=_criterion(text, phase),
                                       measured=v.measured, limit=v.limit))
            passed = bool(report.ran and report.error is None and not any(v.severity == "hard"
                                                                          for v in report.violations))
            record["replay_error"] = (report.error or None) and report.error[:1000]
            record["strength_sample_count"] = report.metrics.get("strength_sample_count")
        else:
            rows, violations, k = _pass1(instance, states)
            passed = None  # pass 1 alone cannot decide the D64 verdict (no departure fills)
            record["pass1_all_ok"] = k >= n_states
        record["replay_wall_s"] = time.monotonic() - t_replay
        record["k"] = k
        record["inport_pass"] = passed
        prof, eff = _profile(rows, k, n_states)
        record["profile"] = prof
        # First failure.
        first = None
        if violations:
            v0 = min(violations, key=lambda v: (v["state"] if v["state"] is not None else 10**9))
            phase_root = v0["phase"].split(".")[0]
            row = rows.get(v0["state"], {}).get(phase_root) if v0["state"] is not None else None
            margin = row["margins"].get(v0["criterion"]) if row else None
            first = dict(v0, margin=margin, is_list=v0["criterion"] == LIST_KEY,
                         heel_exhausted=_heel_exhausted(row["metrics"]) if row else None,
                         heel_residual_moment_kgm=row["metrics"].get("heel_residual_moment_kgm") if row else None,
                         heel_authority_remaining_kgm=row["metrics"].get("heel_authority_remaining_kgm") if row else None)
            first["list_with_heel_exhausted"] = bool(first["is_list"] and first["heel_exhausted"])
        record["first_failure"] = first
        by_crit = {}
        for v in violations:
            by_crit.setdefault(v["criterion"], set()).add(v["state"])
        record["failing_states_by_criterion"] = {c: len(s) for c, s in sorted(by_crit.items())}
        record["violations_head"] = violations[:20]
        record["n_violations"] = len(violations)
    record["wall_s"] = time.monotonic() - started
    return record


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("case_dir")
    p.add_argument("method")
    p.add_argument("--resequence", action="store_true")
    p.add_argument("--modes", default="finisher,d57", help="ballast modes to try in order before pass-1 fallback")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    case_dir = Path(args.case_dir)
    if not case_dir.is_absolute() and not case_dir.exists():
        case_dir = common.CASES / args.case_dir
    out = Path(args.out) if args.out else case_dir / f"measure.{args.method}{'.reseq' if args.resequence else ''}.json"
    try:
        record = measure(case_dir, args.method, resequence=args.resequence,
                         modes=tuple(m for m in args.modes.split(",") if m))
    except Exception as exc:
        record = {"case_id": case_dir.name, "method": args.method, "resequence": args.resequence,
                  "error": f"{type(exc).__name__}: {exc}"[:2000], "traceback": traceback.format_exc()[-4000:]}
        common.write_json(out, record)
        print(json.dumps({"case_id": case_dir.name, "method": args.method, "error": record["error"][:300]}))
        return 1
    common.write_json(out, record)
    print(json.dumps({k: record.get(k) for k in ("case_id", "method", "n_moves", "k", "inport_pass", "wall_s")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

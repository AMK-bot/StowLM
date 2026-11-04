"""Build current-epoch cases and raw planner plans for the in-port lift-order experiment.

    uv run python experiments/inport-order/build_cases.py roster
    uv run python experiments/inport-order/build_cases.py gen <case_id>
    uv run python experiments/inport-order/build_cases.py plan <case_id> <scalable|twostep|exact>

``gen`` uses ``stowage.generate.instances_v3.generate`` on specs from ``scenario_specs`` (C2 on for medium is set
by scenario_specs itself when the rule-freeze switch is on). If a spec is rejected by the campaign screens the
next candidate spec of the same slot (same generator/port, next noise/seed) is tried, and the rejection is logged.
``plan`` calls ``planner.plan(instance, seed=0, time_limit_s=...)`` directly (not run_planner): the saved plan is
the method's raw MovePlan in its own lift order, without ballast finishing, checking or repair.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

common.single_thread_env()

from stowage.rules.catalogue import rule_freeze_v1  # noqa: E402


def roster():
    from stowage.generate.instances_v3 import scenario_specs
    with rule_freeze_v1(enabled=True):
        cases = []
        for rung in common.RUNGS:
            specs = scenario_specs(profiles={rung})
            for ship in common.SHIPS:
                mine = [s for s in specs if s["vessel"] == ship]
                ports = list(dict.fromkeys(s["port"] for s in mine))
                per_gen = common.PER_RUNG[rung] // 2
                for gi, generator in enumerate(("random_yard", "simulated_yard")):
                    for j in range(per_gen):
                        port = ports[(gi * per_gen + j) % len(ports)]
                        cands = [s for s in mine if s["generator"] == generator and s["port"] == port]
                        cands = cands[:common.CANDIDATES_PER_SLOT]
                        if not cands:
                            continue
                        case_id = f"{rung}-{ship}-{generator}-{port}"
                        cases.append(dict(case_id=case_id, rung=rung, ship=ship, generator=generator, port=port,
                                          candidates=cands))
        common.write_json(common.ROSTER, {"cases": cases})
    print(f"{len(cases)} cases -> {common.ROSTER}")


def _case(case_id):
    for case in common.load_roster()["cases"]:
        if case["case_id"] == case_id:
            return case
    raise SystemExit(f"unknown case {case_id}")


def gen(case_id):
    from stowage.generate.instances_v3 import generate, CampaignRejection
    case = _case(case_id)
    out = common.CASES / case_id
    out.mkdir(parents=True, exist_ok=True)
    status = {"case_id": case_id, "attempts": []}
    with rule_freeze_v1(enabled=True):
        status["c2_forced_off"] = common.c2_off()
        for spec in case["candidates"]:
            if common.c2_off() and spec.get("c2_screened"):
                spec = {**spec, "c2_screened": False}
            started = time.monotonic()
            try:
                instance, audit = generate(spec)
            except CampaignRejection as exc:
                status["attempts"].append(dict(seed=spec["seed"], noise=spec["noise"], status="rejected",
                                               message=str(exc)[:500], wall_s=time.monotonic() - started))
                continue
            except Exception as exc:  # recorded, next candidate
                status["attempts"].append(dict(seed=spec["seed"], noise=spec["noise"], status="error",
                                               message=f"{type(exc).__name__}: {exc}"[:2000],
                                               wall_s=time.monotonic() - started))
                continue
            (out / "instance.json").write_text(instance.model_dump_json(indent=1) + "\n")
            status.update(status="ok", spec=spec, instance_id=instance.id, n_load=len(instance.load_list),
                          n_discharge=len(getattr(instance, "discharge_list", []) or []),
                          wall_s=time.monotonic() - started, audit_status=audit.get("status"))
            status["attempts"].append(dict(seed=spec["seed"], noise=spec["noise"], status="ok",
                                           wall_s=time.monotonic() - started))
            common.write_json(out / "generation.json", status)
            print(json.dumps({k: status[k] for k in ("case_id", "instance_id", "n_load", "wall_s")}))
            return 0
    status["status"] = "failed"
    common.write_json(out / "generation.json", status)
    print(f"{case_id}: all candidates failed", file=sys.stderr)
    return 1


def plan(case_id, method):
    from stowage.model import Instance
    case = _case(case_id)
    out = common.CASES / case_id
    path = out / "instance.json"
    if not path.exists():
        raise SystemExit(f"{case_id}: no instance.json")
    limit = common.TIME_LIMITS[method][case["rung"]]
    record = {"case_id": case_id, "method": method, "time_limit_s": limit, "seed": 0,
              "global_screen": common.global_screen()}
    with rule_freeze_v1(enabled=True):
        instance = Instance.model_validate_json(path.read_text())
        planner = common.make_planner(method)
        record["planner"] = f"{planner.name}@{getattr(planner, 'version', '?')}"
        started = time.monotonic()
        try:
            result = planner.plan(instance, seed=0, time_limit_s=limit)
        except Exception as exc:
            record.update(status="exception", message=f"{type(exc).__name__}: {exc}"[:4000],
                          traceback=traceback.format_exc()[-4000:], wall_s=time.monotonic() - started)
            common.write_json(out / f"plan.{method}.status.json", record)
            return 1
        record.update(status=result.status, message=(result.message or "")[:4000], wall_s=time.monotonic() - started,
                      has_plan=result.plan is not None,
                      n_moves=len(result.plan.moves) if result.plan is not None else None,
                      has_ballast=result.plan is not None and result.plan.ballast is not None,
                      metrics={k: v for k, v in (result.metrics or {}).items()
                               if isinstance(v, (int, float, str, bool)) or v is None})
        if result.plan is not None:
            (out / f"plan.{method}.json").write_text(result.plan.model_dump_json(indent=1) + "\n")
            record["fingerprint"] = common.fingerprint(result.plan)
    common.write_json(out / f"plan.{method}.status.json", record)
    print(json.dumps({k: record.get(k) for k in ("case_id", "method", "status", "n_moves", "wall_s")}))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("roster")
    g = sub.add_parser("gen")
    g.add_argument("case_id")
    q = sub.add_parser("plan")
    q.add_argument("case_id")
    q.add_argument("method", choices=("scalable", "twostep", "exact"))
    args = p.parse_args(argv)
    if args.cmd == "roster":
        return roster()
    if args.cmd == "gen":
        return gen(args.case_id)
    return plan(args.case_id, args.method)


if __name__ == "__main__":
    sys.exit(main() or 0)

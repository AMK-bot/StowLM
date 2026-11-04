"""Break-it worker: one process holds the sandbox plan and answers drags, JSON lines over stdin and stdout.

    python -m app.v6_sandbox_worker --role fast|full

    in   {"id": 7, "op": "try", "box": "...", "slot": "..."}      fast role: fast_screen; full role: try_move
         {"id": 8, "op": "cached", "box": "...", "slot": "..."}   the recorded answer for this exact input, or null
         {"id": 9, "op": "explain", "box": "...", "lang": "th", "slot": null}
    out  {"ready": true, "pid": 123, "load_s": 4.1}
         {"id": 7, "ok": true, "verdict": {...}, "explanation": {...}, "wall_s": 0.7}
         {"id": 7, "ok": false, "error": "..."}

No model, no network. The instance and plan load once. A worker leaves when stdin closes or its parent goes away.

The explanation cache is bound to the hero as computed, not to the demo's derivative of it. So a box answers from the hero's own
inputs unless the disclosed edit changed that box; that box, and every move, is answered on the demo's exact inputs. Moves reuse a
verdict already in hand, recorded or from this worker, so asking about one never starts a second check.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inputs() -> dict:
    """The derivative plan, plus its recorded baseline report and first prepared drag when the demo fixture still has them."""
    from pydantic import ValidationError
    from app.v6_sandbox import first_case, read_demo, source_path
    from stowage.model import CheckReport, Instance, MovePlan
    from stowage.checker import report_matches_inputs
    demo = read_demo()
    source = json.loads(source_path(demo).read_text())
    instance, plan = Instance.model_validate(source["instance"]), MovePlan.model_validate(source["plan"])
    try:
        report = CheckReport.model_validate(source["report"] if "report" in source else demo.get("baseline_report"))
    except ValidationError:
        report = None
    if report is not None and not report_matches_inputs(report, instance, plan):
        report = None  # no explanations on these inputs; the drags still run
    cases = demo.get("cases")
    return {"instance": instance, "plan": plan, "report": report, "probe": first_case(demo), "cases": cases if isinstance(cases, list) else [],
            "checked_utc": demo.get("created_utc")}


def hero_world(world: dict) -> dict | None:
    """The inputs the explanation cache is bound to, if they're the configured hero's. Boxes the demo edited are listed in ``edited``."""
    if "hero" not in world:
        world["hero"] = _load_hero(world)
    return world["hero"]


def _load_hero(world: dict) -> dict | None:
    from app.v6_sandbox import EXPLAIN_HERO, configured_hero
    from stowage.model import CheckReport, Instance, MovePlan
    hero = configured_hero()
    try:
        data = json.loads((EXPLAIN_HERO / "hero-inputs.json").read_text())
        checked = json.loads((EXPLAIN_HERO / "verification.json").read_text()).get("created_utc")
    except (OSError, ValueError):
        return None
    if not hero or data.get("binding", {}).get("instance_sha256") != hero.get("instance_sha256"):
        return None
    instance = Instance.model_validate(data["instance"])
    ours = {box.id: box for box in world["instance"].containers}
    return {"instance": instance, "plan": MovePlan.model_validate(data["plan"]), "report": CheckReport.model_validate(data["report"]), "checked_utc": checked,
            "edited": {box.id for box in instance.containers if ours.get(box.id) != box}}


def cached_drag(world: dict, box: str, slot: str) -> dict | None:
    from app.v6_sandbox import cache_requests
    if (box, slot) not in cache_requests():
        return None
    from experiments.mvp.pipeline.sandbox.demo_cache import lookup_cached_drag
    recorded = lookup_cached_drag(world["instance"], world["plan"], box, slot)
    if recorded:
        recorded.pop("canonical_call", None)  # the audit's raw report: big, and nothing here reads it
    return recorded


def recorded_move(world: dict, box: str, slot: str) -> tuple[dict, object] | None:
    """A full verdict recorded on these exact inputs, and when it was checked."""
    for case in world["cases"]:
        if isinstance(case, dict) and (case.get("box_id"), case.get("target_slot")) == (box, slot) and isinstance(case.get("verdict"), dict):
            return case["verdict"], (case.get("fast_recording") or {}).get("created_utc") or world.get("checked_utc")
    cached = cached_drag(world, box, slot)
    return (cached["full"], cached.get("checked_utc")) if cached else None


def explained(world: dict, request: dict) -> dict:
    from experiments.mvp.pipeline.explain import explain_box, explain_move
    box, lang, target = request["box"], request["lang"], request.get("slot")
    ours = (world["instance"], world["plan"], world["report"])
    if target:
        if world["report"] is None:
            raise ValueError("no checker report for these inputs")
        recorded = recorded_move(world, box, target)
        verdict = recorded[0] if recorded else world.get("move_verdicts", {}).get((box, target))
        result = explain_move(*ours, box, target, verdict, language=lang) if verdict is not None else explain_box(*ours, box, language=lang, what_if=target)
        return {"explanation": result, "basis": {"move": "recorded" if recorded else "live", "checked_utc": recorded[1] if recorded else None}}
    hero = hero_world(world)
    if hero and box not in hero["edited"]:
        result = explain_box(hero["instance"], hero["plan"], hero["report"], box, language=lang)
        return {"explanation": result, "basis": {"about": "hero", "checked_utc": hero["checked_utc"]}}
    if world["report"] is None:
        raise ValueError("no checker report for these inputs")
    return {"explanation": explain_box(*ours, box, language=lang), "basis": {"about": "edited" if hero else "demo"}}


def answer(world: dict, role: str, request: dict) -> dict:
    from experiments.mvp.pipeline import sandbox
    started = time.perf_counter()
    if request["op"] == "cached":
        return {"recorded": cached_drag(world, request["box"], request["slot"]), "wall_s": time.perf_counter() - started}
    if request["op"] == "explain":
        said = explained(world, request)
        return {**said, "explanation": said["explanation"].model_dump(mode="json"), "wall_s": time.perf_counter() - started}
    screen = sandbox.fast_screen if role == "fast" else sandbox.try_move
    verdict = screen(world["instance"], world["plan"], request["box"], request["slot"])
    if role == "full":
        saved = world.setdefault("move_verdicts", {})
        saved[(request["box"], request["slot"])] = verdict
        while len(saved) > 24:
            saved.pop(next(iter(saved)))
    return {"verdict": verdict.model_dump(mode="json"), "explanation": sandbox.explain_verdict(verdict).model_dump(mode="json"),
            "wall_s": time.perf_counter() - started}


def _orphan_watch(parent: int) -> None:
    while os.getppid() == parent:
        time.sleep(2)
    os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("fast", "full"), required=True)
    role = parser.parse_args().role
    reply = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")  # library prints must never reach the protocol
    os.dup2(2, 1)
    threading.Thread(target=_orphan_watch, args=(os.getppid(),), daemon=True).start()
    started = time.perf_counter()
    world = inputs()
    from experiments.mvp.pipeline import sandbox
    import stowage.checker  # noqa: F401 - the full check's imports count against launch, not the first drag
    if role == "fast":
        hero_world(world)
    if world["probe"]:
        sandbox.fast_screen(world["instance"], world["plan"], world["probe"]["box_id"], world["probe"]["target_slot"])
    reply.write(json.dumps({"ready": True, "pid": os.getpid(), "load_s": round(time.perf_counter() - started, 3)}) + "\n")
    for line in sys.stdin:
        request = json.loads(line)
        try:
            out = {"id": request["id"], "ok": True, **answer(world, role, request)}
        except Exception as error:  # noqa: BLE001 - an error is an answer; the server shows it as one
            out = {"id": request["id"], "ok": False, "error": f"{type(error).__name__}: {error}"}
        reply.write(json.dumps(out, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()

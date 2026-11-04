"""Build the Functional app's sample call views and runner outcomes (synthetic, deterministic).

    uv run python app/tests/fixtures/functional/build_fixtures.py

Writes, beside this file:
  outcomes/<name>/run/outcome.json   small runner outcomes in run_packet's shape (l0, relaxed-l3, no-plan, failed-l0)
  planviews/<name>.json              app.functional_view.plan_view of the drawable outcomes
  live/                              call views and planner payloads captured from a real Functional app call
                                     (synthetic case, no model stages; not written by this script)

Everything here is invented for tests: a 4-bay toy section, made-up box ids and hashes. No real data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))

H = lambda c: c * 64  # noqa: E731 - fake digest
CALL = "c20261103-101500-a1b2c3"
LIFTS_RULE = "expert.e2.weight_order"


def instance(load: list[str]) -> dict:
    cells = []
    for bay in (1, 3, 5, 7):
        for row in (1, 2, 3, 4):
            for tier in (2, 4, 82, 84):
                cells.append({"id": f"{bay:03d}{row:02d}{tier:02d}", "reefer_plug": tier == 82 and row == 1})
    pods = ["SGSIN", "MYPKG"]
    containers = []
    for i, cid in enumerate(load):
        containers.append({"id": cid, "length_ft": 40 if i % 5 == 4 else 20, "gross_mass_kg": 8000 + 1500 * (i % 9),
                           "pod": pods[i % 2], "reefer": i % 7 == 3, "empty": i % 11 == 10,
                           "dg": {"imdg_class": "3", "un_number": "1263"} if i % 6 == 2 else None})
    return {"id": "fa-toy.small-feeder_500-s91000017", "size_class": "small", "stage": "A",
            "vessel": {"id": "feeder_500.small", "name": "feeder_500.small", "nominal_teu": 64, "cells": cells,
                       "hatch_covers": [{"id": "HC-1", "bays": [1, 3]}, {"id": "HC-2", "bays": [5, 7]}]},
            "rotation": {"calls": [{"locode": "THLCH"}, {"locode": "SGSIN"}, {"locode": "MYPKG"}]},
            "onboard": {"placements": []}, "containers": containers, "load_list": list(load)}


def moves(load: list[str], inst: dict, ashore: set[str] = frozenset()) -> list[dict]:
    sizes = {c["id"]: c["length_ft"] for c in inst["containers"]}
    out = [{"kind": "hatch_cover_off", "cover_id": "HC-1", "dst": "quay"}]
    free20 = [(b, r, t) for b in (1, 3, 5, 7) for t in (2, 4, 82, 84) for r in (1, 2, 3, 4)]
    used = set()
    for cid in load:
        if cid in ashore:
            continue
        if sizes[cid] == 40:
            spot = next(((b, r, t) for (b, r, t) in free20 if b in (1, 5) and (b, r, t) not in used and (b + 2, r, t) not in used), None)
            if spot is None:
                continue
            b, r, t = spot
            used |= {(b, r, t), (b + 2, r, t)}
            out.append({"kind": "load", "container_id": cid, "src": "B001-001-01-01", "dst": f"{b + 1:03d}{r:02d}{t:02d}"})
        else:
            spot = next(s for s in free20 if s not in used)
            used.add(spot)
            b, r, t = spot
            out.append({"kind": "load", "container_id": cid, "src": "B001-001-01-01", "dst": f"{b:03d}{r:02d}{t:02d}"})
    out.append({"kind": "hatch_cover_on", "cover_id": "HC-1"})
    return out


def outcome(kind: str) -> dict:
    load = [f"QZXU{n:07d}" for n in range(1, 25)]
    inst = instance(load)
    base = {"label": "live", "synthetic": True, "instance": inst, "instance_sha256": H("1"), "packet_sha256": H("2"),
            "events": [], "warnings": [], "receipt": {"plan_sha256": H("3"), "checker": {"passed": kind == "l0"}},
            "rules": [{"sentence": "Nothing goes into bay seven on this call.", "confirmed": True,
                       "reading": {"constraint": {"type": "close_bay", "bay": 7}}, "recheck": {"passed": True, "violations": []}}],
            "first_checked_wall_s": 11.4, "wall_s": 62.0, "timing": {"wall_s": 62.0, "first_checked_wall_s": 11.4}}
    if kind in ("l0", "failed-l0"):
        plan = {"id": "toy.plan", "moves": moves(load, inst)}
        base.update(status="checked_plan", passed=True, level=0, plan=plan, broken_rules=[], boxes_ashore=[], lifts=6.0,
                    checker={"passed": True, "violations": []},
                    message="Plan passed the full checker and every confirmed instruction recheck. First legal plan: 11.4 s; total wall time: 62.0 s.",
                    evidence={"boxes_total": len(load), "instruction_recheck_passed": True, "level": 0})
        if kind == "failed-l0":  # a corrupt outcome: says checked_plan, but the checker and the recheck failed
            base["checker"] = {"passed": False, "violations": [{"rule": "iscode.loading.gm"}]}
            base["rules"][0]["recheck"] = {"passed": False, "violations": ["bay 07"]}
    elif kind == "relaxed-l3":
        ashore = {load[-1], load[-2]}
        plan = {"id": "toy.plan.relaxed", "moves": moves(load, inst, ashore)}
        base.update(status="relaxed_plan", passed=False, level=3, plan=plan,
                    broken_rules=[LIFTS_RULE, "objective.all_loaded"], boxes_ashore=sorted(ashore), lifts=9.0,
                    checker={"passed": False, "violations": [{"rule": LIFTS_RULE}, {"rule": "objective.all_loaded"}]},
                    message="Partial relaxed plan at L3; 91.7% aboard; full checker pass: false. Broken rules: expert.e2.weight_order, objective.all_loaded. Boxes ashore: 2.",
                    evidence={"boxes_total": len(load), "level": 3,
                              "completion": {"level": 3, "completed": False, "boxes_aboard_percent": 91.6667,
                                             "boxes_left_ashore": sorted(ashore)}})
    else:
        base.update(status="no_checked_plan", passed=False, level=None, plan=None, broken_rules=[], boxes_ashore=[], lifts=None,
                    checker=None, first_checked_wall_s=None,
                    message="Runner engine deadline reached; no checked plan was found. Retry with a longer deadline or reduce the load.",
                    evidence={"boxes_total": len(load)})
    return base


AVAIL_ON = {
    "spark": {"available": True, "reason": "", "who": "Muse Spark", "where": "Meta Model API, cloud", "model": "muse-spark-1.3"},
    "voice": {"available": True, "reason": "", "who": "Muse Voice Transcribe", "where": "Meta Model API, cloud", "model": "muse-voice-transcribe-1.0"},
    "reader": {"available": True, "reason": "", "who": "StowLM Reader", "where": "fine-tuned Muse Glimmer 30B (LoRA), on this laptop (MLX)",
               "model": "muse-glimmer-30b", "adapter_path": "models/reader/adopted", "adapter_sha256": H("e"), "adopted": True,
               "decode": "schema mask (v3 decoder)", "loaded": False},
}
AVAIL_OFF = {
    "spark": {**AVAIL_ON["spark"], "available": False, "reason": "MODEL_API_KEY isn't set in this launch"},
    "voice": {**AVAIL_ON["voice"], "available": False, "reason": "MODEL_API_KEY isn't set in this launch"},
    "reader": {**AVAIL_ON["reader"], "available": False, "reason": "mlx_lm isn't installed in this environment"},
}

REQUEST = {"ship": "feeder_500", "size_class": "small", "fill_pct": 60, "generator": "random_yard", "port": "laem_chabang_like", "seed": 91000017}
ENV_IN = {"in_envelope": True, "size_class": "small", "reasons": [],
          "numbers": {"boxes_to_load": 24, "box_limit": 195, "physical_cell_fill": 0.47, "fill_limit": 0.7, "ship": "feeder_500",
                      "planned_region": "section", "onboard_boxes": 0, "loading_calls": 1}}
ENV_OUT = {"in_envelope": False, "size_class": "medium", "reasons": [
    {"test": "fill", "measured": 0.812, "limit": 0.7, "message": "Physical fill 81.200% (203/250 cells in the planned half-ship section); limit 70%."},
    {"test": "size", "measured": 203, "limit": 195, "message": "203 boxes to load; limit 195 boxes in the planned section."}],
    "numbers": {"boxes_to_load": 203, "box_limit": 195, "physical_cell_fill": 0.812, "fill_limit": 0.7}}
CASE = {"schema": "stowlm.functional-case/1", "request": REQUEST, "seed": 91000017, "seed_source": "planner",
        "seed_status": {"seed": 91000017, "known_in": [], "unseen": True, "sealed": False},
        "instance_sha256": H("1"), "file_sha256": H("4"), "instance_id": "fa-toy.small-feeder_500-s91000017",
        "ship": "feeder_500", "size_class": "small", "boxes": 24, "teu": 29, "fill": 0.47, "ports": ["THLCH", "SGSIN", "MYPKG"],
        "generator": {"producer": "experiments/mvp/corpus_tools/build.py", "producer_sha256": H("5"), "selection": "maps/generate.py fill/195 trim", "module_sha256": H("6")},
        "envelope": ENV_IN, "wall_s": 3.21, "created_utc": "2026-11-03T10:15:03Z", "label": "Synthetic ship and cargo, simulated port",
        "papers": [{"path": "papers/generated/load-list.csv", "media_type": "text/csv", "label": "Synthetic document"},
                   {"path": "papers/generated/dg-declaration.txt", "media_type": "text/plain", "label": "Synthetic document"}]}
REF = "SYN-0f1e2d3c4b5a6978"
GENERATED = [("booking-note.txt", "booking", "text/plain"), ("shipping-instructions.csv", "shipping_instructions", "text/csv"),
             ("vgm.csv", "vgm", "text/csv"), ("dg-declaration.txt", "dg_declaration", "text/plain"), ("gate-slip.txt", "gate_slip", "text/plain"),
             ("reefer-list.csv", "reefer_list", "text/csv"), ("oog-list.txt", "oog_list", "text/plain"), ("load-list.csv", "unspecified", "text/csv")]
DOCS = [{"name": name, "media_type": media, "bytes": 600 + 37 * i, "sha256": H("7"), "origin": "generated", "role": role,
         "booking_ref": REF, "complete_booking_list": role in ("dg_declaration", "reefer_list", "oog_list"), "synthetic": True,
         "label": "Synthetic document"} for i, (name, role, media) in enumerate(GENERATED)]
CASE["papers"] = [{"id": "generated-" + d["role"], "name": d["name"], "path": "papers/generated/" + d["name"], "role": d["role"],
                   "booking_ref": REF, "complete_booking_list": d["complete_booking_list"], "synthetic": True,
                   "media_type": d["media_type"], "label": "Synthetic document"} for d in DOCS]
ROWS = [{"id": c["id"], "length_ft": c["length_ft"], "gross_mass_kg": c["gross_mass_kg"], "pod": c["pod"], "reefer": c["reefer"], "dg": c["dg"]}
        for c in instance([f"QZXU{n:07d}" for n in range(1, 25)])["containers"]]


def stage(name, status, **kw):
    row = {"stage": name, "status": status, "mode": "live", "who": None, "where": None, "model": None, "adapter_sha256": None,
           "started_utc": "2026-11-03T10:15:00Z", "wall_s": None, "cost_usd": None, "input_sha256": None, "output_sha256": None, "notes": None}
    row.update(kw)
    return row


GEN = stage("generate", "done", mode="generated", who="Case generator", where="this laptop, software", wall_s=3.21, output_sha256=H("1"))
ENV = stage("envelope", "done", who="Envelope gate", where="software", wall_s=0.02, notes="Inside the envelope.")
INTAKE = stage("intake", "done", who="Muse Spark", where="Meta Model API, cloud", model="muse-spark-1.3", wall_s=8.7, cost_usd=0.0042, input_sha256=H("7"), output_sha256=H("9"))
ASM = stage("assemble", "done", who="Runner assemble", where="software", wall_s=0.4)
READ = stage("reader", "done", who="StowLM Reader", where="this laptop", model="muse-glimmer-30b", adapter_sha256=H("e"), wall_s=4.1)
CONF = stage("confirm", "done", who="Planner", where="this laptop", input_sha256=H("a"), output_sha256=H("b"), notes="The planner confirmed this exact sentence and rule.")
ENG = stage("engine", "done", who="Frontier Engine 0.13.0", where="this laptop", wall_s=58.2, notes="Admission: waived (no fleet telemetry).")
RREAD = stage("reader", "done", who="StowLM Reader", where="this laptop", mode="live", wall_s=0.01, notes=None)
CHK = stage("checker", "done", who="Full checker", where="software", wall_s=2.9)
LAD = stage("ladder", "done", who="Ladder", where="software", wall_s=0.1)
PKG = stage("package", "done", who="Package writer", where="software", wall_s=0.6)

READING = {"mode": "live", "status": "constraint_ready", "sentence": "Nothing goes into bay seven on this call.",
           "answer": {"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 7}},
           "rule_text": "Nothing loads into bay 07.", "refusal": None,
           "evidence": {"adapter_sha256": H("e"), "model": "muse-glimmer-30b", "decode": "schema mask (v3 decoder)", "wall_s": 4.1, "cold_load_s": 38.5},
           "id": "r0a1b2c3d4e5f", "origin": "typed", "confirmed": True, "sentence_sha256": H("a"), "rule_sha256": H("b")}
REFUSED = {"mode": "live", "status": "refused", "sentence": "Make the ship go faster.", "answer": {"refusal": {"code": "out_of_scope"}},
           "rule_text": None, "refusal": {"code": "out_of_scope", "reason": "the sentence isn't a stowage instruction for this call"},
           "evidence": {"adapter_sha256": H("e"), "model": "muse-glimmer-30b", "wall_s": 2.2, "cold_load_s": None},
           "id": "r111111111111", "origin": "typed", "confirmed": False, "sentence_sha256": H("c"), "rule_sha256": None}


def view(state, message, **kw):
    v = {"id": CALL, "created_utc": "2026-11-03T10:15:00Z", "label": "Synthetic ship and cargo, simulated port", "app": "functional",
         "state": state, "message": message, "request": REQUEST, "case": None, "envelope": None,
         "papers": {"documents": [], "intake": None}, "load_list": {"origin": "generator", "boxes": None, "teu": None, "rows": []},
         "voice": None, "readings": [], "run": None, "outcome": None, "stages": [], "availability": AVAIL_ON}
    v.update(kw)
    return v


def outcome_view(o, files=True):
    return {"status": o["status"], "passed": o["passed"], "level": o["level"], "label": o["label"], "message": o["message"]
            + ("" if o["status"] in ("checked_plan", "relaxed_plan") else " No checked plan found. That isn't a proof that none exists."),
            "broken_rules": o["broken_rules"], "boxes_ashore": o["boxes_ashore"], "lifts": o["lifts"],
            "first_checked_wall_s": o["first_checked_wall_s"], "wall_s": o["wall_s"],
            "active_rules": ["rule.%02d" % i for i in range(41)] if o["plan"] else None, "inactive_rules": [],
            "receipt_sha256": H("d") if o["plan"] else None,
            "package_files": ["bay-plan.html", "crane-sequence.csv", "note-to-master.md", "package.json", "receipt.json"] if files and o["plan"] else []}


def build() -> None:
    from app.functional_view import plan_view

    outcomes = {name: outcome(name) for name in ("l0", "relaxed-l3", "no-plan", "failed-l0")}
    for name, data in outcomes.items():
        target = HERE / "outcomes" / name / "run" / "outcome.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
        if data["plan"] is not None:  # the package receipt is where rule coverage lives (rule_coverage.py)
            receipt = target.parent / "package" / "receipt.json"
            receipt.parent.mkdir(exist_ok=True)
            receipt.write_text(json.dumps({"checker": {"active_rules": ["rule.%02d" % i for i in range(41)], "inactive_rules": []}}) + "\n")
    planviews = {}
    for name in ("l0", "relaxed-l3"):
        planviews[name] = plan_view(HERE / "outcomes" / name)
        planviews[name]["call_id"] = CALL
    ll = {"origin": "generator", "boxes": 24, "teu": 29, "rows": ROWS}
    ready_common = dict(case=CASE, envelope=ENV_IN, papers={"documents": DOCS, "intake": {"skipped": True, "why": "The planner chose the generated load list, not read by a model."}},
                        load_list=ll)
    skip = stage("intake", "skipped", mode="skipped", who="Muse Spark", where="cloud", notes="The planner chose the generated load list, not read by a model.")
    run_done = {"status": "done", "profile": "live", "engine_deadline_s": 720, "elapsed_s": 62.0,
                "admission": {"waived": "no fleet telemetry", "load1": 3.12},
                "incumbents": [{"t_s": 11.4, "lifts": 9.0, "plan_sha256": H("f"), "lane": "lp_first"},
                               {"t_s": 40.2, "lifts": 6.0, "plan_sha256": H("3"), "lane": "lns"}], "events_tail": []}
    views = {
        "generating": view("generating", "Generating a fresh synthetic case.", stages=[stage("generate", "running", mode="generated", who="Case generator", where="software")]),
        "needs-papers-unavailable": view("needs_papers", "Choose cargo papers or the generated load list.", case=CASE, envelope=ENV_IN,
                                         papers={"documents": DOCS, "intake": None}, load_list=ll, stages=[GEN, ENV], availability=AVAIL_OFF),
        "needs-papers": view("needs_papers", "Choose cargo papers or the generated load list.", case=CASE, envelope=ENV_IN,
                             papers={"documents": DOCS, "intake": None}, load_list=ll, stages=[GEN, ENV]),
        "needs-answers": view("needs_answers", "Muse Spark has questions about the papers.", case=CASE, envelope=ENV_IN,
                              papers={"documents": DOCS + [{"name": "booking-amendment.txt", "media_type": "text/plain", "bytes": 902, "sha256": H("0"), "origin": "pasted",
                                                         "role": "amendment", "booking_ref": REF, "complete_booking_list": False, "synthetic": None, "label": None}],
                                      "intake": {"mode": "live", "rows": ROWS, "questions": [
                                          {"id": "q1", "text": "Box QZXU0000003 has no gross mass on the papers. What is it in kg?", "field": "gross_mass_kg", "box": "QZXU0000003"},
                                          {"id": "q2", "text": "Is QZXU0000004 a reefer?", "field": "reefer", "box": "QZXU0000004", "choices": ["yes", "no"]}],
                                          "conflicts": [], "complete": False, "boxes": 24, "model": "muse-spark-1.3", "wall_s": 8.7, "cost_usd": 0.0042, "receipt_sha256": H("9")}},
                              load_list={**ll, "origin": "intake"}, stages=[GEN, ENV, INTAKE]),
        "out-of-envelope": view("out_of_envelope", "This call is outside the pipeline envelope.", case={**CASE, "envelope": ENV_OUT, "size_class": "medium", "boxes": 203, "fill": 0.812},
                                envelope=ENV_OUT, stages=[GEN, stage("envelope", "failed", who="Envelope gate", where="software", notes="Outside: fill, size.")]),
        "ready-unavailable": view("ready", "Ready to plan.", **ready_common, stages=[GEN, ENV, skip, ASM,
                                  stage("reader", "unavailable", mode="unavailable", who="StowLM Reader", where="this laptop", notes="mlx_lm isn't installed in this environment")],
                                  availability=AVAIL_OFF),
        "ready-reading": view("ready", "Ready to plan.", **ready_common, readings=[REFUSED, {**READING, "confirmed": False}],
                              voice={"mode": "live", "transcript": "Nothing goes into bay seven on this call.", "model": "muse-voice-transcribe-1.0",
                                     "audio_seconds": 3.4, "audio_sha256": H("6"), "wall_s": 1.9, "cost_usd": 0.0011, "origin": "browser"},
                              stages=[GEN, ENV, skip, ASM, stage("voice", "done", who="Muse Voice Transcribe", where="cloud", wall_s=1.9, cost_usd=0.0011),
                                      stage("reader", "refused", who="StowLM Reader", where="this laptop", wall_s=2.2, notes="the sentence isn't a stowage instruction for this call"), READ]),
        "planning": view("planning", "Engine 0.13.0 is searching.", **ready_common, readings=[READING],
                         run={"status": "running", "profile": "live", "engine_deadline_s": 720, "elapsed_s": 41.3,
                              "admission": {"waived": "no fleet telemetry", "load1": 3.12}, "incumbents": run_done["incumbents"], "events_tail": []},
                         stages=[GEN, ENV, skip, ASM, READ, CONF, stage("engine", "running", who="Frontier Engine 0.13.0", where="this laptop")]),
        "planning-searching": view("planning", "Engine 0.13.0 is searching.", **ready_common,
                                   run={"status": "running", "profile": "standard", "engine_deadline_s": 1800, "elapsed_s": 4.0,
                                        "admission": None, "incumbents": [], "events_tail": []},
                                   stages=[GEN, ENV, skip, ASM, stage("engine", "running", who="Frontier Engine 0.13.0", where="this laptop")]),
        "done-l0": view("done", "Plan passed the full checker.", **ready_common, readings=[READING], run=run_done,
                        outcome=outcome_view(outcomes["l0"]), stages=[GEN, ENV, skip, ASM, READ, CONF, ENG, RREAD, CHK, LAD, PKG]),
        "done-relaxed-l3": view("done", "Relaxed plan at L3.", **ready_common, readings=[READING], run=run_done,
                                outcome=outcome_view(outcomes["relaxed-l3"]), stages=[GEN, ENV, skip, ASM, READ, CONF, ENG, RREAD, CHK, LAD, PKG]),
        "done-no-plan": view("done", "No checked plan.", **ready_common, run={**run_done, "incumbents": []},
                             outcome=outcome_view(outcomes["no-plan"]), stages=[GEN, ENV, skip, ASM, ENG]),
    }
    (HERE / "planviews").mkdir(exist_ok=True)
    for name, data in planviews.items():
        (HERE / "planviews" / f"{name}.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    del views  # kept as the reference call-view shapes; live/ holds call views captured from a real call


if __name__ == "__main__":
    build()
    print("wrote fixtures under", HERE.relative_to(ROOT))

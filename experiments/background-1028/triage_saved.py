"""Read-only diagnosis of the retained background acceptance fixture."""
from dataclasses import asdict
import json
from pathlib import Path
from time import perf_counter

from stowage.checker import check
from stowage.generate import background_acceptance as gate
from stowage.io import load_instance
from stowage.model import MovePlan, BallastCondition
from stowage.vessel.fleet_conditions import empty_background_witness_tank_masses

HERE = Path(__file__).resolve().parent
instance = load_instance(HERE / "review-fixes/retry-random-feeder_800-seed0.instance.json")
background = gate.background_instance(instance)
recipe = empty_background_witness_tank_masses(background.vessel)
background.onboard.tank_masses_kg.update(recipe)
adjustable = {tank.id for tank in background.vessel.tanks if tank.adjustable}
plan = MovePlan(id="triage-empty", instance_id=background.id, call_index=background.call_index,
    moves=[], ballast=BallastCondition(source="planner",
        tank_masses_kg={key: value for key, value in recipe.items() if key in adjustable}))
started = perf_counter()
report = check(background, plan)
payload = dict(check_seconds=perf_counter()-started, report=report.model_dump(mode="json"))
(HERE / "saved-triage-direct.json").write_text(json.dumps(payload, indent=2) + "\n")
print(json.dumps(dict(stage="direct", seconds=payload["check_seconds"], passed=report.passed,
    failures={name: part.model_dump(mode="json") for name, part in report.parts.items()
              if not part.ran or part.error or part.violations})), flush=True)
gate.clear_background_cache()
result = gate.evaluate_background(instance)
(HERE / "saved-triage-gate.json").write_text(json.dumps(asdict(result), indent=2) + "\n")
print(json.dumps(dict(stage="gate", accepted=result.accepted, reasons=result.reasons,
    finisher_status=result.finisher_status, seconds=result.elapsed_s)), flush=True)

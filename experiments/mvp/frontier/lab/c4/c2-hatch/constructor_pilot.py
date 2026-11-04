"""Development-only quick25 cold construction and ballast pilot (spawn-safe)."""
from dataclasses import asdict
from pathlib import Path
import json
from time import monotonic

from stowage.io import load_instance
from constructor import construct
from alns import Problem
from planner import _finish


def main():
    instance = load_instance("experiments/mvp/corpus/val/small/feeder_800/simulated_yard/seed-040100025.json")
    started = monotonic()
    problem = Problem(instance)
    result = construct(instance, budget_s=30., seed=0, problem=problem, tables=problem.engine.T)
    row = asdict(result)
    out = Path(__file__).parent / "results/constructor-pilot25.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(row, indent=2) + "\n")
    print(json.dumps({k: row[k] for k in ["status", "replay_lifts", "wall_time_s", "iterations"]}), flush=True)
    if result.actions:
        scored = problem.score(result.actions)
        try:
            finished, metadata = _finish(instance, scored.plan, seed=0, finalize_s=20., label="cold-constructor-pilot")
            row["cold_finish20"] = metadata
            row["cold_plan20"] = finished.plan.model_dump(mode="json") if finished and finished.plan else None
            print(json.dumps(metadata), flush=True)
        except Exception as error:
            row["cold_finish20"] = dict(error=f"{type(error).__name__}: {error}", passed=False)
            raise
        finally:
            row["total_wall_s"] = monotonic() - started
            out.write_text(json.dumps(row, indent=2) + "\n")


if __name__ == "__main__":
    main()

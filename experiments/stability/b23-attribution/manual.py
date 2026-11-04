"""B 2.3 quantities of the ship's own standard loading-manual conditions (departure), for comparison."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments/checker/perf-1028")); sys.path.insert(0, str(Path(__file__).parent))
from corpus import manual_case
from sweep import b23
from stowage.checker.full import departure_condition
from stowage.checker.stability import check_stability
for ship in sys.argv[1:]:
    for cond in ("C1", "C2", "C3"):
        try:
            inst, plan = manual_case(ship, cond)
            rep = check_stability(inst, departure_condition(inst, plan), call_index=inst.call_index)
            r = b23(rep)
            bal = sum(plan.ballast.tank_masses_kg.values()) / 1e3
            print(ship, cond, "ballast_t", round(bal), {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k != "ratios"}, flush=True)
        except Exception as e:
            print(ship, cond, "ERROR", type(e).__name__, str(e)[:200], flush=True)

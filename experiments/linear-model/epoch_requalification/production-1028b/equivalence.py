"""Force the owner's independent coefficient rebuilds for this fresh epoch."""
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("w2_equivalence_promoter", HERE.parent/"promote.py")
promote = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = promote
spec.loader.exec_module(promote)
for ship in promote.SHIPS:
    folder = HERE/"ordinary"/ship
    result = promote.verify_equivalence(ship, folder, promote.read(folder/"raw.json"))
    print(json.dumps(dict(ship=ship,passed=result["passed"],seconds=result["seconds"])),flush=True)

"""Force independent coefficient rebuilds while edge measurements finish."""
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('claude_equivalence_promoter',
    HERE.parent/'epoch_requalification/promote.py')
promote = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = promote
spec.loader.exec_module(promote)
for ship in promote.SHIPS:
    folder = HERE/'epoch-claude'/ship
    result = promote.verify_equivalence(ship, folder, promote.read(folder/'raw.json'))
    print(json.dumps(dict(ship=ship,passed=result['passed'],seconds=result['seconds'])),flush=True)

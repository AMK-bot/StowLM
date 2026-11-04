"""The 4 medium probe inputs (mvp-twostep's comparison-v43-mass55-medium4-scoped-v4; two-step passed 1/4). Diagnostic
only: never used for tuning."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SPECS = ROOT / "experiments/mvp/solve47/twostep/comparison-v43-mass55-medium4-scoped-v4/specs.json"

if __name__ == "__main__":
    print(" ".join(str(Path(s["instance_path"]).relative_to(ROOT)) for s in json.loads(SPECS.read_text())))

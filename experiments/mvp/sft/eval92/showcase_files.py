"""The three small Laem Chabang showcase calls (mvp-ui FINAL_RULE, app/stage_stowlm.py SHOWCASE_INSTANCES), resolved to
their frozen showcase corpus files by instance id. Prints the relative paths."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
IDS = ("random.small.29000201.0.7", "random.small.29000022.0.7", "random.small.29000102.0.7")


def showcase_files() -> list[str]:
    found = {}
    for f in sorted((ROOT / "experiments/mvp/corpus/showcase").glob("*-small.json")):
        iid = json.loads(f.read_text()).get("id")
        if iid in IDS:
            found[iid] = str(f.relative_to(ROOT))
    missing = [i for i in IDS if i not in found]
    if missing:
        raise SystemExit(f"showcase inputs missing for {missing}")
    return [found[i] for i in IDS]


if __name__ == "__main__":
    print(" ".join(showcase_files()))

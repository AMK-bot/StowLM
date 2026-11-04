# Like make_data.py but with ~15k-token texts, for QLoRA benchmarks at seq 8k / 12k. Timing only (random content).
# Usage: uv run python experiments/glimmer-bench/make_long_data.py OUT_DIR
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
random.seed(1)
from make_data import plan  # noqa: E402  (make_data also writes its own small set into argv[1]; we overwrite below)

out = Path(sys.argv[1])
for name, n in [("train", 16), ("valid", 2)]:
    with open(out / f"{name}.jsonl", "w") as f:
        for _ in range(n):
            f.write(json.dumps({"text": plan(500)}) + "\n")

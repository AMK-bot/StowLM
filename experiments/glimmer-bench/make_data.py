# Synthetic LoRA benchmark data shaped like a StowLM master plan (cargo groups -> bay sections).
# Only for timing: the content is random, so the loss means nothing. Usage: make_data.py OUT_DIR
import json
import random
import sys
from pathlib import Path

PORTS = ["THLCH", "SGSIN", "MYPKG", "VNSGN", "IDJKT"]
random.seed(0)


def action():
    return (f"<grp POD={random.choice(PORTS)} size={random.choice([20, 40])} wcls={random.choice('LMH')} "
            f"dg={random.choice(['none', 'none', 'none', '3', '8', '9'])} n={random.randint(1, 30)}> -> "
            f"<bay {random.randint(1, 40):02d} deck={random.choice(['above', 'below'])} "
            f"rows={random.randint(1, 6):02d}-{random.randint(7, 12):02d}>")


def plan(n_actions):
    return "\n".join(action() for _ in range(n_actions))


out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
for name, n in [("train", 64), ("valid", 8)]:
    with open(out / f"{name}.jsonl", "w") as f:
        for _ in range(n):
            f.write(json.dumps({"text": plan(400)}) + "\n")  # long enough to fill a 4k-token window

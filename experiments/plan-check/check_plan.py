# Check the docs/plan.md task pyramid: every task's needs must point to existing tasks in strictly lower tiers.
# Usage: uv run python experiments/plan-check/check_plan.py docs/plan.md
import functools
import re
import sys

text = open(sys.argv[1]).read()
tasks = {}
for m in re.finditer(r"\*\*(\d+)\.(\d+) [^*]*\*\*(?: \(needs ([^;)]*)[;)])?", text):
    tasks[f"{m.group(1)}.{m.group(2)}"] = (int(m.group(1)), [n.strip() for n in (m.group(3) or "").split(",") if n.strip()])
for m in re.finditer(r"\*\*(\d+\.\d+) [^*]*\*\* \(same needs as (\d+\.\d+)", text):
    tasks[m.group(1)] = (tasks[m.group(1)][0], tasks[m.group(2)][1])

errors = []
for tid, (tier, needs) in tasks.items():
    if tier > 0 and not needs:
        errors.append(f"{tid}: no needs listed")
    for n in needs:
        if n == "G1":
            if tier <= 5:
                errors.append(f"{tid} needs G1 but is in tier {tier}")
        elif n not in tasks:
            errors.append(f"{tid}: unknown need {n}")
        elif tasks[n][0] >= tier:
            errors.append(f"{tid} (tier {tier}) needs {n} (tier {tasks[n][0]})")


@functools.lru_cache(None)
def chain(t):
    longest = max((chain(n) for n in tasks[t][1] if n in tasks), key=len, default=())
    return longest + (t,)


used = {n for _, ns in tasks.values() for n in ns}
order = lambda s: tuple(map(int, s.split(".")))
print(f"{len(tasks)} tasks")
print("needed by nothing:", ", ".join(sorted(set(tasks) - used, key=order)))
for goal in ("9.1", "10.2", "100.1"):
    if goal in tasks:
        print(f"longest chain to {goal}:", " → ".join(chain(goal)))
print("errors:", "\n  ".join([""] + errors) if errors else "none")
sys.exit(1 if errors else 0)

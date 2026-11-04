"""Why would the checker refuse this stow? Full check with the ship's default tanks, by rule family."""
from pathlib import Path
from collections import Counter
import sys
from time import monotonic
ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier'), str(Path(__file__).parent)]
from alns import Problem, Sequencer
from pages import Model
from stowage.checker import check
from stowage.io import load_instance
instance = load_instance(ROOT/sys.argv[1]); problem = Problem(instance)
model = Model(problem)
model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=1, deadline=monotonic()+30)
chosen, info = model.dive(deadline=monotonic()+60)
a = model.assignment(chosen)
scored = problem.score(Sequencer(problem).order(a))
t = monotonic()
report = check(instance, scored.plan)
print('passed', report.passed, 'check s', round(monotonic()-t, 1))
print(Counter(v.rule_id for v in report.violations).most_common(30))
seen = set()
for v in report.violations:
    if v.rule_id not in seen:
        seen.add(v.rule_id)
        print(v.rule_id, v.message[:220], v.measured, v.limit)
import json
Path(__file__).with_name('diag_assignment.json').write_text(json.dumps(a))

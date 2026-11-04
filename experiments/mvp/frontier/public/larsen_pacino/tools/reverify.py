"""Re-scores every saved plan with the current evaluator and compares it with the stored result."""
import glob, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import evaluator as ev, published as pub
HERE = os.path.join(os.path.dirname(__file__), '..')
ROOT = "/Users/anda/Projects/meta-hackathon-stowage/data/raw/larsen-pacino"
tags = sys.argv[1:]
worst = 0; n = 0; bad = []; maxgap = 0
for tag in tags:
    for f in sorted(glob.glob(f"{HERE}/results/{tag}/inst*.json")):
        r0 = json.load(open(f)); k = r0['instance']
        v = ev.read_vessel(pub.path_vessel(ROOT, k)); I = ev.read_instance(pub.path_instance(ROOT, k))
        r = ev.evaluate(v, I, ev.read_plan(f"{HERE}/plans/{tag}/inst{k:02d}.plan"))
        n += 1
        worst = max(worst, abs(r['obj'] - r0['obj']))
        maxgap = max(maxgap, abs(r['obj'] - r0['search']['obj']))
        if not r['feasible']: bad.append((tag, k, r['violations']))
print(f"plans {n}; largest change from stored score {worst:.6f}; largest search-vs-evaluator gap {maxgap:.4f}; infeasible {bad}")

"""Part B: what securing costs in lifts. One map, one reference GM, arms that change only the securing price or layout."""
import json
from pathlib import Path
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from stow import LAB, Settings, Assignment, block_layout, exact_ratio, reference, repair_oog, to_assignment
from method import cover_first, install, Model, Sequencer
from alns import Problem
from stowage.checker import check
from stowage.model import Instance

ROOT = LAB.parents[3]
ARMS = dict(unpriced=dict(limit=9.), limit100=dict(limit=1.), limit95=dict(limit=.95), limit90=dict(limit=.9), limit85=dict(limit=.85),
            tiers4=dict(limit=.95, cap=4), tiers3=dict(limit=.95, cap=3),
            spread_covers=dict(limit=.95, cover_port_weight=0., deck_port_weight=0.))


def run(path, gm):
    install()
    instance = Instance.model_validate_json((ROOT/path).read_text())
    p, hull = Problem(instance), instance.vessel.hull
    draft = .9*min(hull.max_draught_m, instance.port.max_draught_m or hull.max_draught_m)
    model = Model(p, oracle=None, deck_tiers=None, sight_draft_m=draft, sight_roof_margin_m=.15)
    total = sum(p.boxes[b].gross_mass_kg for b in instance.load_list)/1000.
    x = hull.lpp_m*.48
    rows = []
    for name, arm in ARMS.items():
        arm = dict(arm)
        cap = arm.pop('cap', None)
        started = monotonic()
        settings = Settings(lcg=(x-3., x+1.), tcg=.08, vertical_max_tm=total*(hull.depth_m+.5), **arm)
        if cap:
            settings.deck_tier_cap = {j: cap for j, q in enumerate(model.pages) if q.deck}
        solver = Assignment(model, reference(hull, gm), settings, layout=block_layout(model))
        placed = solver.dive(deadline=monotonic()+60.)
        row = dict(map=instance.id, arm=name, placed=placed is not None)
        if placed is not None:
            if settings.limit < 2:
                placed, excess = solver.exchange(placed, deadline=monotonic()+45.)
                row['excess'] = excess
            assignment, illegal = to_assignment(model, placed, solver)
            assignment = repair_oog(p, model, model.clear_oog(assignment))
            actions = Sequencer(p).order(assignment) if not p.mixes_hold_stack(assignment) else None
            scored = p.score(actions) if actions else None
            if scored:
                g, ratio, over = exact_ratio(instance, scored.plan)
                report = check(instance, scored.plan)
                c = report.count
                row.update(lifts=float(c.weighted_nonproductive), passed=report.passed, gm=g, ratio=ratio, chains_over=over,
                           relocations=c.relocations, hatch_now=c.hatch_cover_lifts,
                           hatch_later=sum(f.hatch_cover_lifts for f in c.future), restow_later=sum(f.restow_lifts for f in c.future),
                           rules=sorted({v.rule_id.split('.')[-1] for v in report.violations}))
        row['s'] = round(monotonic()-started, 1)
        rows.append(row)
        print(row, flush=True)
    return rows


if __name__ == '__main__':
    out = []
    for path, gm in ((l.split()[0], float(l.split()[1])) for l in open(HERE/'price-maps.txt')):
        out += run(path, gm)
        (HERE/'price.json').write_text(json.dumps(out, indent=1)+'\n')

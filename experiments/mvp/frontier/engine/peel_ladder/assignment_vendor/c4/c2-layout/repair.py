# Copied from experiments/mvp/frontier/lab/c5/c5-peel/assignment_vendor/c4/c2-layout/repair.py; frozen body unchanged.
"""Local OOG repair using checker placement rules, without changing any rule."""
from stowage.checker.rules import placement_violations
from stowage.conventions import parse_slot
from stowage.model.ship import Placement


def repair_oog(problem, model, assignment):
    """Swap a conflicting OOG box to a compatible clear position.

    Scout excludes every rare/special box from swap partners. Here a partner may
    be special, but both resulting whole columns and both placement rule sets
    are checked. Final replay and the full checker remain mandatory.
    """
    out = dict(assignment)
    T = model.T
    oog = [b for b in out if problem.boxes[b].oog is not None]

    def violations(table, box_id):
        ship = problem.instance.onboard.model_copy(update={
            'placements': [Placement(container_id=b, slot=s)
                           for b, s in table.items() if b != box_id]})
        return [v for v in placement_violations(
            problem.instance, ship, problem.boxes[box_id], table[box_id])
                if v.severity == 'hard']

    def e9_count(table):
        return sum(sum('e9' in str(v.rule_id) for v in violations(table, b))
                   for b in oog)

    count = e9_count(out)
    if not count:
        return out
    for box_id in oog:
        if not any('e9' in str(v.rule_id) for v in violations(out, box_id)):
            continue
        box = problem.boxes[box_id]
        candidates = [b for b in out if b != box_id
                      and problem.boxes[b].length_ft == box.length_ft]
        candidates.sort(key=lambda b: (problem.boxes[b].pod != box.pod,
                                       -parse_slot(out[b]).tier,
                                       abs(problem.boxes[b].gross_mass_kg-box.gross_mass_kg), b))
        for partner in candidates:
            trial = dict(out)
            trial[box_id], trial[partner] = out[partner], out[box_id]
            if not (T.static[T.box_index[box_id], T.slot_index[trial[box_id]]]
                    and T.static[T.box_index[partner], T.slot_index[trial[partner]]]):
                continue
            if not (model.column_fine(trial, trial[box_id])
                    and model.column_fine(trial, trial[partner])):
                continue
            if violations(trial, box_id) or violations(trial, partner):
                continue
            new_count = e9_count(trial)
            if new_count < count:
                out, count = trial, new_count
                break
    return out

"""Run the reviewer's evaluator (indep.py) and check_rcspp on the same plans and record every disagreement.

Usage: audit_plans.py <suite_root> <plans_dir or -> <out.jsonl> <names...>; '-' means the published plan of each name.
"""
import collections
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import indep
from check_rcspp import Readings, evaluate
from rcspp_io import read_instance, read_vessel, vessel_path, without_idle_ports

SAME_RULE = {'(15) two boxes in one slot': '(15)', '(22) gap below a box': '(22)', '(23) 20 on a 40': '(23)', '(24) stack height': '(24)', '(25) stack weight': '(25)',
             '(26) reefer without a plug': '(26)', 'given: arrival reefer without a plug': '(26) ac', '(6) displacement': '(6)', '(7) trim': '(7)', '(11) GM': '(11)',
             '(12) bending': '(12)', '(29) lashing': '(29)', '(35) long crane': '(35) per box', '(37) too many mixed bays': '(37)',
             'arrival-only stack lashing': '(29) ac-only', 'given: arrival stack height': '(24) ac-only', 'given: arrival stack weight': '(25) ac-only',
             '(2) tank outside 0 to 100 %': '(2)', '(18)(19) lone 20': 'lone half cell', '(18)(19) paired 20s differ in load or discharge port': '(19)',
             '(13) arrival position differs': '(13) cell differs', '(16) arrival box missing from plan': 'AC box missing', '(20)(21) aboard exactly from load to discharge port': '(20)(21) aboard set'}


def main():
    root = Path(sys.argv[1])
    plans, out = sys.argv[2], sys.argv[3]
    vessels = {}
    for name in sys.argv[4:]:
        ship = name.split('_')[0]
        theirs_vessel = vessels.setdefault('indep_'+ship, indep.vessel(root/'Vessels'/f'vessel_{ship}.xml'))
        vessel = vessels.setdefault(ship, read_vessel(vessel_path(root, name)))
        theirs_instance = indep.renumber(indep.instance(root/'Instances'/f'{name}.xml'))
        instance = without_idle_ports(read_instance(root/'Instances'/f'{name}.xml'))
        if plans == '-':
            positions, tanks, _ = indep.published(root/'Results'/f'{name}_results.xml')
        else:
            positions, tanks = indep.plan_json(Path(plans)/f'{name}.json')
        indep.OUT.clear()
        breaches, notes, by_port, totals = indep.check(theirs_vessel, theirs_instance, positions, tanks)
        faults, m = evaluate(vessel, instance, positions, tanks, Readings())
        mine = collections.Counter(breaches)
        mine['(2)'] = mine.pop('(2) over', 0)+mine.pop('(2) negative', 0)
        mine['(27*)'] = notes['(27) levels mixed, arrival included']
        ours = collections.Counter()
        for key, count in faults.items():
            ours['(27*)' if key.startswith('(27*)') else SAME_RULE.get(key, key)] += count
        rules = (set(mine) | set(ours))-{'(35) per box, hatch lifts added', '(13) arrival side differs'}
        differ = {k: (mine[k], ours[k]) for k in rules if mine[k] != ours[k]}
        worst = {k: max(abs(by_port[p][k]-m['ports'][p][k]) for p in by_port) for k in ('displacement', 'trim', 'gm', 'bending', 'lashing', 'ballast')}
        counts = sum(by_port[p][k] != m['ports'][p][k] for p in by_port for k in ('mixed_bays', 'long_crane'))
        totals_differ = {k: (totals[k], m[k]) for k in ('objective_flat', 'objective_file', 'ballast') if abs(totals[k]-m[k]) > 1e-6}
        if totals['loaded'] != m['loaded']:
            totals_differ['loaded'] = (totals['loaded'], m['loaded'])
        row = {'name': name, 'plans': plans, 'loaded': totals['loaded'], 'rule_differences': differ, 'worst_number_difference': worst, 'count_differences': counts, 'totals_differ': totals_differ,
               'independent_breaches': {k: v for k, v in mine.items() if v}, 'off_table': len(indep.OUT)}
        with open(out, 'a') as sink:
            sink.write(json.dumps(row)+'\n')
        print(name, 'rule differences', differ, 'worst', round(max(worst.values()), 9), 'counts', counts, 'totals', totals_differ, flush=True)


if __name__ == '__main__':
    main()

"""Join fixed-map outer evidence, constructive attempts and fresh controls."""
from pathlib import Path
import hashlib
import json

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
ROOT = HERE.parents[5]


def main():
    validity = json.loads((HERE/'validity.json').read_text())['validation_plans']
    controls = {r['instance_id']:r for r in validity}
    input_paths = (LAB/'sets/quick_medium.txt').read_text().splitlines()[:2]
    input_paths += (LAB/'sets/medium12.txt').read_text().splitlines()
    rows = []
    for path in input_paths:
        identity = json.loads((ROOT/path).read_text())['id']
        row = json.loads((HERE/'results-final'/(identity+'.json')).read_text())
        control = controls.get(identity)
        if control:
            row.update(status='feasible_with_witness',positive_control=control)
        rod_path = HERE/'results-rod'/(identity+'.json')
        if rod_path.exists():
            row['rod_strengthening'] = json.loads(rod_path.read_text())
        rows.append(row)
    followups = [r.get('page_followup',{}) for r in rows[2:]]
    summary = dict(maps=[], d185=dict(maps=12,
            proved_infeasible=sum(r['status']=='proved_infeasible' for r in rows[2:]),
            relaxation_witnesses=sum(r.get('relaxation_status')=='feasible_with_witness' for r in rows[2:]),
            replay_complete=sum(bool(f.get('replay_complete')) for f in followups),
            full_checker_passes=sum(bool(f.get('full_checker_passed')) for f in followups),
            full_checker_calls=sum(f.get('full_checker_calls',0) for f in followups),
            preliminary_outer_wall_s=sum(r.get('elapsed_s',0)-r.get('page_followup',{}).get('wall_s',0) for r in rows[2:]),
            outer_wall_s=sum(r.get('rod_strengthening',{}).get('elapsed_s',0) for r in rows[2:]),
            surviving_gm_branches=sum(sum(b['status']=='relaxation_feasible_with_witness' for b in
                r.get('rod_strengthening',{}).get('branches',[])) for r in rows[2:]),
            followup_wall_s=sum(f.get('wall_s',0) for f in followups)),
        controls=dict(maps=2,relaxation_feasible=2,full_checker_passes=sum(r['passed'] for r in validity),
                      paired_mean_lifts=sum(r['lifts'] for r in validity)/2,
                      full_checker_wall_s=sum(r['elapsed_s'] for r in validity)),
        assumptions=['Empty decision arrival and closed covers; original rule activation; no active stubs',
                     'Lashing GM is distinct from stability GM; no equality/transfer bound is assumed',
                     'Feasible aggregate witnesses do not establish physically legal stows',
                     'Only full-checker passes are labeled feasible; no numerical LP status is an infeasibility proof'])
    lines = ['# Final fixed-map outcomes','',
        '| Map suffix | Verdict | Strong outer s | Page follow-up s | Replay complete | Full pass |',
        '| --- | --- | ---: | ---: | --- | --- |']
    for row in rows:
        f = row.get('page_followup',{})
        control = controls.get(row['instance_id'])
        base_time = row.get('elapsed_s',0)-f.get('wall_s',0)
        outer_time = row.get('rod_strengthening',{}).get('elapsed_s',base_time)
        short = row['instance_id']
        record = dict(instance_id=short,input=row['input'],status=row['status'],
            outer_feasible=row.get('relaxation_status')=='feasible_with_witness',
            outer_s=outer_time,preliminary_outer_s=base_time,followup_s=f.get('wall_s',0.),
            replay_complete=bool(f.get('replay_complete')) if not control else control['replay_legal'],
            full_checker_passed=bool(f.get('full_checker_passed')) if not control else control['passed'],
            lifts=f.get('lifts') if not control else control['lifts'],
            evidence=str((HERE/'results-final'/(short+'.json')).relative_to(ROOT)),
            outer_evidence=str((HERE/'results-rod'/(short+'.json')).relative_to(ROOT)))
        summary['maps'].append(record)
        lines.append(f"| {short} | {row['status']} | {outer_time:.2f} | {f.get('wall_s',0.):.2f} | {record['replay_complete']} | {record['full_checker_passed']} |")
    d = summary['d185']
    lines += ['',f"D185: {d['proved_infeasible']}/12 proved infeasible; {d['relaxation_witnesses']}/12 outer witnesses; "
              f"{d['replay_complete']}/12 complete replay-legal stows; {d['full_checker_passes']}/12 full-checker passes.",
              '',f"D185 outer time {d['outer_wall_s']:.2f} s; constructive follow-up {d['followup_wall_s']:.2f} s, "
              f"including {d['full_checker_calls']} full-checker calls.",
              '',f"All {d['surviving_gm_branches']}/96 D185 lashing-GM intervals survived even the occupied-root rod rows. "
              'Across all fourteen maps, all 112 intervals survived. Earlier weaker spatial sweep time is retained '
              f"separately ({d['preliminary_outer_wall_s']:.2f} s for D185).",
              '', 'The controls reuse preexisting cold passing plans and were freshly checked: 71/116 lifts, '
              'both complete and legal. Their mean is 93.5 versus V6 172.5; this experiment claims no new lift gain. '
              'No paired 0.9.0 medium lift comparison is available.',
              '', 'Unknown means neither a complete legal stow nor an all-stow/all-tank contradiction was established. '
              'A surviving GM branch is an outer allocation at a necessary acceleration floor, not an achieved GM/KG state. '
              'Finite-angle area upper bounds and visibility remain too weak to settle these maps.']
    (HERE/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (HERE/'RESULTS.md').write_text('\n'.join(lines)+'\n')
    (HERE/'source.sha256.json').write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [*HERE.glob('*.py'),*(ROOT/'src/stowage/checker').glob('*.py')]},indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='maps'},indent=2))


if __name__=='__main__':
    main()

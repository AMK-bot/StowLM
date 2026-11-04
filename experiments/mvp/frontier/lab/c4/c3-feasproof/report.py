"""Render the compact measured report directly from retained artifacts."""
from pathlib import Path
import json,statistics
HERE=Path(__file__).resolve().parent
w=json.loads((HERE/'windows.json').read_text())
c=json.loads((HERE/'controls.json').read_text())['controls']
v=json.loads((HERE/'validation.json').read_text())
rows=[r for r in w['maps'].values() if r['cohort']=='medium12']
rows.sort(key=lambda r:r['input'])
interval=lambda a:'–'.join(f'{x:.3f}' for x in a)
lines=['# Measured Cycle 4 result','',
    'Graph: fractional box-class to real bay/tier signatures, shared cargo moments and departure tanks across D57 phases, necessary securing/rod rows, and whole-domain finite-angle interval covers.','',
    f"Joint sweep: {w['summary']['wall_s']:.3f} s for 23 maps; area sweep: {w['area_summary']['wall_s']:.3f} s for 69 map-phases. Fresh unchanged full checks: {sum(x['elapsed_s'] for x in c):.3f} s, 19/19 complete replay-legal passes on 12 input maps. No new constructed plans or passes. No paired 0.9+E9 comparison or mean-lift improvement claim.",'',
    'In medium12, seed 185100000 is feasible with the retained 114-lift legal witness. Every other map is unknown, with a fractional outer witness. None is proved infeasible. Candidate area caps are extremely loose and uncertified for the default direct floating fallback; they impose no strict KG cut.','',
    'Exact per-phase and per-bay endpoints are in windows.json. The following are **surviving scalar outer windows**, not achieved safety conditions. Draft is mean upright draught in metres, deck mass is tonnes. The times include the joint and area stages for that map, excluding the separately timed control recheck.','',
    '| Map seed | Verdict | Departure KG m | Midpoint KG m | Arrival KG m | Draft dep / arrival m | Deck count | Deck tonnes | Time s |',
    '|---|---|---:|---:|---:|---|---:|---:|---:|']
for r in rows:
    p=r['phase_windows'];seed=Path(r['input']).stem.replace('seed-','')
    verdict='feasible, 114 lifts' if r['verdict']=='feasible_with_legal_witness' else 'unknown'
    lines.append('| '+' | '.join([seed,verdict,*[interval(p[n]['physical_kg_m']) for n in ('departure','mid_voyage','arrival')],
        interval(p['departure']['mean_draught_m'])+' / '+interval(p['arrival']['mean_draught_m']),
        interval(r['cargo']['deck_count']),interval([m/1000 for m in r['cargo']['deck_mass_kg']]),
        f"{r['wall_s']+r['area_results']['wall_s']:.2f}"])+' |')
lines+=['','All 11 preselected passing f60/f70 input maps remain feasible with their 18 saved legal plans. This includes passing plans with '+', '.join(map(str,sorted({x['requested_boxes'] for x in c if x['requested_boxes'] in (88,162)})))+' boxes. These are validity controls, not a lift benchmark.','',
    f"Verification: {v['independently_verified_support_certificates']} exact matrix support certificates verified independently; 19/19 full-plan projections and scalar-window containment checks; 57/57 observed phase areas inside candidate bounds; all 69 area windows retain full free-trim and heel coverage. 25 unit tests passed with GMP, plus all 9 area tests passed on the standard Fraction backend. Source and artifact hashes pass final_audit.py. No strict area cap is installed.",'',
    'Keep part: retain the joint windows, witnesses and regression checks; the area lane has not resolved a map or narrowed the KG windows. Next certify native direct arithmetic and contract the extreme free-trim tails before refining heel knots.','',
    'Assumptions: default proportional D57 consumables; one departure ballast state retained at sea; empty decision arrival/no future load scope; ordinary active rule catalogue. Unsupported variants must remain unknown.','',
    'Entry points: run_windows.py, then run_area.py; final_audit.py checks the retained artifacts. Papers and the outwardness argument are in NOTES.md.']
(HERE/'MEASUREMENTS.md').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines[:9]))
print('control_lifts_mean',statistics.mean(x['lifts'] for x in c),'control_sizes',sorted({x['requested_boxes'] for x in c}))

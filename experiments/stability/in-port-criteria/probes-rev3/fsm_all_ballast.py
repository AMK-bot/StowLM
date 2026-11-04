"""Rev-3 addendum probe: GM cost of charging maximum FSM (over all sounding rows) for every adjustable ballast tank.
Cost = sum(rho * max fsm_m4) / displacement. Displacements/GMs from arrival_trim.json and loaded_arrival.json."""
import json
from pathlib import Path
from stowage.vessel.fleet import load_vessel
H = Path(__file__).parent
at = json.loads((H / 'arrival_trim.json').read_text()); la = json.loads((H / 'loaded_arrival.json').read_text())
loaded = {k.split(':')[1].split('.')[0]: v for k, v in la.items()}
out = {}
for s in ('feeder_500', 'feeder_800', 'feeder_1700'):
    v = load_vessel(s)
    rows = []
    for t in v.tanks:
        if t.adjustable and t.kind == 'ballast':
            table = t.table if isinstance(t.table, list) else [t.table]
            fsm = max(r.fsm_m4 for r in table)
            rows.append((t.id, fsm * t.content_density_kg_m3 / 1e3))  # t*m
    tot = sum(m for _, m in rows)
    # empty-ship arrival: consumables + FPT 25% + HEEL 50% (approx displacement from probes)
    e = at[s]['cases']['arrival_consumables']; heel = at[s]['heel']['pair@0.5']
    d_empty = heel['disp_t'] + 0.25 * at[s]['fwd_groups']['FPT']['t']
    gm_empty = heel['gm']
    l = loaded[s]['booked']
    out[s] = dict(sum_max_fsm_tm=round(tot, 1), per_tank={k: round(m, 1) for k, m in rows},
                  empty_arrival=dict(disp_t=round(d_empty, 1), gm_before=round(gm_empty, 3), dGM=round(tot / d_empty, 3), gm_after=round(gm_empty - tot / d_empty, 3)),
                  loaded_slice_arrival=dict(disp_t=l['disp'], gm_before=l['gm'], dGM=round(tot / l['disp'], 3), gm_after=round(l['gm'] - tot / l['disp'], 3)))
    print(s, json.dumps({k: out[s][k] for k in ('sum_max_fsm_tm', 'empty_arrival', 'loaded_slice_arrival')}))
(H / 'fsm_all_ballast.json').write_text(json.dumps(out, indent=1))

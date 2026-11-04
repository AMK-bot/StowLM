"""Rev-3 probe: arrival in-port state (s0) of saved D58 pilot instances (loaded background), as booked,
and with forward trim ballast added; plus HEEL pair at 50%. Read-only."""
import json, math, sys
from pathlib import Path
from stowage.model import Instance
from stowage.checker import stability
ROOT = Path(__file__).resolve().parents[4]
def cap(t): return t.capacity_m3 * t.content_density_kg_m3
out = {}
for run in ('000', '008', '018'):
    inp = json.loads((ROOT / f'experiments/solver-diag-1028/pilot-d58/runs/exact/{run}/result.json').read_text())['input']
    i = Instance.model_validate_json(Path(inp).read_text())
    lim = i.vessel.hull.lpp_m * math.tan(math.radians(1))
    tanks = {t.id: t for t in i.vessel.tanks}
    base = dict(i.onboard.tank_masses_kg)
    def ev(fill):
        j = i.model_copy(deep=True); j.onboard = j.onboard.model_copy(update={'tank_masses_kg': fill})
        r = stability.check_stability(j, j.onboard, call_index=j.call_index, in_port=True); m = r.metrics
        return dict(rules=sorted({v.rule_id for v in r.violations}), msg=[v.message[:70] for v in r.violations][:2], trim=round(m.get('trim_m', 0), 3),
                    gm=round(m.get('gm_m', 0), 3), ta=round(m.get('draught_aft_m', 0), 3), tf=round(m.get('draught_fwd_m', 0), 3),
                    disp=round(m.get('displacement_kg', 0) / 1e3, 1))
    rows = {'booked': ev(base)}
    fpt = [t for t in tanks.values() if t.id.startswith('FPT')]
    fwd = [t for t in tanks.values() if t.id.startswith('FWD-DEEP')]
    aft_bal = [t for t in tanks.values() if t.id.startswith(('APT', 'WING-A'))]
    rows['booked_fill_t'] = {k: round(v / 1e3, 1) for k, v in base.items() if v > 1}
    for name, group in (('FPT', fpt), ('FWD-DEEP', fwd)):
        for frac in (.5, 1.):
            f = dict(base); f.update({t.id: max(f.get(t.id, 0), frac * cap(t)) for t in group}); rows[f'{name}@{frac}'] = ev(f)
    # swap aft ballast forward (empty APT/WING-A, fill FPT) keeps displacement roughly
    f = dict(base); moved = sum(f.get(t.id, 0) for t in aft_bal)
    for t in aft_bal: f[t.id] = 0.
    for t in fpt: f[t.id] = min(cap(t), f.get(t.id, 0) + moved / len(fpt))
    rows['aft_to_FPT'] = ev(f)
    f = dict(base); f.update({t.id: .5 * cap(t) for t in tanks.values() if t.id.startswith('HEEL')}); rows['heel50'] = ev(f)
    out[f'{run}:{i.vessel.id}'] = dict(trim_limit=round(lim, 3), max_disp=round(i.vessel.hull.max_displacement_kg / 1e3, 1), **rows)
    print(run, i.vessel.id, 'lim', round(lim, 3), json.dumps(rows)[:900], flush=True)
Path(__file__).with_name('loaded_arrival.json').write_text(json.dumps(out, indent=1))

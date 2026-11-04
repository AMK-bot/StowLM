import json, glob, math
from pathlib import Path
here = Path(__file__).parent
rows = []
for f in sorted(here.glob("*-0*.json")):
    d = json.loads(f.read_text())
    print(f"\n== {f.name} {d['instance_id']} {d['vessel']} moves={d['moves']}")
    print(f"  pass={d['passed']} pass_int={d['passed_intermediate']} cold={d['check_s_cold']:.1f}s warm={d['check_s']:.2f}s int={d['check_intermediate_s']:.2f}s")
    print("  int viol:", sorted({(v['rule'], v['move_index'], v['msg'][:60]) for v in d['intermediate_violations']}))
    a = d['arrival_A']; dep = d['departure']['stab']
    def line(tag, s, st=None):
        b = s.get('b23_worst_ratio')
        print(f"  {tag:28s} GM={s['gm_m'] and round(s['gm_m'],3)} list={s['list_deg'] and round(s['list_deg'],3)}deg trim={s['trim_m'] and round(s['trim_m'],3)} T={s['draught_mean_m'] and round(s['draught_mean_m'],3)} disp={s['displacement_kg'] and round(s['displacement_kg']/1e3)}t wx={s['weather_ratio'] and round(s['weather_ratio'],3)} B23={b and round(b,3)} fail={s['fail_rules']} t={s['t_s']:.2f}" + (f" SF={st['max_sf_ratio']:.3f} BM={st['max_bm_ratio']:.3f} {st['fail_rules']} ts={st['t_s']:.2f}" if st else ""))
    line("arrival", a)
    gms, lists = [], []
    for t in d['trajectory']:
        tag = f"m{t['move_index']} {t['slot']} {t['len_ft']}' {t['gross_kg']/1e3:.1f}t{' OPEN' if t['covers_off'] else ''}"
        line(tag + " A", t['A_initial_tanks']['stab'], t['A_initial_tanks']['strength'])
        if t['B_departure_ballast']['stab'] != t['A_initial_tanks']['stab']:
            line(tag + " B", t['B_departure_ballast']['stab'], t['B_departure_ballast']['strength'])
        if 'C_initial_tanks_covers_forced_on' in t:
            line(tag + " C(forced on)", t['C_initial_tanks_covers_forced_on']['stab'])
        for k in ('A_initial_tanks', 'C_initial_tanks_covers_forced_on'):
            s = t.get(k, {}).get('stab')
            if s and s['gm_m'] is not None: gms.append(s['gm_m']); lists.append(abs(s['list_deg']))
    line("departure", dep, d['departure']['strength'])
    ab = [ (t['A_initial_tanks']['stab']==t['B_departure_ballast']['stab']) for t in d['trajectory']]
    rows.append({"file": f.name, "instance": d['instance_id'], "vessel": d['vessel'], "n_loads": d['n_load_lifts'],
        "passed": d['passed'], "passed_intermediate": d['passed_intermediate'],
        "check_warm_s": d['check_s'], "check_cold_s": d['check_s_cold'], "check_intermediate_s": d['check_intermediate_s'],
        "int_rules": sorted({v['rule'] for v in d['intermediate_violations']}),
        "int_move_indices": sorted({v['move_index'] for v in d['intermediate_violations']}),
        "open_hatch_loads": sum(1 for t in d['trajectory'] if t['covers_off']),
        "min_gm_intermediate": min(gms) if gms else None, "max_abs_list_deg_intermediate": max(lists) if lists else None,
        "gm_departure": dep['gm_m'], "list_deg_departure": dep['list_deg'], "gm_arrival": a['gm_m'],
        "A_equals_B_all": all(ab), "ballast_total_kg": d['ballast_total_kg']})
(here / "summary.json").write_text(json.dumps(rows, indent=1))

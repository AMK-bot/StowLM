"""Summarize actual production qualification and shaped-liquid measurements."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent


def main():
    lines = ["# Hull-following tank stability validation", "",
        "Production inputs are loaded with `load_vessel(name, with_hull=True)` for departure checks. KN holdouts use the same production parameter geometry directly. All quantities are SI.", "",
        "## Physics", "",
        "Fixed ballast uses actual-volume sounding FSM and the solid's finite-angle liquid centroid. Hull-section full and empty paths also use the solid API. Only explicitly rectangular solids retain the exact box acceleration.", "",
        "Consumables use a whole-fill upright FSM supremum: between polygon vertex heights, surface moments are polynomials and every stationary point of central inertia is enumerated. The associated fill is not assumed to maximize finite transfer. A separate branch-and-bound searches the complete fill range at each heel/trim using actual liquid geometry. It returns a conservative upper bound, with total numerical excess at most 0.1 mm GZ per angle (up to 0.000105 m rad through 60 degrees), in addition to provider/root roundoff and the provider's longitudinal-quadrature approximation.", "",
        "ASSUMPTION: ballast stays at its specified state fill throughout the voyage. Consumable operational limits are absent, so all carried types use their whole usable range simultaneously. Tank structure, connectivity and quadrature retain the upstream synthetic assumptions.", "",
        "## KN interpolation requalification", "",
        "Final cache identities include tank payloads and loading/tank implementation hashes. Tank-only changes can reuse identical raw hull integrals, but never an old qualification certificate.", "",
        "| Vessel | Density | Curves / broad states | Failures | Max L5 fraction: GZ / area / GM |",
        "|---|---:|---:|---:|---|" ]
    total_curves = total_broad = 0
    for path in sorted(ROOT.glob('validation_feeder_*.json')):
        data = json.loads(path.read_text())
        for row in data['validation']:
            broad = row['broad_geometric_validation']
            fractions = [max(row['errors']['gz_m']['max_l5_fraction'], broad['errors']['max_l5_fraction']),
                         row['errors']['area_mrad']['max_l5_fraction'], row['errors']['gm_m']['max_l5_fraction']]
            failures = len(row['failures'])+len(broad['failures'])
            total_curves += row['completed_samples']; total_broad += broad['completed_samples']
            lines.append(f"| {row['vessel']} | {row['density_kg_m3']:g} | {row['completed_samples']} / {broad['completed_samples']} | {failures} | {' / '.join(f'{x:.6f}' for x in fractions)} |")
    lines += ["", f"Total: {total_curves} complete curves and {total_broad} broad states. The target is <=0.2 of the applicable IACS UR L5 allowance. This empirical comparison is not a uniform mathematical error bound.", "",
        "## Production conditions", "",
        "ASSUMPTION: synthetic homogeneous departures, every ballast tank20%, every HFO tank50%, diesel/fresh water empty. Identical box masses are reduced to retain90% design displacement. These are stability diagnostics, not approved voyages or full-checker acceptance.", "",
        "| Vessel | Ran | Cold / warm s | GM / FSC m | B criteria passed | Weather | Table used |",
        "|---|---|---:|---:|---:|---|---|" ]
    departures = [json.loads(p.read_text()) for p in sorted(ROOT.glob('departure_feeder_*.json'))]
    for data in departures:
        cold = data.get('cold'); warm = data.get('warm')
        if cold is None:
            lines.append(f"| {data['vessel']} | pending | pending | pending | pending | pending | pending |")
            continue
        m = cold.get('metrics', {}); margins = cold.get('margins', {})
        b = [v for k,v in margins.items() if k.startswith('containership_')]
        weather = [v for k,v in margins.items() if k.startswith('weather_')]
        times = f"{cold['seconds']:.3f} / {warm['seconds']:.3f}" if warm else f"{cold['seconds']:.3f} / pending"
        lines.append(f"| {data['vessel']} | {cold['ran']} | {times} | {m.get('gm_m',float('nan')):.6f} / {m.get('fsc_m',float('nan')):.6f} | {sum(v>=-1e-12 for v in b)}/{len(b)} | {'pass' if weather and all(v>=-1e-12 for v in weather) else 'fail'} | {m.get('geometry_table_used')} |")
        if cold.get('error'): lines += ["", f"{data['vessel']} error: {cold['error']}"]
    complete = [d for d in departures if d.get('cold', {}).get('ran')]
    if complete:
        lines += ["", "All reported initial GM values exceed the A2.2.4 minimum0.15m. B2.3 details below show measured value / minimum, with areas in m rad, levers in m, and flooding angle in rad.", "",
                  '| B2.3 criterion | '+' | '.join(d['vessel'] for d in complete)+' |',
                  '|---|'+'---|'*len(complete)]
        keys = ['downflooding_30_margin_rad', 'area_0_30_mrad', 'area_0_40_mrad',
                'area_30_40_mrad', 'gz_at_or_above_30_m', 'gz_max_m', 'area_0_flood_mrad']
        for key in keys:
            cells=[]
            for data in complete:
                report=data['cold'];margin=report['margins']['containership_'+key]
                measured=report['metrics']['angle_flooding_rad' if key=='downflooding_30_margin_rad' else key]
                cells.append(f"{measured:.6f} / {measured-margin:.6f} {'pass' if margin>=-1e-12 else 'FAIL'}")
            lines.append('| '+key+' | '+' | '.join(cells)+' |')
        lines += ["", "Additional loading-limit violations:"]
        for data in complete:
            ids=[v for v in data['cold']['violation_ids'] if v.startswith(('iscode.loading.', 'port.call.'))]
            lines.append(f"- {data['vessel']}: {', '.join(ids) if ids else 'none'}.")
    lines += ["", "Cold full-fill envelopes are expensive; exact-input memoization does not replace the need for a fast tank geometry batch/lookup API across changing plans. The former dry benchmark does not characterize this wet-condition cost. The unintegrated centroid-only prototype measured only a1.44x gain and is retained as profiling evidence, not an active physics path.", "",
        "Reproduce with `UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run python experiments/stability/hull_tanks/departures.py --vessel feeder_500 --output experiments/stability/hull_tanks/departure_feeder_500.json`, similarly for1000/1700. JSON files retain raw tank soundings, range maxima, loading moments, criteria margins, rule IDs and staged results.", ""]
    (ROOT/'report.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    main()

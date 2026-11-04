"""Read saved JSON only; never import/check/alter the accepted witness."""
from pathlib import Path
import hashlib
import json

HERE = Path(__file__).resolve().parent
WITNESS = HERE/'results/verified-combined'


def main():
    original = {name: (WITNESS/name).read_bytes()
                for name in ('input.json', 'plan.json', 'full-report.json', 'verification.json')}
    instance = json.loads(original['input.json'])
    report = json.loads(original['full-report.json'])
    verification = json.loads(original['verification.json'])
    assert report['passed'] and not report['violations']
    for name, field in (('input.json', 'input_sha256'), ('plan.json', 'plan_sha256'),
                        ('full-report.json', 'report_sha256')):
        assert hashlib.sha256(original[name]).hexdigest() == verification[field]
    hull = instance['vessel']['hull']
    rows = []
    for phase in ('departure', 'mid_voyage', 'arrival'):
        selected = {}
        for part in ('stability', 'lashing'):
            candidates = [(name, value) for name, value in report['parts'].items()
                          if name.startswith('condition:') and name.endswith(':'+part)
                          and value['metrics'].get('condition.phase: '+phase) == 1.]
            assert len(candidates) == 1, (phase, part)
            selected[part] = candidates[0]
        stability_name, stability = selected['stability']
        lashing_name, lashing = selected['lashing']
        sm, lm = stability['metrics'], lashing['metrics']
        aft, forward = sm['draught_aft_m'], sm['draught_fwd_m']
        maximum_draft = hull['max_draught_m']
        if phase == 'departure' and instance['port'].get('max_draught_m') is not None:
            maximum_draft = min(maximum_draft, instance['port']['max_draught_m'])
        area = sm['area_0_flood_mrad']
        margin = stability['margins']['containership_area_0_flood_mrad']
        row = dict(phase=phase, stability_part=stability_name, lashing_part=lashing_name,
            stability_solid_gm_m=sm['gm_solid_m'], physical_kg_m=sm['kg_m'],
            stability_fsc_m=sm['fsc_m'], stability_gm_m=sm['gm_m'],
            securing_gm_m=lm['gm_m'], securing_roll_period_s=lm['roll_period_s'],
            draft_aft_m=aft, draft_forward_m=forward, maximum_draft_m=maximum_draft,
            draft_headroom_m=maximum_draft-max(aft, forward),
            nominal_end_freeboard_m=hull['depth_m']-max(aft, forward),
            total_area_measured_mrad=area, total_area_limit_mrad=area-margin,
            total_area_margin_mrad=margin, maximum_rod_ratio=lm['max_lashing_ratio'],
            rod_ratio_headroom=1.-lm['max_lashing_ratio'])
        assert abs(row['stability_solid_gm_m']-row['stability_fsc_m']-row['stability_gm_m']) < 1e-10
        rows.append(row)
    result = dict(instance_id=verification['instance_id'], requested_loaded=verification['requested_loaded'],
        requested_boxes=verification['requested_boxes'], full_checker_passed=report['passed'],
        violations=len(report['violations']), lifts=report['count']['weighted_nonproductive'],
        independent_checker_s=verification['wall_s'],
        witness_sha256={name: hashlib.sha256(payload).hexdigest() for name, payload in original.items()},
        derivation='Saved full-report JSON only; explicit D57 phase reports, never aggregate aliases.',
        freeboard_definition='Hull depth minus maximum end draft; nominal geometric quantity, not regulatory load-line freeboard.',
        phases=rows)
    (HERE/'results/physical-bindings.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    lines = ['# Accepted D185 witness: physical bindings', '',
        f"`{result['instance_id']}`: **{result['requested_loaded']}/{result['requested_boxes']} requested boxes loaded; "
        f"{result['lifts']:g} lifts; unchanged full checker passed with {result['violations']} violations in "
        f"{result['independent_checker_s']:.2f} s.**", '',
        'Values below are extracted from the saved explicit voyage-phase reports. '
        'The accepted witness files were read only and their recorded hashes were checked.', '',
        '| Phase | Solid GM (m) | KG (m) | FSC (m) | Stability GM (m) | Securing GM (m) | Roll period (s) |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        lines.append('| '+r['phase']+' | '+' | '.join(f'{r[k]:.6f}' for k in
            ('stability_solid_gm_m', 'physical_kg_m', 'stability_fsc_m', 'stability_gm_m',
             'securing_gm_m', 'securing_roll_period_s'))+' |')
    lines += ['', 'Stability GM equals solid GM minus FSC. Securing GM comes from its distinct '
        'native loading calculation, including actual tilted-liquid tangent free surfaces; '
        'these two GM values are not interchangeable.', '',
        '| Phase | Aft draft (m) | Forward draft (m) | Draft limit (m) | Headroom (mm) | Nominal end freeboard (m) |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        lines.append(f"| {r['phase']} | {r['draft_aft_m']:.6f} | {r['draft_forward_m']:.6f} | "
                     f"{r['maximum_draft_m']:.6f} | {1000*r['draft_headroom_m']:.3f} | {r['nominal_end_freeboard_m']:.6f} |")
    lines += ['', 'Nominal end freeboard here is hull depth minus the larger end draft. '
        'It is a geometric summary, not a regulatory load-line freeboard calculation.', '',
        '| Phase | Total GZ area (m rad) | Required area (m rad) | Area margin (m rad) | Maximum rod ratio | Rod headroom (%) |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        lines.append(f"| {r['phase']} | {r['total_area_measured_mrad']:.9f} | {r['total_area_limit_mrad']:.9f} | "
                     f"{r['total_area_margin_mrad']:.9f} | {r['maximum_rod_ratio']:.9f} | {100*r['rod_ratio_headroom']:.6f} |")
    lines += ['', 'Required total area is recovered exactly from measured area minus the checker’s signed margin; '
        'it includes the phase-specific containership form factor and applicable tightening.', '',
        'Reproduce this JSON-only extraction with `summarize_bindings.py`. '
        'Machine-readable values and witness hashes: `results/physical-bindings.json`.', '']
    (HERE/'BINDINGS.md').write_text('\n'.join(lines))
    for name, payload in original.items():
        assert (WITNESS/name).read_bytes() == payload
    print(json.dumps({'written': ['BINDINGS.md', 'results/physical-bindings.json'], 'phases': rows}, indent=2))


if __name__ == '__main__':
    main()

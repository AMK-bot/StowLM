"""Offline handover views. Every displayed number comes from the supplied checker report."""
from __future__ import annotations

import math
from html import escape

from stowage.conventions import cells_of_slot, parse_slot, rows_port_to_starboard
from stowage.model import CheckReport, Instance, MovePlan
from stowage.moves import apply_plan

from .confirmation import canonical_rule_json, canonical_rule_label, confirmation_label


def _part(part) -> dict:
    if part is None:
        return dict(state='unavailable', ran=False, passed=None, error='Checker part absent',
                    metrics={}, margins={}, violations=[])
    passed = bool(part.ran and not part.error and
                  not any(v.severity == 'hard' for v in part.violations))
    return dict(state=('unavailable' if not part.ran or part.error else 'passed' if passed else 'failed'),
                ran=part.ran, passed=passed if part.ran and not part.error else None,
                error=part.error, metrics=dict(part.metrics), margins=dict(part.margins),
                violations=[v.model_dump(mode='json') for v in part.violations])


def stability_summary(instance: Instance, plan: MovePlan, report: CheckReport) -> dict:
    """Extract departure evidence; import stage physics for limits and input identity, never rerun physics.

    The stage-physics frame builder draws a new numerical GZ curve. A handover needs the already checked
    scalar evidence instead, so only its header/identity helpers are used here. Unknowns remain null.
    """
    from app.stage_physics import PHYSICS_VERSION, input_digests, physics_header

    stability = _part(report.parts.get('stability'))
    strength = _part(report.parts.get('strength'))
    securing = _part(report.parts.get('lashing'))
    metrics = stability['metrics']
    header = physics_header(instance, 'departure-only')
    hull = instance.vessel.hull
    limits = [x for x in (getattr(hull, 'max_draught_m', None), instance.port.max_draught_m)
              if x is not None]
    return dict(
        synthetic=True, label='Measured checker output on synthetic data',
        source='CheckReport.parts; app.stage_physics.physics_header',
        physics_version=PHYSICS_VERSION, input_digests=input_digests(instance, plan),
        scope=('GM, trim, draughts and strength ratios describe departure; margins and securing verdict cover '
               'checker-reported sampled conditions. No continuous-voyage or cargo-work ballast programme assessment.'),
        checker_passed=report.passed,
        gm_m=metrics.get('gm_m'), trim_m=metrics.get('trim_m'),
        heel_deg=math.degrees(metrics['list_rad']) if metrics.get('list_rad') is not None else None,
        draught_aft_m=metrics.get('draught_aft_m'), draught_fwd_m=metrics.get('draught_fwd_m'),
        draught_mean_m=metrics.get('draught_mean_m'),
        max_draught_m=min(limits) if limits else None,
        max_sf_ratio=strength['metrics'].get('max_sf_ratio'),
        max_bm_ratio=strength['metrics'].get('max_bm_ratio'),
        strength_margins=dict(strength['margins']),
        stability=stability, strength=strength, securing=securing,
        strength_limits={k: header[k] for k in ('stations_x_m', 'sf_min_n', 'sf_max_n',
                                               'bm_min_nm', 'bm_max_nm')},
        securing_note='Synthetic securing arrangement; the checker verdict is not a vessel-specific Cargo Securing Manual.',
    )


_COLORS = ('#b8ddf0', '#f9d28a', '#c8e4b5', '#d5c4ec', '#f4bfc7', '#a9dfd7', '#e8d7b9', '#cad0f0')


def _number(value, unit='', digits=2) -> str:
    return 'unavailable' if value is None else f'{value:.{digits}f}{unit}'


def _summary_html(summary: dict) -> str:
    fields = (
        ('GM', _number(summary.get('gm_m'), ' m')),
        ('Trim (aft − fwd)', _number(summary.get('trim_m'), ' m')),
        ('Draught aft / fwd', f"{_number(summary.get('draught_aft_m'))} / {_number(summary.get('draught_fwd_m'))} m"),
        ('SF headroom', _number(summary.get('strength_margins', {}).get('sf_ratio'), ' of limit', 3)),
        ('BM headroom', _number(summary.get('strength_margins', {}).get('bm_ratio'), ' of limit', 3)),
        ('Securing', summary.get('securing', {}).get('state', 'unavailable')),
    )
    return '<dl class="summary">' + ''.join(
        f'<div><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>' for label, value in fields) + '</dl>'


def _profile(bays: list[int], counts: dict[int, int], active: int, background=()) -> str:
    width = max(1, len(bays)) * 42 + 24
    pattern_id = f'background-hatch-{active:03d}'
    blocks = []
    for i, bay in enumerate(bays):
        x, height = 12 + i * 42, 5 + min(counts.get(bay, 0), 25)
        fill = f'url(#{pattern_id})' if bay in background else '#163c53' if bay == active else '#bdcdd5'
        blocks.append(f'<rect data-profile-bay="{bay:03d}" data-background="{str(bay in background).lower()}" '
                      f'x="{x}" y="{36-height}" width="37" height="{height}" fill="{fill}"/>'
                      f'<text x="{x+18}" y="49" text-anchor="middle">{bay:03d}</text>')
    return (f'<svg class="profile" viewBox="0 0 {width} 53" role="img" '
            f'aria-label="Longitudinal side elevation; bay {active:03d} highlighted">'
            '<title>Longitudinal side elevation, schematic cargo occupancy, not to scale</title>'
            f'<defs><pattern id="{pattern_id}" width="5" height="5" patternUnits="userSpaceOnUse">'
            '<rect width="5" height="5" fill="#e5e9eb"/><path d="M0 5L5 0" stroke="#637984" stroke-width="1.5"/></pattern></defs>'
            f'<path d="M 2 35 L {width-2} 35 L {width-9} 41 L 9 41 Z" fill="#e5eaee"/>'
            + ''.join(blocks) + '</svg>')


def render_bay_plan(instance: Instance, plan: MovePlan, report: CheckReport, stability: dict,
                    *, banner: str, scope: dict | None = None, rules=None, confirmation=None) -> str:
    """Self-contained A3 landscape HTML: one physical 20-foot bay per printed page.

    The principal diagram is a transverse bay section looking forward, with a longitudinal side elevation
    for orientation. Long boxes appear in both supporting physical bays, labelled with their shared slot.
    An illegal replay is refused rather than drawing a misleading partial departure plan.
    """
    replay = apply_plan(instance, plan)
    if not replay.legal or replay.executed != len(plan.moves):
        raise ValueError('Cannot render bay plan: move replay failed')
    cells = {cell.id: cell for cell in instance.vessel.cells}
    boxes = {box.id: box for box in instance.containers}
    occupied = {}
    for placement in replay.final_state.ship.placements:
        for cell in cells_of_slot(placement.slot):
            occupied[cell] = placement
    bays = sorted({parse_slot(cell).bay for cell in cells})
    pods = sorted({boxes[p.container_id].pod for p in replay.final_state.ship.placements})
    colors = {pod: _COLORS[i] if i < len(_COLORS) else f'hsl({i*137 % 360} 48% 83%)'
              for i, pod in enumerate(pods)}
    legend = ' '.join(f'<span class="pod" style="--pod:{colors[pod]}">{escape(pod)}</span>' for pod in pods)
    counts = {bay: sum(parse_slot(cell).bay == bay for cell in occupied) for bay in bays}
    profile_bays = scope['all_bays'] if scope else bays
    background_bays = scope['background_bays'] if scope else []
    for load in instance.vessel.fixed_loads:
        for box in load.cargo_boxes or []:
            for cell in box.cell_ids:
                bay = parse_slot(cell).bay
                counts[bay] = counts.get(bay, 0) + 1
    weights = ''
    if scope:
        weight_rows = ''.join(f'<tr><th>{escape(row["component"])}</th><td>{row["mass_t"]:,.3f} t</td></tr>'
                              for row in scope['weights'] if row['mass_kg'] != 0)
        departure = scope['displacement_kg']
        checker_total = 'unavailable' if departure is None else f'{departure / 1000:,.3f} t'
        weights = (f'<div class="weight-account"><b>Departure weight account</b><table class="weight-table">{weight_rows}'
                   f'<tr><th>Total mass aboard</th><td>{scope["weight_total_kg"] / 1000:,.3f} t</td></tr>'
                   f'<tr><th>Checker displacement</th><td>{checker_total}</td></tr></table>'
                   f'<p>{"Closes to checker displacement." if scope["weight_closes"] else "Checker displacement unavailable; closure unverified."} '
                   'Closed hatch covers are included in lightship.</p></div>')
    summary = _summary_html(stability)
    instructions = ''.join(
        '<div class="confirmed-instruction">'
        f'<p><b>Confirmed instruction:</b> {escape(row["sentence"])}</p>'
        f'<p><b>Canonical rule:</b> {escape(canonical_rule_label(row))}</p>'
        f'<p class="canonical-reading">{escape(canonical_rule_json(row))}</p>'
        f'<p>{escape(confirmation_label(row, confirmation))}</p></div>'
        for row in (rules or []) if row.get('confirmed') is True)
    if instructions:
        instructions = '<div class="instructions" aria-label="Confirmed voyage instructions">' + instructions + '</div>'
    pages = []
    for page, bay in enumerate(bays, 1):
        addresses = [parse_slot(cell) for cell in cells if parse_slot(cell).bay == bay]
        rows = rows_port_to_starboard(sorted({a.row for a in addresses}))
        tiers = sorted({a.tier for a in addresses}, reverse=True)
        lines = ['<thead><tr><th>Tier</th>' + ''.join(f'<th>{row:02d}</th>' for row in rows) + '</tr></thead><tbody>']
        previous = None
        for tier in tiers:
            deck_boundary = previous is not None and previous >= 80 > tier
            lines.append(f'<tr class="{"deck-line" if deck_boundary else ""}"><th>{tier:02d}</th>')
            for row in rows:
                cell = f'{bay:03d}{row:02d}{tier:02d}'
                placement = occupied.get(cell)
                if cell not in cells:
                    lines.append('<td class="void" aria-label="No vessel cell"></td>')
                elif placement is None:
                    lines.append(f'<td class="empty" data-cell="{cell}"><span>·</span></td>')
                else:
                    box = boxes[placement.container_id]
                    dg = (f'<strong class="dg">◆ DG {escape(box.dg.imdg_class)} / UN {escape(box.dg.un_number)}</strong>'
                          if box.dg else '')
                    marks = (' RF' if box.reefer else '') + (' OOG' if box.oog else '')
                    shared = ' ↔' if box.length_ft > 20 else ''
                    lines.append(f'<td class="box" data-cell="{cell}" data-box="{escape(box.id)}" '
                                 f'style="--pod:{colors[box.pod]}"><b>{escape(box.id)}</b>'
                                 f'<span>{box.gross_mass_kg/1000:.2f} t · {escape(box.pod)}</span>'
                                 f'<span>{box.length_ft}′ · {placement.slot}{shared}{marks}</span>{dg}</td>')
            lines.append('</tr>')
            previous = tier
        lines.append('</tbody>')
        pages.append(
            f'<section class="bay-page" data-bay="{bay:03d}"><div class="banner">{escape(banner)}</div>'
            f'{instructions}'
            f'<header><div><p class="eyebrow">StowLM · synthetic handover</p><h1>Bay {bay:03d}</h1>'
            f'<p>{escape(instance.vessel.name)} · {escape(instance.port.locode)} · plan {escape(plan.id)}</p></div>'
            f'<div class="page-id">{page} / {len(bays)}<br>Departure condition</div></header>'
            f'{summary}<div class="orientation"><span>PORT</span><span>Bay section looking forward · rows / tiers</span><span>STARBOARD</span></div>'
            f'<div class="diagram"><table aria-label="Bay {bay:03d} container stow">{"".join(lines)}</table></div>'
            f'<div class="lower"><div><p><b>POD colours</b> {legend}</p>'
            '<p>◆ DG = dangerous goods (class / UN number) · RF = reefer · OOG = out of gauge · t = gross tonnes.<br>'
            '↔ = shared 40′/45′ slot, repeated in both supporting bays; do not count it twice. Empty cells: ·</p></div>'
            f'<div><p>Longitudinal side elevation · schematic occupancy</p>{_profile(profile_bays, counts, bay, background_bays)}'
            '<p>Hatched bays = fixed synthetic background cargo, outside the planned section.</p>'
            f'{weights}</div></div>'
            f'<footer><span>Measured checker output on synthetic data. Full checker: {"PASS" if report.passed else "FAIL"}. '
            'Synthetic securing arrangement; no vessel-specific Cargo Securing Manual. Positive strength headroom = inside limit.</span>'
            '<span>Departure scalars; minimum margins across sampled conditions. See stability.json and receipt.json.</span></footer></section>')
    # The warning is physically the first line as well as visibly repeated on every printed page.
    first_line = escape(banner).replace('--', '—')
    return f'<!-- {first_line} -->\n' + '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Synthetic bay plan · StowLM</title><style>
*{box-sizing:border-box}body{margin:0;background:#e9eef1;color:#142d3d;font:12px Arial,sans-serif}
.bay-page{background:white;width:400mm;height:275mm;padding:8mm;margin:7mm auto;display:flex;flex-direction:column;gap:3mm;overflow:hidden}
.banner{font-size:11px;font-weight:bold;border:1px solid #8c4e16;background:#fff4d9;color:#53300d;padding:2mm 3mm}
.instructions{font-size:10px;border-left:2px solid #163c53;padding-left:2mm}.confirmed-instruction+.confirmed-instruction{margin-top:1mm}.instructions p{overflow-wrap:anywhere}.canonical-reading{font-size:8px;color:#526877}
header{display:flex;justify-content:space-between;align-items:center}h1{font-size:30px;line-height:1;margin:1mm 0}p{margin:0;line-height:1.4}.eyebrow{text-transform:uppercase;letter-spacing:1.8px;font-size:10px}.page-id{text-align:right}
.summary{display:grid;grid-template-columns:repeat(6,1fr);margin:0;border-top:2px solid #163c53;border-bottom:1px solid #ced9df;padding:2.5mm 0;gap:3mm}.summary dt{font-size:10px;color:#526877}.summary dd{font-size:14px;font-weight:bold;margin:1mm 0 0}
.orientation{display:flex;justify-content:space-between;font-size:10px;letter-spacing:.6px}.diagram{flex:1;min-height:0}table{border-collapse:collapse;table-layout:fixed;width:100%;height:100%;font-size:11px}thead{height:6mm}th{font-size:10px;color:#526877;font-weight:normal}th:first-child{width:9mm}td{border:1px solid #a3b4be;text-align:center;padding:1mm;line-height:1.15}td b,td span,td strong{display:block;white-space:nowrap}.box{background:var(--pod)}.box b{font-size:12px;letter-spacing:.3px}.box span{margin-top:.7mm}.empty{background:#f5f8fa;color:#99aebc}.void{border:0;background:repeating-linear-gradient(135deg,#fff,#fff 3px,#f4f6f7 3px,#f4f6f7 4px)}.deck-line td,.deck-line th{border-top:3px solid #163c53}.dg{color:#7b1420;font-size:10px;margin-top:.7mm}.lower{display:grid;grid-template-columns:1fr 1fr;gap:8mm;font-size:10px}.lower p{margin-bottom:1mm}.pod{display:inline-block;background:var(--pod);border:1px solid #8da1ad;padding:1mm 2mm;margin:0 1mm 1mm 0}.profile{width:100%;height:15mm}.profile text{font:9px Arial,sans-serif;fill:#142d3d}footer{border-top:1px solid #ced9df;padding-top:2mm;display:flex;justify-content:space-between;gap:8mm;font-size:9px;color:#526877}
.weight-account{font-size:9px;margin-top:1mm}.weight-table{height:auto;table-layout:auto;font-size:9px}.weight-table th:first-child{width:auto;text-align:left;font-size:9px}.weight-table td{width:26mm;padding:.3mm;text-align:right}.weight-table th{padding:.3mm}.weight-account p{font-size:8px}
@page{size:A3 landscape;margin:10mm}@media print{body{background:white;-webkit-print-color-adjust:exact;print-color-adjust:exact}.bay-page{width:400mm;height:275mm;margin:0;padding:4mm;break-after:page;page-break-after:always}.bay-page:last-child{break-after:auto;page-break-after:auto}}
@media screen and (max-width:900px){.bay-page{transform-origin:top left;margin:3mm;width:1100px}.box b{font-size:10px}}
</style></head><body>''' + ''.join(pages) + '</body></html>\n'

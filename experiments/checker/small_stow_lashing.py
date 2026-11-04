"""Read-only attribution of every securing violation in six frozen G1 stows.

Public checks use original background, original liquids and replayed stow.
Counterfactual checks patch only this process's loading-condition lookup: they
hold the complete securing graph fixed while substituting a separately solved
condition, to distinguish local stack loading from whole-ship motion effects.
They are diagnostics, never full-checker approval or an operational condition.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'

from small_stow_common import background_only, cases, load_case, source_identity
from stowage.checker import lashing, visibility
from stowage.conventions import parse_slot

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE.parent / 'ballast/review_fix/diagnostics.json'


def condition_dict(condition):
    return {key: value for key, value in vars(condition).items() if key != 'geometry'}


def passed(report):
    return report.ran and not any(v.severity == 'hard' for v in report.violations)


def report_dict(report):
    return report.model_dump(mode='json')


def metric_name(violation):
    return 'container_mass_rating' if violation.unit == 'kg' else violation.message.split(' exceeds')[0]


def grouped_violations(report):
    groups = defaultdict(list)
    for violation in report.violations:
        groups[(metric_name(violation), tuple(violation.slots))].append(violation.measured)
    return {f'{metric}|{",".join(slots)}': max(values) for (metric, slots), values in groups.items()}


def check_with_trace(instance, ship):
    """Observe production record() calls, including ratios below the limit."""
    records = []
    def observe(frame, event, arg):
        if event == 'call' and frame.f_code.co_name == 'record' and frame.f_code.co_filename == lashing.__file__:
            fields = frame.f_locals
            records.append(dict(metric=fields['key'], value=float(fields['value']), slots=list(fields['slots'])))
    previous = sys.getprofile()
    sys.setprofile(observe)
    try:
        report = lashing.check_lashing(instance, ship, call_index=instance.call_index)
    finally:
        sys.setprofile(previous)
    return report, records


def grouped_records(records):
    result = defaultdict(list)
    for record in records:
        key = f'{record["metric"]}|{",".join(record["slots"])}'
        result[key].append(record['value'])
    return result


def diagnostic(instance, ship):
    condition = visibility._loading_condition(instance, ship)
    hull = instance.vessel.hull
    draught = (condition.t_aft_m + condition.t_fwd_m) / 2
    volume = condition.geometry.submerged(hull.scantling_draught_m, hull.scantling_draught_m).volume_m3
    cb = volume / (hull.lpp_m * hull.beam_m * hull.scantling_draught_m)
    motions = lashing._motions(hull, draught, condition.gm_m, cb)
    graph = lashing._load_graph(instance, ship, motions)
    geometries = {q.placement.slot: q for q in visibility._all_cargo_geometry(instance, ship)}
    boxes = {}
    stack_slots = defaultdict(list)
    for slot, load in graph.items():
        geometry = geometries[slot]
        address = parse_slot(slot)
        stack_ids = sorted({cell.stack_id for cell in geometry.physical})
        boxes[slot] = dict(slot=slot, container_id=load.container.id,
            owner='background' if geometry.background else 'planned',
            physical_stack_ids=stack_ids, physical_cell_ids=[q.id for q in geometry.physical],
            bay=address.bay, row=address.row, tier=address.tier,
            gross_mass_kg=load.container.gross_mass_kg, securing_mass_kg=load.mass,
            floor_m=load.floor, height_m=load.height, cg_height_m=load.cg_height,
            parents=load.parents, acceleration_m_s2=dict(longitudinal=load.al, transverse=load.at, vertical=load.av))
        for stack_id in stack_ids:
            stack_slots[stack_id].append(slot)
    stacks = {}
    for stack_id, slots in sorted(stack_slots.items()):
        local = [boxes[slot] for slot in slots]
        stacks[stack_id] = dict(slots=slots,
            planned_mass_kg=sum(q['gross_mass_kg'] for q in local if q['owner'] == 'planned'),
            background_mass_kg=sum(q['gross_mass_kg'] for q in local if q['owner'] == 'background'),
            planned_tiers=[q['tier'] for q in local if q['owner'] == 'planned'],
            background_tiers=[q['tier'] for q in local if q['owner'] == 'background'])
    return condition, boxes, stacks


def run_case(case, archive):
    instance, plan, ship = load_case(case)
    bg_ship = background_only(ship)
    original, original_records = check_with_trace(instance, ship)
    background = lashing.check_lashing(instance, bg_ship, call_index=instance.call_index)
    condition, boxes, stacks = diagnostic(instance, ship)
    bg_condition = visibility._loading_condition(instance, bg_ship)
    with patch.object(visibility, '_loading_condition', return_value=bg_condition):
        same_graph_bg_motions, bg_motion_records = check_with_trace(instance, ship)
    with patch.object(visibility, '_loading_condition', return_value=condition):
        bg_graph_full_motions = lashing.check_lashing(instance, bg_ship, call_index=instance.call_index)

    # Diagnostic only: remove background cargo mass and boxes from a private
    # copy, retaining all non-cargo constants, liquids, planned cargo and hull.
    # No model builder, frozen input or production checker is changed.
    planned_vessel = instance.vessel.model_copy(update={'fixed_loads':[
        load for load in instance.vessel.fixed_loads if load.kind != 'cargo']})
    planned_instance = instance.model_copy(update={'vessel': planned_vessel})
    try:
        planned_condition = visibility._loading_condition(planned_instance, ship)
        with patch.object(visibility, '_loading_condition', return_value=planned_condition):
            same_graph_planned_motions, planned_motion_records = check_with_trace(instance, ship)
        with patch.object(visibility, '_loading_condition', return_value=replace(condition, gm_m=planned_condition.gm_m)):
            same_graph_planned_gm, planned_gm_records = check_with_trace(instance, ship)
        planned_counterfactual = dict(condition=condition_dict(planned_condition),
            complete_graph_report=report_dict(same_graph_planned_motions),
            gm_only_complete_graph_report=report_dict(same_graph_planned_gm))
    except (ValueError, ArithmeticError) as exc:
        planned_counterfactual = dict(error=str(exc))
        same_graph_planned_motions = None
        planned_motion_records, planned_gm_records = [], []

    bg_groups = grouped_violations(background)
    bg_full_groups = grouped_violations(bg_graph_full_motions)
    alternative_groups = grouped_violations(same_graph_bg_motions)
    planned_groups = grouped_violations(same_graph_planned_motions) if same_graph_planned_motions else {}
    original_group_records = grouped_records(original_records)
    bg_motion_group_records = grouped_records(bg_motion_records)
    planned_motion_group_records = grouped_records(planned_motion_records)
    planned_gm_group_records = grouped_records(planned_gm_records)
    occurrence = Counter()
    attribution = []
    for index, violation in enumerate(original.violations):
        local = [boxes[slot] for slot in violation.slots]
        owners = {q['owner'] for q in local}
        stack_ids = sorted({sid for q in local for sid in q['physical_stack_ids']})
        key = f'{metric_name(violation)}|{",".join(violation.slots)}'
        if metric_name(violation) == 'container_mass_rating':
            counterfactual_ratios = None
        else:
            # The checker emits violations only for failed record() calls. Walk
            # the original calls in order for each metric/slot group, retaining
            # the same occurrence in every unchanged-graph counterfactual.
            pos = occurrence[key]
            values = original_group_records[key]
            while pos < len(values) and abs(values[pos] - violation.measured) > 1e-10:
                pos += 1
            assert pos < len(values), (case, key, violation.measured)
            occurrence[key] = pos + 1
            counterfactual_ratios = dict(record_occurrence=pos, original=values[pos],
                background_only_motions=bg_motion_group_records[key][pos],
                no_background_mass_motions=planned_motion_group_records[key][pos] if key in planned_motion_group_records else None,
                no_background_mass_gm_only=planned_gm_group_records[key][pos] if key in planned_gm_group_records else None)
        owner = 'mixed local stack' if len(owners) > 1 else next(iter(owners), 'unknown')
        attribution.append(dict(index=index, violation=report_dict(violation), metric=metric_name(violation),
            local_cause=owner, affected_stack_ids=stack_ids,
            stack_inventory={sid: stacks[sid] for sid in stack_ids},
            survives_background_only=key in bg_groups,
            background_only_group_max=bg_groups.get(key),
            background_graph_full_motions_group_max=bg_full_groups.get(key),
            full_graph_background_only_motions_group_max=alternative_groups.get(key),
            full_graph_no_background_mass_motions_group_max=planned_groups.get(key),
            same_record_counterfactual_ratios=counterfactual_ratios,
            global_interpretation=('mass-rating failure is independent of motions' if metric_name(violation) == 'container_mass_rating'
                else 'compare same graph with substituted motions; absent group means this metric/slot group has no violation, not necessarily zero demand')))
    archived = archive[case]['departure']['parts']['lashing']
    assert original.model_dump(mode='json') == archived, f'Current lashing differs from archived initial condition: {case}'
    assert len(attribution) == len(original.violations)
    counts = Counter(item['local_cause'] for item in attribution)
    metric_owners = defaultdict(Counter)
    for item in attribution:
        metric_owners[item['metric']][item['local_cause']] += 1
    offending_stacks = sorted({sid for row in attribution for sid in row['affected_stack_ids']})
    return dict(case=case, original_report=report_dict(original), background_only_report=report_dict(background),
        background_only_passed=passed(background), original_condition=condition_dict(condition),
        background_only_condition=condition_dict(bg_condition),
        counterfactual_full_graph_background_only_motions=report_dict(same_graph_bg_motions),
        counterfactual_background_graph_full_motions=report_dict(bg_graph_full_motions),
        counterfactual_no_background_mass=planned_counterfactual,
        archived_initial_report_exact_match=True,
        counts=dict(counts), metric_owner_counts={key: dict(value) for key, value in metric_owners.items()},
        offending_stack_ids=offending_stacks, stacks=stacks, boxes=boxes, every_violation=attribution)


def markdown(rows):
    lines = ['# Frozen small-stow lashing attribution', '',
        'All original reports match archived initial-state lashing reports exactly. Counts retain repeated physical end / stiffness / direction checks; they are not unique stacks.', '',
        'Stack masses are sums of the gross masses of boxes touching each physical support column. A long box appears in both support columns; these masses must not be added across columns. Per-box records identify both physical cells and the exact parent end-frame links.', '',
        '| Case | Records planned / background / mixed | Background-only pass | GM full → background-only (m) | Max fitting / racking / guide full | Max fitting / racking / guide background-only |',
        '|---|---:|---|---:|---:|---:|']
    for row in rows:
        def ratios(report):
            return ' / '.join(f'{report["metrics"][key]:.5f}' for key in ('max_fitting_ratio','max_racking_ratio','max_cell_guide_ratio'))
        count = row['counts']
        lines.append(f'| {row["case"]} | {count.get("planned",0)} / {count.get("background",0)} / {count.get("mixed local stack",0)} | {row["background_only_passed"]} | {row["original_condition"]["gm_m"]:.5f} → {row["background_only_condition"]["gm_m"]:.5f} | {ratios(row["original_report"])} | {ratios(row["background_only_report"])} |')
    for row in rows:
        lines += ['', f'## {row["case"]}', '',
            '| Physical stack | Planned mass (kg); tiers | Background mass (kg); tiers |', '|---|---|---|']
        for stack_id in row['offending_stack_ids']:
            stack = row['stacks'][stack_id]
            lines.append(f'| {stack_id} | {stack["planned_mass_kg"]:.3f}; {stack["planned_tiers"]} | {stack["background_mass_kg"]:.3f}; {stack["background_tiers"]} |')
    lines += ['', 'The JSON retains every violation, its graph-parent links through the box catalog, all per-box masses / tiers / accelerations, and counterfactual reports. Counterfactual substituted motions are diagnostic only and are not full-checker approvals. Removing background cargo yields a light condition outside the declared operating range; it is used only to isolate motion sensitivity, not to assert that this alternative could sail.']
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    started = time.monotonic()
    identity_start = source_identity()
    archive = {row['case']: row for row in json.loads(ARCHIVE.read_text())}
    rows = []
    for entry in cases():
        case = entry['case'] if isinstance(entry, dict) else entry
        rows.append(run_case(case, archive))
        print(json.dumps(dict(case=case, counts=rows[-1]['counts'], background_only_passed=rows[-1]['background_only_passed'])), flush=True)
    identity_end = source_identity()
    changed = [path for path, value in identity_start['files'].items()
               if identity_end['files'].get(path) != value]
    relevant = lambda path: (path in {'src/stowage/checker/lashing.py', 'src/stowage/checker/visibility.py'}
        or path.startswith(('src/stowage/model/', 'src/stowage/vessel/', 'params/fleet/')))
    output = dict(purpose=__doc__, source_identity_start=identity_start, source_identity_end=identity_end,
        source_unchanged=identity_start == identity_end, changed_source_paths=changed,
        lashing_loading_condition_sources_unchanged=not any(relevant(path) for path in changed),
        wall_s=time.monotonic()-started, rows=rows)
    (HERE / 'small-stow-lashing.json').write_text(json.dumps(output, indent=2) + '\n')
    (HERE / 'small-stow-lashing.md').write_text(markdown(rows))
    print(json.dumps(dict(wall_s=output['wall_s'], source_unchanged=output['source_unchanged'])), flush=True)

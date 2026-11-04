"""Pure design-envelope gate; fill is of the planned section, never the parent ship."""
from __future__ import annotations

from stowage.model import Instance


def check(instance: Instance) -> dict:
    """Screen demand, not feasibility. Fixed synthetic background is not load-list cargo."""
    boxes = {box.id: box for box in instance.containers}
    demand = sum(1 if boxes[cid].length_ft == 20 else 2 for cid in instance.load_list)
    capacity = len(instance.vessel.cells)
    fill = demand / capacity if capacity else None
    ship = instance.vessel.slice_of or instance.vessel.id.split('.slice_', 1)[0]
    section = ('half-ship section' if instance.size_class == 'medium' and instance.vessel.slice_of
               else 'section' if instance.vessel.slice_of else 'full ship')
    calls = 1 + len(set(instance.plan_calls) | {row.call_index for row in instance.forecasts})
    numbers = dict(boxes_to_load=len(instance.load_list), box_limit=195,
                   physical_cell_demand=demand, physical_cell_capacity=capacity,
                   physical_cell_fill=fill, fill_limit=.70,
                   onboard_boxes=len(instance.onboard.placements), loading_calls=calls,
                   discharge_boxes=len(instance.discharge_list), ship=ship,
                   stage=instance.stage, planned_region=section,
                   fill_scope='physical cells in the planned region',
                   section_of=instance.vessel.slice_of)
    reasons = []

    def fail(test, measured, limit, message):
        reasons.append(dict(test=test, measured=measured, limit=limit, message=message))

    if not instance.load_list:
        fail('load_list', 0, 1, '0 boxes to load; supply at least 1 load-list box before planning.')
    elif len(instance.load_list) > 195:
        fail('size', len(instance.load_list), 195,
             f'{len(instance.load_list)} boxes to load; limit 195 boxes in the planned section.')
    if instance.size_class not in ('tiny', 'small', 'medium'):
        fail('size_class', instance.size_class, ['tiny', 'small', 'medium'],
             f'Size class {instance.size_class}; supported limit is medium (195 boxes).')
    if (instance.size_class == 'medium' or instance.vessel.slice_of is None) and ship not in ('feeder_500', 'feeder_800'):
        fail('ship', ship, ['feeder_500', 'feeder_800'],
             f'{section.capitalize()} of {ship}; limit is a half-ship section of tested feeder_500 or feeder_800 (500–800 TEU), or a tiny/small section.')
    if instance.size_class == 'medium' and instance.vessel.slice_of is None:
        fail('section', 'full ship', 'half-ship section',
             'Medium planning requires a half-ship section; select a medium section instead of a full ship.')
    if not capacity:
        fail('capacity', 0, 1, '0 physical cells; at least 1 cell is required to measure fill.')
    elif demand * 10 > capacity * 7:
        fail('fill', fill, .70,
             f'Physical fill {100 * fill:.3f}% ({demand}/{capacity} cells in the planned {section}); limit 70%.')
    if instance.onboard.placements:
        fail('arrival', len(instance.onboard.placements), 0,
             f'{len(instance.onboard.placements)} boxes onboard on arrival; limit 0 boxes.')
    if instance.discharge_list:
        fail('discharge', len(instance.discharge_list), 0,
             f'{len(instance.discharge_list)} boxes to discharge; limit 0 boxes.')
    if calls != 1:
        fail('loading_calls', calls, 1, f'{calls} loading calls; limit 1 loading call.')
    if instance.stage != 'A':
        fail('stage', instance.stage, 'A',
             f'Stage {instance.stage}; limit is stage A (1 loading call, 0 arrival boxes).')
    return dict(in_envelope=not reasons, size_class=instance.size_class,
                reasons=reasons, numbers=numbers)

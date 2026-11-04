"""Read-only D42 producer -> public 3.3 checker integration, not a full certificate."""
import json
from pathlib import Path
import time

from stowage.checker.lashing import check_lashing
from stowage.checker.visibility import check_visibility
from stowage.model import ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_vessel
from stowage.vessel.fleet_conditions import departure_tank_masses
from stowage.vessel.fleet_slices import slice_preset


def main():
    started = time.monotonic()
    vessel = slice_preset(load_vessel('feeder_500'), 'tiny')
    # Unplaced synthetic fixture boxes contribute no mass. Only the produced
    # background and its tanks are evaluated, without inventing six decision
    # placements. This probe is deliberately not a departure approval.
    instance = build_instance().model_copy(update={
        'vessel': vessel, 'load_list': [],
        'onboard': ShipState(vessel_id=vessel.id, tank_masses_kg=departure_tank_masses(vessel)),
    })
    expected = sum(len(load.cargo_boxes or []) for load in vessel.fixed_loads)
    lashing = check_lashing(instance, instance.onboard, call_index=0)
    visibility = check_visibility(instance, instance.onboard, call_index=0)
    result = dict(expected_background_boxes=expected,
                  aggregate_mass_kg=sum(load.mass_kg for load in vessel.fixed_loads),
                  lashing=lashing.model_dump(mode='json'),
                  visibility=visibility.model_dump(mode='json'), wall_s=time.monotonic()-started)
    Path(__file__).with_name('producer-probe.json').write_text(json.dumps(result, indent=2)+'\n')
    assert lashing.ran, lashing.error
    assert lashing.metrics['physical_box_count'] == expected
    assert visibility.ran, visibility.error
    print(json.dumps(dict(boxes=expected, lashing_violations=len(lashing.violations),
                          visibility_violations=len(visibility.violations), wall_s=result['wall_s'])))


if __name__ == '__main__':
    main()

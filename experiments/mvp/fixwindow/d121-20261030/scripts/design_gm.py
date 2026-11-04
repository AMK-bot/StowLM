from pathlib import Path
import json
import math
import sys
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.vessel.fleet import load_vessel, load_ship_params
from stowage.vessel.fleet_lashing_design import populate_lashing_design
assert rule_freeze_v1_enabled() is False
run = Path(sys.argv[1])
prior = {'feeder_500': 3.2838356051426465,
         'feeder_800': 3.9584570964621637,
         'feeder_1700': 5.530108952747626}
rows = []
for name, expected in prior.items():
    vessel = load_vessel(name)
    audit = populate_lashing_design(vessel, load_ship_params(name),
                                   'D115 post-window live parameter context')
    actual = audit['max_gm_lashing_m']
    row = {'ship': name, 'prior_cap_m': expected, 'cap_m': actual,
           'exactly_unchanged': actual == expected, 'delta_m': actual - expected,
           'audit': audit}
    rows.append(row)
    (run / 'design-gm.json').write_text(json.dumps(rows, indent=2, allow_nan=False) + '\n')
    print(name, f'{actual:.3f}', 'm', 'exactly_unchanged=', actual == expected)
    assert math.isfinite(actual) and len(audit['conditions']) == 6
    assert audit['governing']['condition'] == 'C3', row
    assert actual == expected, row

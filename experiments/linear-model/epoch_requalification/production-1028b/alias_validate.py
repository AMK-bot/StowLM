"""Both public middle-ship names must resolve to the promoted numerical bank."""
import importlib.util
import json
from pathlib import Path

import numpy as np

from stowage.linear import build_linear_model, linear_constraints
from stowage.linear.bands import band_for_instance

RUN = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("w2_alias_driver", RUN.parent/"driver.py")
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
models = {}
rows = {}
for ship in ("feeder_800", "feeder_1000"):
    instance, _ = driver.cargo_instance(ship, "light")
    model = build_linear_model(instance, band=band_for_instance(instance))
    models[ship] = model
    rows[ship] = linear_constraints(model, direction="necessary")
    assert model.measurement_diagnostics["margin_policy"]["production_usable"] is True
identities = {ship: model.measurement_diagnostics["response_calibration_identity"]
    for ship, model in models.items()}
assert len(set(identities.values())) == 1
for attribute in ("a_cell", "a_tank", "const", "lb", "ub"):
    np.testing.assert_array_equal(getattr(rows["feeder_800"], attribute),
        getattr(rows["feeder_1000"], attribute))
assert rows["feeder_800"].names == rows["feeder_1000"].names
result = dict(passed=True, response_identities=identities,
    identical_necessary_rows=True, row_count=len(rows["feeder_800"].names))
(RUN/"alias-validation.json").write_text(json.dumps(result, indent=2)+"\n")
print(json.dumps(result))

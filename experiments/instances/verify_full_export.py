"""Six bounded, single-core full-export smoke runs; not checker certificates."""
import json
from pathlib import Path
from time import perf_counter

from stowage.generate.random_yard import RandomGenerationReport, generate_random_instance
from stowage.generate.instances import GenerationReport
from stowage.generate.instances_v2_random import generate_random_v2

rows = []
destination = Path(__file__).with_name("full-export.json")
for vessel in ("feeder_500", "feeder_800", "feeder_1700"):
    for dense in (False, True):
        start = perf_counter()
        audit = GenerationReport() if dense else RandomGenerationReport()
        if dense:
            instance = generate_random_v2("full", seed=1, noise=.3,
                vessel_name=vessel, port_name="laem_chabang_like",
                dense_20ft=True, report=audit)
        else:
            instance = generate_random_instance("full", seed=1, vessel_name=vessel, report=audit)
        row = dict(vessel=vessel, dense=dense, boxes=len(instance.load_list),
                   seconds=perf_counter()-start, attempts=audit.attempts,
                   rejections=audit.rejections, **audit.full_export)
        assert row["design_displacement_fraction"] >= .9
        assert .75 <= row["cargo_mass_fraction"] <= .9
        rows.append(row)
        destination.write_text(json.dumps(rows, indent=2) + "\n")
        print(json.dumps(row), flush=True)

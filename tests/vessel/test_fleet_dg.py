"""DoC contradictions fail before any hull assembly; new inputs invalidate caches."""

from copy import deepcopy
import json

import pytest

from stowage.vessel.fleet import _stack_records, load_ship_params
from stowage.vessel.fleet_cache import _cache_key


@pytest.mark.parametrize("mistake", ["area_id", "classes", "missing_classes", "unknown_member", "duplicate_member", "duplicate_area"])
def test_rejects_contradictory_dg_document(mistake):
    params = load_ship_params("feeder_500")
    record = params["stacks"][0]
    areas = params["dg_document"]["areas"]
    if mistake == "area_id":
        record["dg_doc_area_id"] = "MISSING"
    elif mistake == "classes":
        record["dg_classes_allowed"] = ["7"]
    elif mistake == "missing_classes":
        record.pop("dg_classes_allowed")
    elif mistake == "unknown_member":
        areas[0]["stack_ids"].append("99999D")
    elif mistake == "duplicate_member":
        areas[0]["stack_ids"].append(areas[0]["stack_ids"][0])
    else:
        areas.append(deepcopy(areas[0]))
    with pytest.raises(ValueError, match="DG"):
        _stack_records(params)


@pytest.mark.parametrize("field", ["classes", "area_id", "packing", "equipment", "opening", "lightship"])
def test_cache_fingerprint_covers_new_parameter_sections(field):
    original = load_ship_params("feeder_500")
    changed = deepcopy(original)
    if field == "classes":
        changed["stacks"][0]["dg_classes_allowed"] = []
    elif field == "area_id":
        changed["stacks"][0]["dg_doc_area_id"] = "OTHER"
    elif field == "packing":
        changed["dg_document"]["areas"][0]["packing_forms_allowed"] = []
    elif field == "equipment":
        changed["dg_document"]["areas"][0]["equipment_assumptions"] = "different equipment"
    elif field == "opening":
        changed["envelope"]["openings"][0]["z_m"] += 0.1
    else:
        changed["hull"]["lightship_mass_kg"] += 1.0
    # The cache hashes bytes without interpreting their representation: use a
    # deterministic encoding of the actual trees to isolate each changed leaf.
    encode = lambda tree: json.dumps(tree, sort_keys=True).encode()
    for with_hull in (False, True):
        for size in ("full", "tiny"):
            assert _cache_key("feeder_500", size, with_hull, encode(original)) != _cache_key(
                "feeder_500", size, with_hull, encode(changed))

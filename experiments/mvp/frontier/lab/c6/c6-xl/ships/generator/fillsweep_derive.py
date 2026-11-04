"""Exact pure derivation helpers copied from strategy/fillsweep/generate.py.
Source SHA256: 42c472bcedd199caf778b7633a1b9f9653f37472a5244336bd1204791a568a3f
"""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json

def footprint(box):
    return 1 if box['length_ft'] == 20 else 2

def stratum(box):
    return json.dumps([box['pod'], box['length_ft'], box['reefer'],
                       box['dg'] is not None, box['empty'], box['special_stowage']], sort_keys=True)

def nested_order(cargo, seed):
    """Proportional-deficit interleaving; deterministic hashed order in strata."""
    groups = defaultdict(list)
    for box in cargo:
        groups[stratum(box)].append(box)
    for group in groups.values():
        group.sort(key=lambda box: hashlib.sha256(f"D192:{seed}:{box['id']}".encode()).hexdigest())
    counts = {key: len(group) for key, group in groups.items()}
    used = Counter()
    result = []
    for n in range(len(cargo)):
        key = min((key for key in groups if used[key] < counts[key]),
                  key=lambda key: (-((n + 1) * counts[key] / len(cargo) - used[key]), key))
        result.append(groups[key][used[key]])
        used[key] += 1
    return result

def nearest_prefix(order, target_cells):
    occupancy = [0]
    for box in order:
        occupancy.append(occupancy[-1] + footprint(box))
    # Prefer lower occupancy on an exact tie; all rungs share this one order.
    count = min(range(1, len(order) + 1), key=lambda n: (abs(occupancy[n] - target_cells), n))
    return order[:count]

def derive_payload(parent, selected, map_id, multiplier, catalogue):
    payload = deepcopy(parent)
    keep = {box['id'] for box in selected}
    payload['id'] = f'D192.{map_id}'
    payload['load_list'] = sorted(keep)
    for box in payload['containers']:
        if box['id'] in keep:
            box['gross_mass_kg'] = max(box['tare_kg'], multiplier * box['gross_mass_kg'])
            box['weight_class'] = catalogue.classify(box['gross_mass_kg'])
            # Do not retain a stale interchangeability key after mass changes.
            box['category'] = None
    # Unselected targets remain ordinary ashore cargo; full parent yard and
    # inventory stay fixed. They are not loading demand or movable onboard mass.
    # Keep the parent's producer fields as lineage, while marking the derived identity.
    payload['family'].update(fillsweep='D192', fillsweep_parent_id=parent['id'],
                             fillsweep_mass_multiplier=repr(multiplier),
                             n_boxes=str(len(keep)))
    payload['family']['booking_utilisation_teu'] = repr(sum(box['length_ft'] / 20 for box in selected) /
                                                       len(parent['vessel']['cells']))
    return payload

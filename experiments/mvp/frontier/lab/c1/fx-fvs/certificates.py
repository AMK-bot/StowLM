"""Aggregate checked score rows and explicit conditional relocation certificates."""
import argparse
import hashlib
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAB = HERE.parent.parent
ROOT = LAB.parents[3]


def aggregate(run):
    baseline = json.loads((LAB/'baseline.json').read_text())['maps']
    selected = (LAB/'sets/quick.txt').read_text().splitlines()
    result = []
    for rel in selected:
        raw = (ROOT/rel).read_bytes()
        instance = json.loads(raw)
        loads = set(instance['load_list'])
        mandatory = {upper for stack in instance['yard']['stacks']
                     for i,lower in enumerate(stack['boxes']) if lower in loads
                     for upper in stack['boxes'][i+1:] if upper not in loads}
        pairs = sum(upper in loads for stack in instance['yard']['stacks']
                    for i,lower in enumerate(stack['boxes']) if lower in loads
                    for upper in stack['boxes'][i+1:])
        path = Path(run)/f"{instance['id']}.json"
        row = json.loads(path.read_text()) if path.exists() else {}
        old = baseline[instance['id']]
        is_original = row.get('assignment') is not None and row.get('assignment') == old.get('stored')
        bounds = row.get('method_metadata',{}).get('order_bounds',[])
        bound = bounds[0] if len(bounds) == 1 else next((b for b in bounds if b.get('stored_assignment')), {})
        chosen_hash = hashlib.sha256(json.dumps(row.get('assignment'),sort_keys=True).encode()).hexdigest()
        chosen_bound = next((b for b in bounds if b.get('assignment_sha256') == chosen_hash), {})
        if is_original and not chosen_bound:
            chosen_bound = bound
        chosen_lb = chosen_bound.get('lower_bound')
        chosen_search = chosen_bound.get('replay_search') or {}
        if chosen_search.get('optimal'):
            chosen_lb = chosen_search['lower_bound']
        joint = row.get('method_metadata',{}).get('joint') or {}
        class_lb = joint.get('relocation_lower_bound')
        if class_lb is not None:
            class_lb = len(mandatory) + math.ceil(class_lb-1e-7)
        checked = row.get('passed') is True
        if row and row.get('input_sha256') != hashlib.sha256(raw).hexdigest():
            raise ValueError(f"Input changed: {rel}")
        reloc = (row.get('parts') or {}).get('relocations')
        fixed_lb = bound.get('lower_bound')
        replay_search = bound.get('replay_search') or {}
        if replay_search.get('optimal'):
            fixed_lb = replay_search['lower_bound']
        result.append(dict(instance_id=instance['id'],input=rel,
                           mandatory_inventory_lower_bound=len(mandatory), load_yard_pairs=pairs,
                           checked=checked,checked_relocations=reloc,
                           map_yard_minimum_proven=checked and reloc == len(mandatory),
                           selected_stow_lower_bound=chosen_lb,
                           selected_stow_minimum_proven=bool(checked and chosen_lb == reloc),
                           occupied_slot_class_lower_bound=class_lb,
                           occupied_slot_class_minimum_proven=bool(checked and class_lb == reloc),
                           joint_status=joint.get('status'),
                           fixed_stored_stow_lower_bound=fixed_lb,
                           fixed_stored_stow_minimum_proven=bool(checked and is_original and fixed_lb == reloc),
                           proof_scope='yard relocations; no simulator gate events; conditional bounds require monotone Place-only plans'))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run')
    parser.add_argument('--out',required=True)
    args = parser.parse_args()
    rows = aggregate(args.run)
    Path(args.out).write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps(dict(maps=len(rows), checked=sum(r['checked'] for r in rows),
                          map_minima=sum(r['map_yard_minimum_proven'] for r in rows),
                          fixed_minima=sum(r['selected_stow_minimum_proven'] for r in rows),
                          class_minima=sum(r['occupied_slot_class_minimum_proven'] for r in rows))))

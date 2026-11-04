"""Preregister a deliberately binding-enriched validation cohort without planning.

Only stored Frontier 0.9 results are inspected. Literal sealed positive sentences
are transported to each real map's physical context. This is not a blind success
rate sample. No checker, planner, repair, or language-model call runs here.
"""
from __future__ import annotations
import collections
import hashlib
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
from experiments.mvp.reader.schema import context_from_instance, validate_answer
from llm.instructions.apply import apply, _class_matches, _dg_classes
from stowage.conventions import cells_of_slot, parse_slot, format_slot
from stowage.model import MovePlan

ROOT = Path(__file__).resolve().parents[3]
READER = ROOT / 'experiments/mvp/reader'
RUN_DIRS = ('tiny48-0.9', 'val-0.9', 'unseen15-0.9')
TYPES = ('close_bay', 'max_tiers', 'dg_keep_away', 'one_port_per_hatch')


def physical_hatch_violations(instance, plan):
    stacks = {s.id: s for s in instance.vessel.stacks}
    cover_by_cell = {c.id: stacks[c.stack_id].hatch_cover_id for c in instance.vessel.cells}
    boxes = {b.id: b for b in instance.containers}
    positions = {p.container_id: p.slot for p in instance.onboard.placements
                 if p.container_id not in set(instance.discharge_list)}
    positions.update({m.container_id: m.dst for m in plan.moves if m.kind == 'load'})
    ports = collections.defaultdict(set)
    for box, slot in positions.items():
        for cell in cells_of_slot(slot):
            if cover_by_cell.get(cell):
                ports[cover_by_cell[cell]].add(str(boxes[box].pod))
    return [f'mixed discharge ports at {h}: {sorted(p)}' for h,p in sorted(ports.items()) if len(p)>1]


def hatch_capacity_possible(instance):
    """Necessary TEU partition screen, not geometric or checker feasibility."""
    stacks = {s.id:s for s in instance.vessel.stacks}
    caps = collections.Counter(stacks[c.stack_id].hatch_cover_id for c in instance.vessel.cells)
    covers = [h.id for h in instance.vessel.hatch_covers]
    boxes = {b.id:b for b in instance.containers}
    demand = collections.Counter()
    for bid in instance.load_list:
        demand[str(boxes[bid].pod)] += boxes[bid].length_ft//20
    ports = sorted(demand)
    if len(covers) < len(ports): return False
    for allocation in itertools.product(ports, repeat=len(covers)):
        capacity = collections.Counter()
        for h,p in zip(covers, allocation): capacity[p] += caps[h]
        if all(capacity[p]>=n for p,n in demand.items()): return True
    return False


def _namespace(value):
    if isinstance(value, dict): return SimpleNamespace(**{k:_namespace(v) for k,v in value.items()})
    if isinstance(value, list): return [_namespace(v) for v in value]
    return value


def _selection_instance(raw):
    # Avoid parsing unrelated large fleet certification records: only physical
    # placement data used by the existing pure instruction predicates is needed.
    vessel = {k: raw["vessel"][k] for k in ("cells", "stacks", "hatch_covers", "exclusion_regions")}
    return _namespace({"id":raw["id"],"vessel":vessel,"containers":raw["containers"],
                       "load_list":raw["load_list"],"discharge_list":raw["discharge_list"],
                       "onboard":raw["onboard"]})



def physical_slots_for_box(instance, box):
    """Size-compatible physical footprints only; no checker feasibility claim.

    Match Frontier/engine slot geometry: paired neighbouring odd cells at the
    same row/tier, with each physical cell accepting the actual box length.
    """
    cells = {c.id: c for c in instance.vessel.cells}
    accepts = f"accepts_{box.length_ft}"
    if box.length_ft == 20:
        return tuple(sorted(c.id for c in cells.values() if getattr(c, accepts, False)))
    if box.length_ft not in (40, 45):
        return ()
    slots = []
    for cell in cells.values():
        address = parse_slot(cell.id)
        partner = format_slot(address.bay + 2, address.row, address.tier) if address.bay < 998 else None
        if partner in cells and getattr(cell, accepts, False) and getattr(cells[partner], accepts, False):
            slots.append(format_slot(address.bay + 1, address.row, address.tier))
    return tuple(sorted(slots))


def per_box_slot_screen(instance, compiled):
    """Return load boxes with no size-compatible footprint after instruction.

    Passing is merely necessary: support, mass, DG rules, reefer requirements,
    retained occupancy and full-checker conditions are deliberately not solved.
    """
    boxes = {b.id: b for b in instance.containers}
    return [bid for bid in instance.load_list
            if not any(compiled.forbids(bid, slot) is None
                       for slot in physical_slots_for_box(instance, boxes[bid]))]


def select(output_path=None):
    output_path = Path(output_path) if output_path is not None else READER / "binding-cases.json"
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite frozen preregistered cohort: {output_path}")
    sealed = [json.loads(line) for line in (READER/'data/sealed.jsonl').read_text().splitlines()]
    positive = [r for r in sealed if 'constraint' in r['expected']]
    candidates = collections.defaultdict(list)
    seen = set()
    inventory = collections.Counter()
    for run_dir in RUN_DIRS:
        for baseline_file in sorted((ROOT/'experiments/mvp/frontier/runs'/run_dir).glob('*.json')):
            baseline = json.loads(baseline_file.read_text())
            if not isinstance(baseline,dict) or not baseline.get('passed') or not baseline.get('plan'): continue
            path = Path(baseline['input'])
            if 'val' not in path.parts or any(x in path.parts for x in ('train','test')):
                raise ValueError(f'non-validation input {path}')
            if str(path) in seen: continue
            seen.add(str(path))
            raw = json.loads((ROOT/path).read_text())
            instance = _selection_instance(raw)
            plan = MovePlan.model_validate(baseline['plan'])
            context = context_from_instance(raw)
            boxes = {b.id:b for b in instance.containers}
            inventory[run_dir]+=1
            input_sha256 = hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
            for row in positive:
                kind = row['expected']['constraint']['type']
                try: answer = validate_answer(json.dumps(row['expected']),context)
                except ValueError: continue
                if kind == 'one_port_per_hatch':
                    violations = physical_hatch_violations(instance,plan)
                    if not hatch_capacity_possible(instance):continue
                    capacity = 'necessary cover/port TEU partition passed; does not certify geometric/checker feasibility'
                else:
                    if kind == 'dg_keep_away' and not any(_class_matches(answer.imdg_class,_dg_classes(boxes[b])) for b in instance.load_list): continue
                    compiled = apply(instance,answer)
                    if compiled.capacity_shortfall():continue
                    if per_box_slot_screen(instance, compiled):continue
                    violations = compiled.violations(plan)
                    capacity = 'necessary open-cell and per-box size-compatible instruction-allowed footprint screens passed; not a checker feasibility certificate'
                case = {'type':kind,'input':str(path),'baseline_file':str(baseline_file.relative_to(ROOT)),
                        'instance_id':instance.id,'sentence':row['sentence'],'sealed_id':row['id'],
                        'expected':row['expected'],'context':context,'baseline_weighted':baseline['weighted'],
                        'baseline_violations':violations,'baseline_violation_count':len(violations),
                        'capacity_screen':capacity,'n_load':len(instance.load_list),
                        'input_sha256':input_sha256}
                # Prefer binding, tiny cheap runs; hatch needs >=2 physical covers
                # for multiple ports so its capacity screen naturally excludes most tiny maps.
                rank = (not bool(violations),len(instance.load_list),-baseline['weighted'],str(path),row['id'])
                candidates[kind].append((rank,case))
    cases=[]
    for kind in TYPES:
        used=set()
        for _,case in sorted(candidates[kind],key=lambda pair:pair[0]):
            if case['input'] in used:continue
            used.add(case['input'])
            case['case_id']=f'{kind}-{len(used):02}'
            case['selection_rationale']='Openly baseline-screened, binding-enriched validation cohort: prefer violating stored best plans, then smaller load lists, then larger baseline weighted move count; one literal sealed sentence per distinct map. '+case['capacity_screen']
            cases.append(case)
            if len(used)==5:break
        if len(used)!=5:raise RuntimeError(f'{kind}: only {len(used)} distinct eligible maps')
    result={'schema_version':'reader-binding-cases/1','validation_only':True,'selection_is_blind':False,
            'max_runs':20,'workers':2,'load_limit':36,'planner_calls_in_selection':0,
            'selection_policy':'Exactly five distinct maps per type, selected from passed stored Frontier 0.9 validation plans. Baseline violations inspected deliberately; no constrained outcomes observed. Whole sealed sentences copied literally; their context replaced by actual instance context. Necessary capacity screens only.',
            'inventory':dict(inventory),'cases':cases}
    with output_path.open('x') as output:
        output.write(json.dumps(result,indent=2)+'\n')
    for c in cases:print(c['case_id'],c['instance_id'],c['sealed_id'],c['baseline_weighted'],c['baseline_violation_count'])
    return result

if __name__=='__main__':select()

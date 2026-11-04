# Copied from experiments/mvp/frontier/lab/c3/c2-hatch/securing_cuts.py; engine integration changes are local.
"""Lift source-pinned ballast-independent rod proofs to complete columns."""
from stowage.conventions import cells_of_slot
from .bound_securing import securing_bound


def add_securing_cut(context, assignment, ship):
    bound = securing_bound(context['instance'],ship)
    if bound.get('status') != 'infeasible_fixed_stow' or not bound.get('proof'):
        return None,bound
    T,model,chosen = context['T'],context['model'],context['chosen']
    boxes = {b.id:b for b in context['instance'].containers}
    slots = bound.get('witnesses',{}).get('max_lashing_ratio',[])
    detail = bound.get('witness_details',{}).get('max_lashing_ratio',{})
    ratio = bound.get('lower_ratios',{}).get('max_lashing_ratio',0.)
    if ratio > 1.+1e-8 and slots and detail and set(slots)<=set(assignment.values()):
        touched = {int(sj) for slot in slots for sj in T.slot_stacks[T.slot_index[slot]] if sj>=0}
        ids = {bid for bid,slot in assignment.items()
               if any(int(sj) in touched for sj in T.slot_stacks[T.slot_index[slot]] if sj>=0)}
        pure = len(touched)==2 and all(T.stack_deck[sj] for sj in touched)
        pure = pure and all(boxes[bid].length_ft != 20 and
            {int(sj) for sj in T.slot_stacks[T.slot_index[assignment[bid]]] if sj>=0}==touched for bid in ids)
        if pure:
            variables = [chosen[T.box_index[bid],T.slot_index[assignment[bid]]] for bid in ids]
            occupied = {cid for bid in ids for cid in cells_of_slot(assignment[bid])}
            empty = {ci for sj in touched for ci in T.stack_cells[sj] if T.cell_ids[ci] not in occupied}
            model.add(sum(variables)<=len(variables)-1+sum(context['occupied'][ci] for ci in empty))
            return {'kind':'source_pinned_pure_long_rod_columns','size':len(variables)+len(empty),
                    'ratio':ratio,'source_sha256':bound['source_sha256'],
                    'dependency_sha256':bound.get('dependency_sha256',{}),
                    'box_slots':{bid:assignment[bid] for bid in sorted(ids)},
                    'empty_cells':sorted(T.cell_ids[ci] for ci in empty),
                    'witness_slots':list(slots),'proof':bound['proof']},bound
    variables = [chosen[T.box_index[bid],T.slot_index[slot]] for bid,slot in assignment.items()]
    model.add(sum(variables)<=len(variables)-1)
    return {'kind':'source_pinned_full_stow_securing','size':len(variables),
            'source_sha256':bound['source_sha256'],
            'dependency_sha256':bound.get('dependency_sha256',{}),
            'box_slots':dict(assignment),'proof':bound['proof']},bound

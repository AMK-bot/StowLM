"""Exact vectorization of the existing search-only sight eligibility inequality."""
from collections import defaultdict
import numpy as np


def envelope_key(box):
    o=box.oog
    return (box.length_ft, o.over_length_aft_m if o else 0., o.over_length_fore_m if o else 0.,
            o.over_width_starboard_m if o else 0., o.over_width_port_m if o else 0.)


def restrict(model, out, minimum):
    """Same scalar inequality, grouped only by native sight_limit dependencies.

    sight_limit reads length and horizontal overhangs; height appears separately
    in this very inequality. Keep its original exact geometric computation once
    per signature, including for OOG boxes. Physical slots and masks stay distinct.
    """
    boxes=np.asarray(model.boxes,dtype=np.int64)
    groups=defaultdict(list)
    for b in model.boxes:
        groups[envelope_key(model.p.boxes[model.T.box_ids[b]])].append(b)
    groups=[np.asarray(group,dtype=np.int64) for group in groups.values()]
    for page in model.pages:
        if not page.deck:
            continue
        base=max(float(model.T.stack_base_z[j]) for j in page.stacks)
        for tier,slot in enumerate(page.slots):
            floor=base+tier*minimum
            for group in groups:
                eligible=group[out[group,slot]]
                if not len(eligible):
                    continue
                limit=model.sight_limit(page,tier,int(eligible[0]))
                bad=floor+model.T.height[eligible]>limit+1e-8
                out[eligible[bad],slot]=False
    return out

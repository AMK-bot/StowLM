"""Action features for review rules; no solver, no labels at decode time."""
import numpy as np
from stowage.conventions import parse_slot, cells_of_slot
NAMES=['deck','deck_tier','hold_tier','mass','mass_deck','stack_mass','stack_mass2','new_stack','outboard','len40_deck','pod_deck','yard_depth']
class Ctx:
    def __init__(s,inst):
        s.mass={b.id:b.gross_mass_kg/1e4 for b in inst.containers}
        s.len={b.id:b.length_ft for b in inst.containers}
        s.cell={c.id:c.stack_id for c in inst.vessel.cells}
        order={c.locode:c.index for c in inst.rotation.calls}
        s.pod={b.id:order.get(b.pod,0) for b in inst.containers}
        s.depth={c:len(st.boxes)-1-i for st in inst.yard.stacks for i,c in enumerate(st.boxes)}
    def stacks(s,slot): return {s.cell[c] for c in cells_of_slot(slot)}
    def state(s,placed):
        last={}
        for a in placed:
            if hasattr(a,'slot') and hasattr(a,'box'): last[a.box]=a.slot
        m={}
        for b,sl in last.items():
            if parse_slot(sl).on_deck:
                for k in s.stacks(sl): m[k]=m.get(k,0.)+s.mass[b]/len(s.stacks(sl))
        return m
    def res_mass(s,a,st):
        return max(st.get(k,0.) for k in s.stacks(a.slot))+s.mass[a.box]
    def row(s,a,st):
        w=parse_slot(a.slot); d=float(w.on_deck)
        rm=s.res_mass(a,st) if d else 0.
        cur=max(st.get(k,0.) for k in s.stacks(a.slot)) if d else 0.
        return [d,d*(w.tier-80)/2,(1-d)*w.tier/2,s.mass[a.box],d*s.mass[a.box],rm,rm*rm/4,d*(cur==0),abs(w.row)/10,d*(s.len[a.box]>=40),d*s.pod[a.box],min(s.depth.get(a.box,0),4)]

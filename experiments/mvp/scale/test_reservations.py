"""Toy allocation tests only; no map/dataset inputs."""
from __future__ import annotations
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import reservations


def toy(deck=False):
    # Four physical columns, three tiers. A/B are short-compatible; C/D long-only.
    # Pair AB and CD can take a long box at every tier.
    stack_pairs=[]; parts=[]; ids=[]
    for stack in range(4):
        for tier in range(3):
            ids.append(f's{stack}-{tier}'); parts.append([f'c{stack}-{tier}'])
            stack_pairs.append([stack,-1])
    for left in (0,2):
        for tier in range(3):
            ids.append(f'l{left}-{tier}')
            parts.append([f'c{left}-{tier}',f'c{left+1}-{tier}'])
            stack_pairs.append([left,left+1])
    cells=[f'c{j}-{k}' for j in range(4) for k in range(3)]
    by_cell=np.zeros((len(ids),len(cells)),dtype=bool)
    for si,values in enumerate(parts):
        for cell in values:
            by_cell[si,cells.index(cell)]=True
    boxes=['L']+[f'S{i}' for i in range(5)]
    t=NS(box_index={cid:i for i,cid in enumerate(boxes)},length=np.array([40]+[20]*5),
         slot_ids=ids,slot_stacks=np.array(stack_pairs),cell_index={c:i for i,c in enumerate(cells)},
         stack_deck=np.full(4,deck,dtype=bool))
    domain=np.zeros((len(boxes),len(ids)),dtype=bool)
    domain[0,12:]=True; domain[1:,:6]=True
    future=NS(t=t,parts=parts,by_cell=by_cell,
              evaluate=lambda ex,remaining:(np.array([t.box_index[c] for c in remaining]),
                                            domain[[t.box_index[c] for c in remaining]]))
    instance=NS(load_list=boxes)
    node=NS(ex=NS(where={},buffer=[]))
    return instance,node,future,domain


class ReservationTests(unittest.TestCase):
    def test_long_avoids_closing_all_future_short_tiers(self):
        from stowage.actions.space import Place
        instance,node,future,_=toy()
        dangerous=Place('L','l0-0'); safe=Place('L','l2-0')
        original=[dangerous,safe]; metrics={}
        ranked=reservations._rank(instance,node,future,original,metrics,float('inf'))
        self.assertEqual(ranked,[safe,dangerous])
        self.assertEqual({id(a) for a in original},{id(a) for a in ranked})
        self.assertEqual(metrics['scale_reservation_first_changed'],1)

    def test_short_preserves_long_only_hold_mode(self):
        from stowage.actions.space import Place
        instance,node,future,domain=toy()
        # L has only the AB pair; short boxes can additionally use CD.
        domain[0,15:]=False; domain[1:,6:12]=True
        dangerous=Place('S0','s0-0'); safe=Place('S0','s2-0')
        self.assertEqual(reservations._rank(instance,node,future,[dangerous,safe],{},float('inf')),
                         [safe,dangerous])

    def test_short_on_deck_does_not_close_future_long_mode(self):
        from stowage.actions.space import Place
        instance,node,future,domain=toy(deck=True)
        domain[0,15:]=False; domain[1:,6:12]=True
        # Same-tier long slot is already unavailable; higher AB tiers remain.
        domain[0,12]=False
        original=[Place('S0','s0-0'),Place('S0','s2-0')]
        self.assertEqual(reservations._rank(instance,node,future,original,{},float('inf')),original)

    def test_deadline_and_discharge_return_original_actions(self):
        from stowage.actions.space import Place,Discharge
        instance,node,future,_=toy()
        actions=[Place('L','l0-0'),Place('L','l2-0')]
        self.assertIs(reservations._rank(instance,node,future,actions,{},0.),actions)
        discharges=[Discharge('x')]
        self.assertIs(reservations._rank(instance,node,future,discharges,{},float('inf')),discharges)

    def test_installs_order_only_and_restores(self):
        from stowage.actions.space import Place
        from stowage.solver import construct_v4
        instance,node,future,_=toy()
        actions=[Place('L','l0-0'),Place('L','l2-0')]
        original=lambda *args,**kwargs:actions
        with patch.object(construct_v4,'_choices',original):
            restore=reservations.install()
            ranked=construct_v4._choices(instance,node,future,None,{},0)
            self.assertEqual(ranked,list(reversed(actions)))
            restore(); restore()
            self.assertIs(construct_v4._choices,original)

if __name__=='__main__':
    unittest.main()

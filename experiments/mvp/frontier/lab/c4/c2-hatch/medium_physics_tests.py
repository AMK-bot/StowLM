"""Synthetic independent ballast projection and passing-plan row projections."""
from fractions import Fraction as F
from itertools import product
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from medium_physics import max_ballast_benefit,derive
import medium_physics


def test_knapsack_envelope_contains_every_independent_fill():
    tanks=[(F(2),F(3)),(F(3),F(1)),(F(1),F(-5))]
    for allowance in range(8):
        brute=max(sum(m*b for m,(_,b) in zip(masses,tanks))
                  for masses in product(range(3),range(4),range(2))
                  if sum(masses)<=allowance)
        assert max_ballast_benefit(tanks,F(allowance))==brute


def test_fractional_ballast_and_negative_benefits():
    assert max_ballast_benefit([(2,3),(3,1)],F(5,2))==F(13,2)
    assert max_ballast_benefit([(2,-3)],100)==0
    assert max_ballast_benefit([(2,3)],-1)==0


def test_missing_source_evidence_omits_optional_row(monkeypatch):
    def missing(*args):
        raise FileNotFoundError('source pin unavailable')
    monkeypatch.setattr(medium_physics,'derive',missing)
    assert medium_physics.add_row({'instance':None,'T':None})==dict(
        added=False,reason='source pin unavailable')


def test_every_passing_train_reference_projects_into_weather_row():
    from stowage.feasibility.tables import build_tables
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from stowage.moves import apply_plan
    from stowage.checker.stability_loading import loading_condition
    here=Path(__file__).resolve().parent
    references=json.loads((here/'medium-references/upper_index.json').read_text())
    diagnostics=[]
    for row in references.values():
        instance=load_instance(row['input'])
        T=build_tables(instance)
        coeff,rhs,info=derive(instance,T)
        replay=apply_plan(instance,MovePlan.model_validate(row['plan']))
        assert replay.legal
        lhs=sum((coeff[T.box_index[p.container_id],T.slot_index[p.slot]]
                 for p in replay.final_state.ship.placements),F(0))
        assert lhs<=rhs
        assert info['coordinate_guard_m']==1e-6
        independent_upper=sum((max(value for (other,_),value in coeff.items() if other==bi)
                               for bi in range(len(T.box_ids))),F(0))
        diagnostics.append(dict(instance_id=instance.id,reference_cargo_lower_kg_m=float(lhs),
            upper_cargo_moment_kg_m=float(rhs),
            independent_all_slots_upper_kg_m=float(independent_upper),
            redundant_even_without_packing=independent_upper<=rhs,
            physics_evidence=info))
    destination=here/'medium-results/physics-projection.json'
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(diagnostics,indent=2)+'\n')

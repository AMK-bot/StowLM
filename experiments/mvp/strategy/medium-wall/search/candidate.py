"""Replay a TRAIN-only externally packed candidate through carrier/terminal APIs.

No relaxations. This helper does not run automatically or finish ballast. Invoke
only within the admitted search slot, and include its elapsed in the map ledger.
"""
from __future__ import annotations
import argparse,importlib.util,json,sys,time,signal,traceback
from pathlib import Path
H=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('medium_wall_search',H/'search.py');s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)

def direct_proposal(inst,positions,out,row):
 from stowage.actions import get_action_space
 from stowage.actions.space import Place
 from stowage.conventions import parse_slot
 from stowage.moves import apply_plan
 from stowage.model import PlannerResult,MovePlan
 from stowage.checker.rules import e6_completion_possible
 from stowage.rules.catalogue import rule_freeze_v1
 boxes={b.id:b for b in inst.containers}
 ordered=sorted(positions,key=lambda c:(parse_slot(positions[c]).on_deck,parse_slot(positions[c]).tier,-boxes[c].gross_mass_kg,c))
 actions=[Place(cid,positions[cid]) for cid in ordered]
 with rule_freeze_v1(enabled=False):
  plan=get_action_space('boxslot').expand(inst,actions)
  replay=apply_plan(inst,plan)
  aboard={p.container_id for p in replay.final_state.ship.placements}
  complete=set(inst.load_list)<=aboard and not set(inst.discharge_list)&aboard
  row.update(direct_proposal_physical_legal=replay.legal,direct_proposal_inventory_complete=complete,direct_proposal_physical_rules=[v.rule_id for v in replay.violations],direct_proposal_e6=e6_completion_possible(inst,replay.final_state.ship,(),call_index=inst.call_index))
  if replay.legal and complete:
   result=PlannerResult(status='ok',plan=plan,planner='medium_wall.cp_direct_witness',planner_version='external1',seed=0,metrics=dict(proposal_only=True,native_replay_status=row.get('carrier_status'),full_checker_certified=False))
   s.save(out/'direct-result.json',result.model_dump(mode='json'));row['direct_proposal_path']=str(out/'direct-result.json')

def run(mapid,witness_path,out,budget):
 reg=s.read(H/'preregister.json');job=next(r for r in reg['jobs'] if r['map_id']==mapid)
 witness_path=Path(witness_path).resolve();assert witness_path.is_relative_to(H.parent/'capacity')
 payload=s.read(witness_path);positions=payload.get('witness',payload.get('positions'))
 if not isinstance(positions,dict):raise ValueError('Expected positions or witness mapping')
 from stowage.io import load_instance
 from stowage.solver.twostep import _canonical_instance,category_of,PreStowPlan,SlotCategory,make_feasibility_engine
 from stowage.solver.twostep_terminal import terminal_plan
 from stowage.actions import get_action_space
 from stowage.actions.expander import Expander
 from stowage.actions.space import Place
 from stowage.conventions import parse_slot
 from stowage.feasibility import FeasibilityConfig
 from stowage.rules.catalogue import rule_freeze_v1
 from stowage.solver.securing_geometry import securing_geometry_violation,securing_state_violations
 from stowage.checker.rules import e6_completion_possible
 from stowage.model import PlannerResult,MovePlan
 from stowage.moves import apply_plan
 inst=load_instance(s.ROOT/job['input']);assert s.digest(s.ROOT/job['input'])==job['sha256']
 assert set(positions)==set(inst.load_list)
 out=Path(out).resolve();assert out.is_relative_to(H)
 out.mkdir(parents=True,exist_ok=True)
 row=dict(map_id=mapid,witness_sha256=s.digest(witness_path),witness_path=str(witness_path),snapshot_sha256=reg['snapshot_sha256'],budget_s=budget,solver_complete=False,checker_certified=False,carrier_status='not_started',terminal_status='not_started')
 started=time.monotonic();deadline=started+budget
 signal.signal(signal.SIGALRM,s.alarm);signal.setitimer(signal.ITIMER_REAL,budget)
 try:
  with rule_freeze_v1(enabled=False):
   canonical=_canonical_instance(inst);boxes={b.id:b for b in canonical.containers};ex=Expander(canonical)
   cfg=FeasibilityConfig(extra={'global_screen':0});engine=make_feasibility_engine(canonical,get_action_space('boxslot'),config=cfg)
   pending=set(positions);trace=[];blocked={}
   while pending:
    progress=False
    # Try all currently support-ready destinations through unchanged native oracle.
    for cid in sorted(pending,key=lambda c:(parse_slot(positions[c]).on_deck,parse_slot(positions[c]).tier,-boxes[c].gross_mass_kg,c)):
     a=Place(cid,positions[cid])
     if not engine.is_legal(a):blocked[cid]='native placement rejection';continue
     reason=securing_geometry_violation(canonical,ex,a)
     if reason:blocked[cid]=reason;continue
     engine.push(a);ex.step(a);trace.append(a);pending.remove(cid);progress=True;break
    if not progress:
     row.update(carrier_status='rejected',carrier_aboard=len(trace),remaining=sorted(pending),blocking=blocked);return row
   ex.finish();violations=securing_state_violations(canonical,ex)
   physical=apply_plan(canonical,MovePlan(id=inst.id+'.cp-carrier',instance_id=inst.id,call_index=inst.call_index,moves=ex.moves))
   if not physical.legal or violations or not e6_completion_possible(canonical,physical.final_state.ship,(),call_index=inst.call_index):
    row.update(carrier_status='rejected',carrier_aboard=len(trace),securing_violations=violations,physical_rules=[v.rule_id for v in physical.violations]);return row
   row.update(carrier_status='ok',carrier_aboard=len(trace),carrier_seconds=time.monotonic()-started)
   prestow=PreStowPlan(slots=tuple(SlotCategory(positions[c],category_of(boxes[c],canonical),c) for c in sorted(positions)),actions=tuple(trace))
   plan,metrics,status,message=terminal_plan(inst,prestow,deadline=deadline,seed=0,engine_factory=make_feasibility_engine,feasibility_config=cfg)
   row.update(terminal_status=status,terminal_metrics=metrics,message=message)
   if plan is not None:
    replay=apply_plan(inst,plan);row.update(replay_legal=replay.legal,physical_rules=[v.rule_id for v in replay.violations],solver_complete=status=='ok' and replay.legal)
    result=PlannerResult(status=status,plan=plan,planner='medium_wall.cp_candidate_terminal',planner_version='external1',seed=0,wall_time_s=time.monotonic()-started,metrics=dict(row))
    s.save(out/'result.json',result.model_dump(mode='json'))
 except BaseException as exc:row.update(error=str(exc),traceback=traceback.format_exc())
 finally:
  signal.setitimer(signal.ITIMER_REAL,0)
  if not row.get('solver_complete') and time.monotonic()<deadline:
   # Authorized separately labelled diagnostic: no native bound relaxation or checker change.
   signal.setitimer(signal.ITIMER_REAL,max(.1,deadline-time.monotonic()))
   try:direct_proposal(inst,positions,out,row)
   except BaseException as exc:row['direct_proposal_error']=f'{type(exc).__name__}: {exc}'
   finally:signal.setitimer(signal.ITIMER_REAL,0)
  row['elapsed_s']=time.monotonic()-started;s.save(out/'summary.json',row)
 return row

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--map',required=True);p.add_argument('--witness',required=True);p.add_argument('--out',required=True);p.add_argument('--budget',type=float,default=300);a=p.parse_args();print(json.dumps(run(a.map,a.witness,a.out,a.budget)))

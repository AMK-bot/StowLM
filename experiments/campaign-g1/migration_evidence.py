"""Live post-freeze admission over the complete 5.4 solver-plan population.

Serialized verdicts are audit material, never proof of checker/mask execution.
The gate executes both components itself. Gold does not exist before G1.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from eval.snapshot_io import read_snapshot

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
COMPONENT_KEYS = ('prompt_version','checker_version','mask_version','exact_solver_version',
                  'scalable_solver_version','catalogue_loaded_sha256')
SOLVER_STAGES = {'4.6','4.7','4.8'}
D62_COUNTERS = ('ship_lifts_at_or_above_h','yard_lifts_at_or_above_h',
                'rejected_crossing_lifts','yard_h_blocking_rehandles')


def _module(name):
    spec=importlib.util.spec_from_file_location('_migration_evidence_'+name,HERE/(name+'.py'))
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module


def current_component_epoch():
    from llm.stowlm.format_prompt import PROMPT_VERSION
    from llm.stowlm.mask import MASK_VERSION
    from stowage.versions import CHECKER_VERSION
    from stowage.solver.exact import ExactPlanner
    from stowage.solver.scalable import SCALABLE_VERSION
    from stowage.rules.catalogue import catalogue_snapshot
    return dict(prompt_version=PROMPT_VERSION,checker_version=CHECKER_VERSION,mask_version=MASK_VERSION,
        exact_solver_version=ExactPlanner.version,scalable_solver_version=SCALABLE_VERSION,
        catalogue_loaded_sha256=catalogue_snapshot().content_sha256)


def frozen_component_epoch(frozen):
    value=(frozen.get('migration') or {}).get('component_epoch') or frozen.get('component_epoch')
    return value if isinstance(value,dict) else {}


def assert_component_epoch(frozen, actual=None):
    expected=frozen_component_epoch(frozen)
    if set(expected)!=set(COMPONENT_KEYS) or any(not isinstance(expected[k],str) or not expected[k] for k in COMPONENT_KEYS):
        raise ValueError('M5_EPOCH_COMPONENTS_MISSING: complete loaded component epoch required')
    if (current_component_epoch() if actual is None else actual)!=expected:
        raise ValueError('M5_COMPONENT_EPOCH_MISMATCH: loaded components differ from frozen epoch')
    return expected


def _pin(path,bindings,expected=None):
    canonical=str(Path(path).resolve())
    previous=bindings.get(canonical)
    if previous is not None and expected is not None and previous!=expected:
        raise ValueError('evidence binding changed during admission: '+canonical)
    snapshot=read_snapshot(path,expected_sha256=expected or previous)
    bindings[canonical]=snapshot.sha256
    return snapshot


def _eligible(value):
    if isinstance(value,dict):
        if (value.get('diagnostic') or value.get('evidence_eligible') is False or value.get('official') is False
                or value.get('tiny_run') or value.get('allow_drift')):return False
        return all(_eligible(child) for child in value.values())
    if isinstance(value,list):return all(_eligible(child) for child in value)
    return True


def _stratum(inp):
    from stowage.conventions import canonical_vessel_id
    spec=(inp.get('shard') or {}).get('split_spec') or (inp.get('shard') or {}).get('spec') or inp.get('spec') or {}
    ship=inp.get('vessel') or spec.get('vessel') or inp.get('ship')
    return (inp.get('rung'),spec.get('port') or inp.get('port'),canonical_vessel_id(ship) if ship else None)


def _observation_valid(observation,inp,row,frozen):
    if not isinstance(observation,dict) or not isinstance(frozen,dict):return False
    rung,port,ship=_stratum(inp)
    count=observation.get('binding_actions')
    counters=[observation.get(key) for key in D62_COUNTERS]
    if any(not isinstance(value,int) or isinstance(value,bool) or value<0 for value in counters):return False
    return (observation.get('verified') is True and observation.get('source')=='migrated_move_engine'
        and observation.get('status')=='applicable'
        and isinstance(count,int) and not isinstance(count,bool) and count>=0
        and count==observation['rejected_crossing_lifts']+observation['yard_h_blocking_rehandles']
        and observation.get('input_sha256')==inp.get('sha256')
        and observation.get('plan_sha256')==row.get('plan_sha256') and bool(row.get('plan_sha256'))
        and observation.get('epoch_id')==frozen.get('epoch_id') and bool(frozen.get('epoch_id'))
        and observation.get('source_sha256')==frozen.get('source_sha256') and bool(frozen.get('source_sha256'))
        and observation.get('component_epoch')==frozen_component_epoch(frozen) and bool(frozen_component_epoch(frozen))
        and (observation.get('rung'),observation.get('port'),observation.get('ship'))==(rung,port,ship)
        and all((rung,port,ship)))


def d62_coverage(rows,rungs=(),*,frozen=None,population=None,attempted_population=None):
    """Observe returned-plan inputs; report unsolved allocated inputs separately.

    ``population`` contains every unique input for which a solver returned a
    plan, including failed/unreadable plans. Those require actual observations.
    ``attempted_population`` is the exact campaign allocation: unsolved inputs
    are censored, never classified as zero/nonbinding or as observed evidence.
    """
    inputs=list(population) if population is not None else [r.get('input') or r for r in rows]
    attempted=list(attempted_population) if attempted_population is not None else inputs
    members={};attempted_members={}
    for inp in inputs:
        digest=inp.get('sha256')
        if digest:members.setdefault(digest,dict(input=inp,observations=[]))
    for inp in attempted:
        digest=inp.get('sha256')
        if digest:attempted_members.setdefault(digest,dict(input=inp))
    for row in rows:
        inp=row.get('input') or row;member=members.get(inp.get('sha256'))
        if member is None:continue
        observation=(row.get('measurement') or row).get('d62_crossing')
        valid=_observation_valid(observation,member['input'],row,frozen)
        member['observations'].append((observation.get('binding_actions',0)>0) if valid else None)
    def digest(group):
        return hashlib.sha256(json.dumps(sorted(m['input']['sha256'] for m in group),separators=(',',':')).encode()).hexdigest()
    def aggregate(group,attempts):
        states=[]
        for member in group:
            values=member['observations']
            states.append(None if not values or None in values else any(values))
        known=[v for v in states if v is not None];bound=sum(v is True for v in known)
        returned={m['input']['sha256'] for m in group}
        unsolved=sum(m['input']['sha256'] not in returned for m in attempts)
        return dict(instances=len(states),attempted_instances=len(attempts),not_solved_instances=unsolved,
            population_sha256=digest(group),attempted_population_sha256=digest(attempts),
            observed_instances=len(known),binding_instances=bound,unknown_instances=len(states)-len(known),
            zero_binding_flag=bool(states) and len(known)==len(states) and bound==0,
            status=('observed' if len(known)==len(states) else 'unverified') if states else 'not_observed_no_returned_plan')
    groups={r:[] for r in rungs};strata={};attempt_groups={r:[] for r in rungs};attempt_strata={}
    for member in members.values():
        rung,port,ship=_stratum(member['input']);groups.setdefault(rung,[]).append(member)
        strata.setdefault((rung,port,ship),[]).append(member)
    for member in attempted_members.values():
        rung,port,ship=_stratum(member['input']);attempt_groups.setdefault(rung,[]).append(member)
        attempt_strata.setdefault((rung,port,ship),[]).append(member)
    result={r:aggregate(groups.get(r,[]),attempt_groups.get(r,[]))
        for r in sorted(set(groups)|set(attempt_groups),key=str)}
    for rung,row in result.items():
        row['by_port_ship']=[dict(port=port,ship=ship,zero_ack_key='|'.join(str(x) for x in (rung,port,ship)),
            **aggregate(strata.get((r,port,ship),[]),attempt_strata.get((r,port,ship),[])))
            for r,port,ship in sorted(set(strata)|set(attempt_strata),key=str) if r==rung]
    return result


def _full_checked(instance,plan,report):
    from stowage.model import PlannerResult
    candidate=PlannerResult(status='ok',plan=plan,planner='migration-recheck',planner_version='2')
    candidate._record_full_check(instance,report)
    if not candidate.shippable or instance.voyage is None:return False
    required={'voyage'} | {f'condition:{identity.id}:{part}' for identity in
        (instance.voyage.departure,instance.voyage.arrival) for part in ('stability','strength')}
    return required<=set(report.parts)


def execute_plan(instance,plan):
    """Direct production execution; this function never reads verdict JSON."""
    from stowage.checker import check
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine,FeasibilityConfig
    from llm.stowlm.format import ActionCodec,load_tokenizer
    from llm.stowlm.mask import MaskConfig,MaskedDecoder,MASK_VERSION
    report=check(instance,plan)
    space=get_action_space();actions=space.from_plan(instance,plan)
    engine=make_feasibility_engine(instance,space,config=FeasibilityConfig())
    if engine.diagnostics.get('global_screen')!='on':raise ValueError('production mask C2 global screen not on')
    load_tokenizer.cache_clear()
    decoder=MaskedDecoder(engine,ActionCodec(load_tokenizer(),[box.id for box in instance.containers]),MaskConfig())
    for action in actions:
        for token in decoder.codec.encode_action(action):decoder.advance(token)
    for token in decoder.codec.end_plan_tokens:decoder.advance(token)
    if decoder.status!='complete' or decoder.actions!=list(actions):raise ValueError('production mask replay incomplete')
    # The move engine owns the per-plan counters, separate from mask-search
    # diagnostics (which include speculative candidate tests).
    from stowage.moves import apply_plan
    physical=apply_plan(instance,plan)
    metric=getattr(physical,'d62_crossing',None)
    observation=metric.model_dump(mode='json') if metric is not None else None
    if observation is not None:
        observation.update(verified=bool(physical.legal and physical.executed==len(plan.moves)
            and observation.get('status')=='applicable'),
            binding_actions=observation['rejected_crossing_lifts']+observation['yard_h_blocking_rehandles'],
            binding_definition='rejected crossing lifts plus committed yard-limit blocking rehandles; exposure counters retained separately')
    return report,dict(verified=True,status='complete',global_screen='on',masked_actions=0,mask_version=MASK_VERSION,
        d62_crossing=observation)


def _instance_admission(instance,frozen):
    from stowage.ports import resolve_port_call_profile
    if tuple(map(int,instance.schema_version.split('.'))) < (1,9):raise ValueError('schema below 1.9')
    if instance.port.max_crane_height_tiers is None:raise ValueError('operational port H absent')
    for call in instance.rotation.calls:
        if resolve_port_call_profile(call,current_port=instance.port) is None:raise ValueError('later PortCall profile unresolved')
    _module('rule_policy').enforce_instance_weight_policy(instance,frozen)


def _validated_candidates(inventory,inventory_path,bindings,frozen,issue):
    """Reconstruct the denominator from all committed primary solver shards."""
    campaign=_module('campaign')
    parent=Path(inventory_path).resolve().parent
    pin=inventory['campaign_identity'];identity_path=Path(pin['path']).resolve()
    identity=_pin(identity_path,bindings,pin['sha256']).json()
    if identity.get('epoch_id')!=frozen['epoch_id'] or not _eligible(identity):raise ValueError('campaign identity epoch/eligibility mismatch')
    if identity.get('component_epoch')!=frozen_component_epoch(frozen):raise ValueError('campaign loaded component epoch mismatch')
    if identity.get('source_sha256')!=frozen.get('source_sha256') or identity.get('freeze_sha256')!=frozen.get('freeze_sha256'):
        raise ValueError('campaign source/cache epoch mismatch')
    if identity_path.parent!=parent.parent:raise ValueError('solver inventory is not in campaign 5.4 directory')
    checkpoint_snapshots={}
    reconstructed=campaign.discover_plans(parent.parent,identity['inputs'],checkpoint_snapshots=checkpoint_snapshots)
    reconstructed=campaign.migration_plan_records(parent.parent,reconstructed,identity,checkpoint_snapshots=checkpoint_snapshots)
    def key(row):return (row['source_stage'],str(Path(row['source_checkpoint']).resolve()),str(Path(row['plan']).resolve()),row['plan_sha256'],row.get('candidate_plan'),row.get('candidate_plan_sha256'),row.get('replay_checkpoint'),json.dumps(row['input'],sort_keys=True))
    actual=inventory.get('records',[])
    if len({key(r) for r in actual})!=len(actual) or sorted(map(key,actual))!=sorted(map(key,reconstructed)):
        raise ValueError('inventory must contain every returned solver artifact exactly once')
    primary_markers=[m for m in sorted((parent.parent).glob('4.*/*/done.json')) if m.parent.parent.name in SOLVER_STAGES]
    replay_markers=[Path(r['replay_checkpoint']) for r in reconstructed if Path(r['replay_checkpoint']).is_file()]
    for marker in primary_markers+replay_markers:
        snapshot=checkpoint_snapshots.get(str(marker.resolve()))
        if snapshot is None:raise ValueError('checkpoint population changed during admission')
        _pin(marker,bindings,snapshot.sha256);committed=snapshot.json()
        if committed.get('identity',{}).get('campaign')!=identity or campaign.verified_done(marker,committed['identity'],marker_snapshot=snapshot) is None:
            issue('M6_SOLVER_PLAN_DENOMINATOR',f'solver checkpoint is not current and committed: {marker}')
        try:_pin(committed['result'],bindings,committed['result_sha256'])
        except Exception as exc:issue('M6_SOLVER_PLAN_DENOMINATOR',exc)
        for artifact in committed['artifacts']:
            try:_pin(artifact['path'],bindings,artifact['sha256'])
            except Exception as exc:issue('M6_SOLVER_PLAN_DENOMINATOR',exc)
    return reconstructed,identity['inputs']


def evidence_report(frozen,*,instance_manifest=None,campaign_manifest=None,summary=None,required=True,
                    gold_manifest=None,recheck_manifest=None):
    """Run admission on every returned 5.4 plan, never on pre-G1 stored gold."""
    issues=[];bindings={};counts={};expected=frozen_component_epoch(frozen)
    def issue(code,detail):issues.append(dict(code=code,detail=str(detail)))
    if not required and not any((instance_manifest,campaign_manifest,summary,gold_manifest,recheck_manifest)):
        return dict(schema_version=2,phase='source_freeze',status='pending_post_freeze',complete=False,issues=[],bindings={},
            requirements=['instances','all_campaign_solver_plans_live_recheck','d62_counts'])
    if gold_manifest is not None or recheck_manifest is not None:
        issue('M6_OBSOLETE_GOLD_INVENTORY','G1 consumes campaign solver plans; stored gold is a post-G1 audit')
    if any(not isinstance(frozen.get(key),str) or not frozen[key] for key in ('epoch_id','source_sha256','freeze_sha256')):
        issue('M5_SOURCE_EPOCH_MISSING','source/cache epoch identity is incomplete')
    def load(path,code):
        if path is None:issue(code,'required inventory absent');return None
        try:return _pin(path,bindings).json()
        except Exception as exc:issue(code,exc);return None
    try:assert_component_epoch(frozen)
    except ValueError as exc:issue('M5_COMPONENT_EPOCH_MISMATCH',exc)
    instances=load(instance_manifest,'M3_INSTANCE_INVENTORY_MISSING');members={}
    if instances is not None:
        root=Path(instance_manifest).resolve().parent
        try:
            from eval.splits import load_inventory
            from eval.splits_epoch import validate_epoch_manifest
            validate_epoch_manifest(root,instances,load_inventory(),allow_diagnostic=False,check_source=True)
            transport={'drift','_manifest_sha256'}
            if {k:v for k,v in (instances.get('freeze_manifest') or {}).items() if k not in transport}!={k:v for k,v in frozen.items() if k not in transport}:
                raise ValueError('instance allocation does not bind source freeze')
            if instances.get('epoch_id')!=frozen.get('epoch_id') or not _eligible(instances):raise ValueError('instance epoch/eligibility mismatch')
        except Exception as exc:issue('M3_INSTANCE_PROVENANCE_INVALID',exc)
        from stowage.model import Instance
        for key,binding in instances.get('bindings',{}).items():
            try:
                path=(root/binding['instance']).resolve()
                if not path.is_relative_to(root):raise ValueError('instance escapes epoch root')
                snapshot=_pin(path,bindings,binding['sha256']);instance=snapshot.model(Instance)
                _instance_admission(instance,frozen)
                members[str(path)]=(snapshot.sha256,instance,binding,key)
            except Exception as exc:issue('M3_INSTANCE_NOT_MIGRATED',f'{key}: {exc}')
        counts['instances']=len(instances.get('bindings',{}))
        if not counts['instances']:issue('M3_INSTANCE_INVENTORY_EMPTY','no generated instances')
    inventory=load(campaign_manifest,'M6_SOLVER_PLAN_INVENTORY_MISSING');candidates=[];population=[];observations=[]
    if inventory is not None:
        try:
            if not _eligible(inventory) or inventory.get('epoch_id')!=frozen.get('epoch_id'):raise ValueError('solver inventory epoch/eligibility mismatch')
            if (inventory.get('source_sha256')!=frozen.get('source_sha256') or inventory.get('freeze_sha256')!=frozen.get('freeze_sha256')
                    or inventory.get('component_epoch')!=expected):raise ValueError('solver inventory source/component/cache epoch mismatch')
            candidates,population=_validated_candidates(inventory,campaign_manifest,bindings,frozen,issue)
        except Exception as exc:issue('M6_SOLVER_PLAN_DENOMINATOR',exc)
    if isinstance(summary,(str,Path)):summary=load(summary,'M8_D62_COUNTS_MISSING')
    required_rungs=set(summary.get('solve_yield',{})) if isinstance(summary,dict) else set()
    if not candidates:issue('M6_SOLVER_PLAN_INVENTORY_EMPTY','G1 requires nonempty returned solver plans')
    if not required_rungs:issue('M6_REQUIRED_RUNGS_MISSING','approval must name evaluated rungs')
    for rung in sorted(required_rungs):
        if not any(row['input'].get('rung')==rung for row in candidates):issue('M6_SOLVER_RUNG_EMPTY',rung)
    if len({inp.get('sha256') for inp in population})!=len(population):
        issue('M6_CAMPAIGN_POPULATION_INVALID','campaign allocation repeats an input digest')
    for inp in population:
        try:
            member=members.get(str(Path(inp['path']).resolve()))
            if member is None or member[0]!=inp['sha256']:raise ValueError('campaign population contains an unlisted input')
            spec=(inp.get('shard') or {}).get('split_spec') or (inp.get('shard') or {}).get('spec')
            if spec!=member[2].get('spec'):raise ValueError('campaign population split spec differs from its epoch binding')
            if spec and inp.get('rung')!=spec.get('rung'):raise ValueError('campaign rung differs from allocated spec')
        except Exception as exc:issue('M6_CAMPAIGN_POPULATION_INVALID',exc)
    from stowage.model import MovePlan
    from stowage.checker import report_matches_inputs
    live_rechecked=0
    for row in candidates:
        try:
            if row.get('artifact_error'):raise ValueError(row['artifact_error'])
            inp=row['input'];path=str(Path(inp['path']).resolve());member=members.get(path)
            if member is None or inp['sha256']!=member[0]:raise ValueError('solver plan does not use its exact regenerated epoch member')
            source=_pin(path,bindings,member[0]);instance=source.model(type(member[1]));_instance_admission(instance,frozen)
            plan_snapshot=_pin(row['plan'],bindings,row['plan_sha256']);plan=plan_snapshot.model(MovePlan)
            report,mask=execute_plan(instance,plan)  # direct component call, never read a report artifact
            if not report.passed or not report_matches_inputs(report,instance,plan) or not _full_checked(instance,plan,report):
                raise ValueError('fresh full checker rejected plan or did not complete every physical part')
            if (report.provenance.versions.get('checker')!=expected['checker_version']
                    or report.provenance.versions.get('rule_catalogue')!=expected['catalogue_loaded_sha256']):raise ValueError('fresh checker versions differ')
            if (mask.get('verified') is not True or mask.get('status')!='complete' or mask.get('global_screen')!='on'
                    or mask.get('masked_actions')!=0 or mask.get('mask_version')!=expected['mask_version']):raise ValueError('production mask replay rejected or incomplete')
            live_rechecked+=1
            observation=mask.get('d62_crossing')
            if isinstance(observation,dict):
                rung,port,ship=_stratum(inp)
                # Bind observations only immediately around this gate's own live replay.
                context=dict(input_sha256=source.sha256,plan_sha256=plan_snapshot.sha256,
                    epoch_id=frozen['epoch_id'],source_sha256=frozen['source_sha256'],component_epoch=expected,rung=rung,port=port,ship=ship)
                if any(key in observation and observation[key]!=value for key,value in context.items()):
                    raise ValueError('live crossing producer returned stale input/source/epoch metadata')
                observation=dict(observation,**context)
            observations.append(dict(input=inp,plan_sha256=plan_snapshot.sha256,measurement=dict(d62_crossing=observation)))
            source.require_fresh();plan_snapshot.require_fresh();assert_component_epoch(frozen)
        except Exception as exc:
            observations.append(dict(input=row['input'],plan_sha256=row.get('plan_sha256'),measurement={}))
            issue('M6_SOLVER_PLAN_RECHECK_FAILED',f"{row.get('plan')}: {exc}")
    counts.update(returned_solver_plans=len(candidates),live_rechecked_solver_plans=live_rechecked)
    returned_population=[row['input'] for row in candidates]
    coverage=d62_coverage(observations,required_rungs,frozen=frozen,population=returned_population,attempted_population=population)
    if not isinstance(summary,dict):issue('M8_D62_COUNTS_MISSING','5.4 approval summary absent')
    else:
        if not _eligible(summary) or summary.get('epoch_id')!=frozen.get('epoch_id'):issue('M8_D62_EPOCH_MISMATCH','approval summary epoch/eligibility mismatch')
        # Caller-written arithmetic cannot replace observed execution/population.
        if summary.get('d62_crossing')!=coverage:issue('M8_D62_COUNTS_INVALID','summary counts differ from exact live campaign population')
        acknowledgements=summary.get('d62_zero_acknowledgements',{})
        for rung,value in coverage.items():
            if value['status']!='observed':issue('M8_D62_COUNTS_UNVERIFIED',rung)
            for panel in value['by_port_ship']:
                if panel['zero_binding_flag']:
                    ack=acknowledgements.get(panel['zero_ack_key'],{}) if isinstance(acknowledgements,dict) else {}
                    if ack.get('epoch_id')!=frozen.get('epoch_id') or not isinstance(ack.get('acknowledged_by'),str) or not ack['acknowledged_by'].strip():
                        issue('M8_D62_ZERO_ACK_REQUIRED',panel['zero_ack_key'])
    if inventory is not None and candidates:
        try:_validated_candidates(inventory,campaign_manifest,bindings,frozen,issue)
        except Exception as exc:issue('M6_SOLVER_PLAN_DENOMINATOR',exc)
    for name,digest in list(bindings.items()):
        try:read_snapshot(name,expected_sha256=digest)
        except Exception as exc:issue('M6_EVIDENCE_DRIFT',exc)
    return dict(schema_version=2,phase='post_freeze',epoch_id=frozen.get('epoch_id'),complete=not issues,
        status='verified' if not issues else 'unverified',issues=issues,bindings=bindings,counts=counts,
        d62_crossing=coverage,d62_observations=observations)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-manifest',type=Path,required=True)
    parser.add_argument('--instance-manifest',type=Path,required=True)
    parser.add_argument('--campaign-manifest',type=Path,required=True,help='5.4/solver-plans.json')
    parser.add_argument('--summary',type=Path,required=True)
    parser.add_argument('--cache-work',type=Path,required=True,help='dedicated attempt-local directory under campaign-g1')
    args=parser.parse_args(argv)
    freezer=_module('freeze');frozen=freezer.check_manifest(args.freeze_manifest)
    import os
    adapter=_module('cache_adapter');adapter.scoped_path(args.cache_work)
    os.environ.update(_module('pin_transport').pin_environment(frozen['numerical_caches'],args.cache_work))
    adapter.redirect_cache(args.cache_work);adapter.enforce_scope()
    result=evidence_report(frozen,instance_manifest=args.instance_manifest,campaign_manifest=args.campaign_manifest,summary=args.summary)
    freezer.check_manifest(args.freeze_manifest)
    print(json.dumps(result,sort_keys=True,indent=2));return 0 if result['complete'] else 1


if __name__=='__main__':raise SystemExit(main())

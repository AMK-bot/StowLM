"""Train-only, resumable tiny gold generation with the full checker as judge.

A search certificate proves a cost minimum within its action model. Gold
admission instead asks whether this particular, clean plan passes the physical
checker. The tiny-only G1 exception waives the campaign gate, while every gold
plan still needs production mask replay, ballast, and a bound full report.
C2-off runs are isolated diagnostics. Public protocol: src/stowage/planner.py.
"""
from __future__ import annotations
import argparse
import ast
import importlib.util
import sys
import tomllib
import fcntl
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from eval.snapshot_io import read_snapshot
import multiprocessing
import shlex
import os
from pathlib import Path
import time

for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name]='1'
ROOT=Path(__file__).resolve().parents[3]
GROUPS=('random_yard/tiny','simulated_yard/tiny')

@dataclass(frozen=True)
class Record:
    root: Path
    group: str
    instance: str
    input_sha256: str
    spec: dict
    spec_key: str
    manifest_sha256: str
    split_version: str=''
    split_entries_sha256: str=''
    split_manifest: Path | None=None
    epoch_id: str | None=None
    required_rules: dict | None=None

@dataclass(frozen=True)
class Config:
    output: Path
    search_s: float=60.
    scalable_s: float=60.
    finalize_s: float=30.
    checker_s: float=30.
    seed: int=0
    max_nodes: int=100000
    stop_at: str='2026-10-27T23:13:00+00:00'
    hard_stop_at: str='2026-10-27T23:15:00+00:00'
    retry_failed: bool=False
    split_manifest: Path | None=None
    c2_off: bool=False
    instances: Path | None=None


def atomic_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+f'.{os.getpid()}.partial')
    data=(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode('utf-8')
    with temporary.open('wb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)
    return sha256(data).hexdigest()


def rule_policy():
    name='_tiny_gold_rule_policy'
    if name not in sys.modules:
        spec=importlib.util.spec_from_file_location(name,ROOT/'experiments/campaign-g1/rule_policy.py')
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return sys.modules[name]


def migration_evidence():
    name='_tiny_gold_migration_evidence'
    if name not in sys.modules:
        spec=importlib.util.spec_from_file_location(name,ROOT/'experiments/campaign-g1/migration_evidence.py')
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return sys.modules[name]


def rule_binding():
    name='_gold_rule_binding_observations'
    if name not in sys.modules:
        path=ROOT/'experiments/gold-plans/campaign/rule_binding.py'
        snapshot=read_snapshot(path)
        from types import ModuleType
        module=ModuleType(name);module.__file__=str(path)
        exec(compile(snapshot.data,str(path),'exec'),module.__dict__)
        snapshot.require_fresh()
        sys.modules[name]=module
    return sys.modules[name]


def required_rule_pin(frozen=None):
    if frozen is not None and not frozen.get('required_rules'):
        raise ValueError('frozen epoch lacks required_rules policy; create a new freeze')
    return rule_policy().enforce_rule_policy(ROOT,(frozen or {}).get('required_rules'))


def inspect_epoch_manifest(root,split_manifest=None,*,allow_diagnostic=False):
    """The standalone watcher/status admit only explicit frozen epoch roots."""
    from eval.splits_epoch import validate_epoch_manifest
    snapshot=read_snapshot(Path(root)/'manifest.json');manifest=snapshot.json()
    required_rule_pin(manifest.get('freeze_manifest') or {})
    validate_epoch_manifest(root,manifest,_inventory(split_manifest),allow_diagnostic=allow_diagnostic)
    snapshot.require_fresh()
    return inspect_manifest(root,split_manifest,manifest_snapshot=snapshot),snapshot


_INVENTORIES={}


def _inventory(path=None):
    """Validate once per selected release file epoch, including in worker processes."""
    from eval.splits import DEFAULT_MANIFEST
    from eval.splits_inventory import FrozenInventory
    path=Path(path or DEFAULT_MANIFEST).resolve()
    snapshot=read_snapshot(path);key=(str(path),snapshot.sha256)
    if key not in _INVENTORIES:_INVENTORIES[key]=FrozenInventory(snapshot.json())
    return _INVENTORIES[key]


def inventory_pin(*,record=None,config=None,split_manifest=None):
    selected=split_manifest
    if selected is None and config is not None:selected=config.split_manifest
    if selected is None and record is not None:selected=record.split_manifest
    inventory=_inventory(selected)
    pin=dict(split_version=inventory.version,split_entries_sha256=inventory.entries_sha256)
    if record is not None:
        for key,value in pin.items():
            if getattr(record,key) and getattr(record,key)!=value:raise ValueError('frozen split release pin mismatch')
    return pin


def _ensure_release_pin(output,pin):
    """A different release needs a fresh output directory, never a silent resume."""
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with (output/'split-release.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        target=output/'split-release.json'
        if target.exists():
            if read_snapshot(target).json()!=pin:raise ValueError('frozen split release pin drift on resume')
        else:
            prior=output/'manifest.json'
            if prior.exists():
                previous=read_snapshot(prior).json()
                if any(previous.get(key)!=value for key,value in pin.items()):
                    # Empty historical watcher metadata contains no reusable work.
                    if any((output/'state').glob('*.json')) or any((output/'plans').glob('*.json')):
                        raise ValueError('missing or changed frozen split release pin on resume')
            atomic_json(target,pin)


def _validate_record(record):
    from eval.splits import spec_key,require_membership
    inventory_pin(record=record)
    require_membership(record.spec,inventory=_inventory(record.split_manifest),split='train')
    if record.group not in GROUPS or record.spec.get('rung')!='tiny':
        raise ValueError('only frozen TRAIN tiny records are permitted')
    generator='random_yard' if record.group.startswith('random_yard/') else 'simulated'
    if record.spec.get('generator')!=generator:raise ValueError('frozen generator/group mismatch')
    if spec_key(record.spec)!=record.spec_key:raise ValueError('frozen specification key mismatch')
    if len(record.input_sha256)!=64 or any(c not in '0123456789abcdef' for c in record.input_sha256):
        raise ValueError('missing or malformed input SHA256')
    root=Path(record.root).resolve();directory=(root/record.group).resolve()
    path=(directory/record.instance).resolve()
    if not directory.is_relative_to(root) or not path.is_relative_to(directory):raise ValueError('shard escapes train group')
    return path


def inspect_manifest(root,split_manifest=None,*,manifest_snapshot=None):
    """Read metadata only; filter train/tiny before any shard file is opened."""
    root=Path(root);snapshot=manifest_snapshot or read_snapshot(root/'manifest.json');manifest=snapshot.json()
    records=[];pin=inventory_pin(split_manifest=split_manifest)
    for group in GROUPS:
        for shard in manifest.get('groups',{}).get(group,{}).get('shards',[]):
            if shard.get('split')!='train' or not shard.get('instance'):continue
            record=Record(root,group,shard['instance'],shard.get('sha256',''),
                          shard['split_spec'],shard['spec_key'],snapshot.sha256,
                          **pin,split_manifest=split_manifest,epoch_id=manifest.get('epoch_id'),
                          required_rules=(manifest.get('freeze_manifest') or {}).get('required_rules'))
            _validate_record(record);records.append(record)
    return sorted(records,key=lambda r:(r.group,r.spec_key))


def component_versions():
    from stowage.frozen import current_provenance
    from stowage.solver.exact import ExactPlanner
    from stowage.solver.scalable import SCALABLE_VERSION
    from stowage.versions import CHECKER_VERSION
    from llm.stowlm.format_prompt import PROMPT_VERSION
    from llm.stowlm.mask import MASK_VERSION
    return {**current_provenance(created_by='tiny-gold').versions,
            'checker':CHECKER_VERSION,'exact':ExactPlanner.version,'scalable':SCALABLE_VERSION,
            'prompt':PROMPT_VERSION,'mask':MASK_VERSION}


_SOURCE_CACHE={}

def _source_hash(path):
    snapshot=read_snapshot(path);key=(str(path),snapshot.sha256)
    if key in _SOURCE_CACHE:return _SOURCE_CACHE[key]
    data=snapshot.data
    if path.suffix=='.py':
        tree=ast.parse(data)
        for node in ast.walk(tree):
            if isinstance(node,(ast.Module,ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and node.body:
                first=node.body[0]
                if isinstance(first,ast.Expr) and isinstance(first.value,ast.Constant) and isinstance(first.value.value,str):node.body.pop(0)
        data=ast.dump(tree,include_attributes=False).encode()
    elif path.suffix=='.toml':data=json.dumps(tomllib.loads(data.decode()),sort_keys=True,default=str).encode()
    elif path.suffix=='.json':data=json.dumps(json.loads(data),sort_keys=True).encode()
    value=sha256(data).hexdigest();_SOURCE_CACHE[key]=value;return value


def source_fingerprint():
    """Relevant implementation epoch, ignoring Python docs/comments and generators."""
    selected={}
    for directory in ('solver','checker','feasibility','vessel','moves','actions','model','linear','rules','bounds','ports','yard'):
        for path in (ROOT/'src/stowage'/directory).rglob('*'):
            if path.is_file() and path.suffix in ('.py','.toml','.json'):
                selected[str(path.relative_to(ROOT))]=_source_hash(path)
    for name in ('conventions.py','params.py','planner.py','versions.py','frozen.py','io.py'):
        path=ROOT/'src/stowage'/name;selected[str(path.relative_to(ROOT))]=_source_hash(path)
    for relative in ('src/eval/splits.py','src/eval/splits_inventory.py','src/eval/snapshot_io.py'):
        selected[relative]=_source_hash(ROOT/relative)
    for pattern in ('mask*.py','format*.py'):
        for path in (ROOT/'src/llm/stowlm').glob(pattern):
            selected[str(path.relative_to(ROOT))]=_source_hash(path)
    from llm.stowlm.format import DEFAULT_TOKENIZER_PATH
    tokenizer=DEFAULT_TOKENIZER_PATH/'tokenizer.json'
    token_key=str(tokenizer.relative_to(ROOT)) if tokenizer.is_relative_to(ROOT) else str(tokenizer)
    if tokenizer.is_file():
        selected[token_key]=read_snapshot(tokenizer).sha256
    else:selected[token_key]='missing: production mask replay requires local tokenizer'
    for path in (ROOT/'params').rglob('*.toml'):selected[str(path.relative_to(ROOT))]=_source_hash(path)
    selected[str(Path(__file__).relative_to(ROOT))]=_source_hash(Path(__file__))
    for name in ('gold.py','rule_binding.py'):
        campaign=ROOT/'experiments/gold-plans/campaign'/name
        if campaign.is_file():selected[str(campaign.relative_to(ROOT))]=_source_hash(campaign)
    for relative in ('experiments/campaign-g1/rule_policy.py','experiments/campaign-g1/REQUIRED_RULES.json','experiments/campaign-g1/migration_evidence.py','experiments/campaign-g1/migration_contract.py',
                     'experiments/campaign-g1/migration_canaries.py','experiments/campaign-g1/freeze.py',
                     'experiments/campaign-g1/trusted_receipts.py','experiments/campaign-g1/qualified_canary_cases.py',
                     'experiments/campaign-g1/admission-trust.json'):
        selected[relative]=_source_hash(ROOT/relative)
    return {'sha256':sha256(json.dumps(selected,sort_keys=True).encode()).hexdigest(),'sources':selected}


def _phase(function,args,limit_s):
    from stowage.solver.exact_deadline import run_with_deadline
    before=source_fingerprint();versions_before=component_versions();started=time.monotonic()
    outcome=run_with_deadline(function,args,deadline=started+limit_s)
    return outcome.value,dict(wall_time_s=time.monotonic()-started,limit_s=limit_s,
        timed_out=outcome.timed_out,worker_pid=outcome.worker_pid,
        sources_before=before,sources_after=source_fingerprint(),
        versions_before=versions_before,versions_after=component_versions())


def _feasibility_config(config):
    from stowage.feasibility import FeasibilityConfig
    # D49 necessary C2 and D55 local visibility are the engine defaults. The
    # explicit diagnostic override disables C2 alone, never local hard rules.
    return FeasibilityConfig(extra={'global_screen':0.}) if config.c2_off else FeasibilityConfig()


def _planner(kind,config):
    if kind=='exact':
        from stowage.solver.exact import ExactPlanner
        return ExactPlanner(num_workers=1,max_nodes=config.max_nodes,feasibility_config=_feasibility_config(config))
    from stowage.solver.scalable import ScalableConfig, ScalableSolver
    return ScalableSolver(ScalableConfig(feasibility_config=_feasibility_config(config)))


def _plan(instance,kind,config):
    planner=_planner(kind,config)
    limit=config.search_s if kind=='exact' else config.scalable_s
    return planner.plan(instance,seed=config.seed,time_limit_s=limit)


def _finalize(instance,result,config):
    from stowage.planner import finalize
    return finalize(instance,result,time_limit_s=config.finalize_s)


def _check(instance,plan):
    from stowage.checker import check
    return check(instance,plan)


_PROVENANCE_FIELD_ADAPTERS={}


def _require_clean(value):
    """Check artifact taint recursively; validate design evidence by its type.

    A field called provenance may be PlacementMapEvidence or D74 source text,
    rather than artifact Provenance. It is never safe to swallow TypeError or
    skip that subtree. Keep visited objects alive, including validated copies.
    """
    from collections.abc import Mapping
    from dataclasses import fields,is_dataclass
    from stowage.frozen import require_clean
    from pydantic import BaseModel,TypeAdapter
    from stowage.model import Provenance
    visited={}
    def visit(item):
        if id(item) in visited:return
        visited[id(item)]=item
        if isinstance(item,Provenance):
            require_clean(item,what='tiny gold')
            if item.input_versions:raise ValueError('tiny gold refuses unclean mixed-version provenance')
        elif isinstance(item,BaseModel):
            for name,field in type(item).model_fields.items():
                child=getattr(item,name)
                visit(child)  # Taint in the original is checked before any conversion.
                if child is not None and (name=='provenance' or name.endswith('_provenance')):
                    key=(type(item),name)
                    if key not in _PROVENANCE_FIELD_ADAPTERS:
                        _PROVENANCE_FIELD_ADAPTERS[key]=TypeAdapter(field.rebuild_annotation())
                    payload=child.model_dump(mode='python') if isinstance(child,BaseModel) else child
                    visit(_PROVENANCE_FIELD_ADAPTERS[key].validate_python(payload,strict=True))
        elif isinstance(item,Mapping):
            for name,child in item.items():
                if (isinstance(name,str) and (name=='provenance' or name.endswith('_provenance'))
                        and isinstance(child,Mapping) and ('stubs' in child or 'input_versions' in child)):
                    visit(Provenance.model_validate(child))
                visit(child)
        elif isinstance(item,(list,tuple,set,frozenset)):
            for child in item:visit(child)
        elif is_dataclass(item) and not isinstance(item,type):
            for field in fields(item):visit(getattr(item,field.name))
        elif getattr(item,'provenance',None) is not None:
            require_clean(item,what='tiny gold')
    visit(value)


def parse_check_report(payload):
    """Restore only the current checker's declared extensions, with no extras lost.

    D74 full reports serialize their part subtypes, including lashing notes.
    The base CheckReport intentionally forbids those JSON fields, so restore
    known subtypes before validating the full report. Unknown fields still fail.
    """
    from stowage.model import CheckReport,PartReport
    from stowage.checker.in_port_report import InPortCheckReport,InPortReport
    from stowage.checker.lashing import LashingReport
    if not isinstance(payload,dict):raise TypeError('checker report must be an object')
    parts=payload.get('parts',{})
    if not isinstance(parts,dict):raise TypeError('checker report parts must be an object')
    restored={};extended=False
    for name,part in parts.items():
        if not isinstance(part,dict):raise TypeError('checker report part must be an object')
        schema=PartReport
        if name=='in_port' and 'ballast_programme' in part:
            schema=InPortReport;extended=True
        elif (name=='lashing' or name.endswith(':lashing')) and 'notes' in part:
            schema=LashingReport;extended=True
        restored[name]=schema.model_validate(part)
    schema=InPortCheckReport if extended else CheckReport
    return schema.model_validate({**payload,'parts':restored})


def _full_hull(instance):
    hull=instance.vessel.hull
    return bool(hull is not None and hull.stations and hull.hull_offsets_ref)


def _structured_plan(instance,plan):
    from stowage.actions import get_action_space
    space=get_action_space();actions=space.from_plan(instance,plan)
    encoded=[space.encode(action) for action in actions]
    if space.expand(instance,actions).moves!=plan.moves:raise ValueError('action expansion changes checked moves')
    return plan.model_copy(update={'actions':encoded,'action_space':f'{space.name}@{space.version}'})


def _replay_mask(instance,plan,config):
    """Replay all canonical tokens and the end marker through the CPU production mask.

    No LM, GPU, sampling or behavior-logprob calculation is needed: admission
    checks allowed tokens only. Mask 1.2 temperature affects scores, not sets.
    """
    from dataclasses import asdict
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MASK_VERSION, MaskConfig, MaskedDecoder
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine
    space=get_action_space();actions=space.from_plan(instance,plan)
    feasibility=_feasibility_config(config)
    engine=make_feasibility_engine(instance,space,config=feasibility)
    global_screen=getattr(engine,'diagnostics',{}).get('global_screen','unavailable: not reported')
    if not config.c2_off and global_screen!='on':
        raise ValueError(f'production mask requires actual C2 on; {global_screen}')
    # Direct repeated admissions must not inherit a tokenizer object loaded
    # before the fingerprinted tokenizer file changed.
    load_tokenizer.cache_clear()
    decoder=MaskedDecoder(engine,ActionCodec(load_tokenizer(),[box.id for box in instance.containers]),MaskConfig())
    for action in actions:
        for token in decoder.codec.encode_action(action):decoder.advance(token)
    for token in decoder.codec.end_plan_tokens:decoder.advance(token)
    if decoder.status!='complete' or decoder.actions!=list(actions):
        raise ValueError('production mask replay did not complete the checked actions')
    return dict(verified=True,status=decoder.status,actions=len(actions),tokens=len(decoder.tokens),
        mask_version=MASK_VERSION,mask_config=asdict(decoder.config),
        feasibility_config=asdict(feasibility),global_screen=global_screen,
        c2='off/diagnostic' if config.c2_off else 'on/default')


def _voyage_report_complete(instance,report):
    """Require D50's four endpoint parts as well as canonical shipping evidence."""
    if instance.voyage is None:return False
    return all(f'condition:{identity.id}:{part}' in report.parts
               for identity in (instance.voyage.departure,instance.voyage.arrival)
               for part in ('stability','strength')) and 'voyage' in report.parts


def _judge(instance,kind,config):
    phases={}
    def expired(stage,summary=None):
        return {**(summary or {}),'accepted':False,'status':'timeout',
                'message':f'absolute hard-stop reserve expired before {stage}','phases':phases}
    # Exact retains its public watchdog; do not nest it in another daemon guard.
    if kind=='exact':
        before=source_fingerprint();versions_before=component_versions()
        limit=remaining_phase_s(config,config.search_s)
        if limit<=0:return expired('exact planning')
        started=time.monotonic();result=_plan(instance,kind,replace(config,search_s=limit))
        phases['plan']=dict(wall_time_s=time.monotonic()-started,limit_s=limit,
            sources_before=before,sources_after=source_fingerprint(),versions_before=versions_before,
            versions_after=component_versions(),timed_out=result.status=='timeout')
    else:
        limit=remaining_phase_s(config,config.scalable_s+2.)
        if limit<=0:return expired('scalable planning')
        search_limit=min(config.scalable_s,limit)
        result,phases['plan']=_phase(_plan,(instance,kind,replace(config,scalable_s=search_limit)),limit)
    if result is None:return dict(accepted=False,status='timeout',phases=phases)
    summary=dict(accepted=False,status=result.status,raw_status=result.status,message=result.message,
        mask_verified=False,shippable=False,report_binding_valid=False,
        global_screen=result.metrics.get('global_screen','unavailable: planner did not report actual screen'),
        raw_objective=result.metrics.get('objective'),metrics=result.metrics,phases=phases)
    if result.plan is None:return summary
    if kind=='exact' and not config.c2_off and result.metrics.get('global_screen')!='on':
        return {**summary,'message':'gold requires actual C2 global_screen=on'}
    _require_clean(result)
    # Frozen 0.9 direct-caller guard. A timeout incumbent may be reconsidered
    # only as a complete candidate; it must already honor its planner's ballast
    # declaration. Missing exact ballast is never supplied behind its back.
    if _planner(kind,config).sets_ballast and result.plan.ballast is None:
        return {**summary,'message':'Planner declares sets_ballast=True but returned ballast=None.'}
    judge_result=result.model_copy(update={'status':'ok'}) if result.status=='timeout' else result
    if result.status=='timeout':summary['timeout_policy']='recheck_complete_incumbent_without_optimality_claim'
    limit=remaining_phase_s(config,config.finalize_s)
    if limit<=0:return expired('finalization',summary)
    judge_result,phases['finalize']=_phase(_finalize,(instance,judge_result,replace(config,finalize_s=limit)),limit)
    if judge_result is None:
        return {**summary,'status':'timeout','failure_stage':'finalize','finalize_status':'timeout',
                'message':'ballast finalization deadline; no completed finalized result'}
    summary['finalize_status']=judge_result.status
    if judge_result.plan is None or judge_result.status!='ok':
        return {**summary,'status':judge_result.status if judge_result.status!='ok' else 'finalize_failed',
                'failure_stage':'finalize',
                'message':f'ballast finalization {judge_result.status}: {judge_result.message or "no complete finalized plan"}'}
    _require_clean(judge_result)
    if judge_result.plan.ballast is None:return {**summary,'message':'missing own/finalized ballast'}
    plan=_structured_plan(instance,judge_result.plan);_require_clean(plan)
    limit=remaining_phase_s(config,config.checker_s)
    if limit<=0:return expired('full checking',summary)
    report,phases['checker']=_phase(_check,(instance,plan),limit)
    if report is None:return {**summary,'status':'timeout','failure_stage':'checker','message':'full checker deadline'}
    _require_clean(report)
    summary.update(checker_passed=report.passed,report=report.model_dump(mode='json'),
        rule_binding=rule_binding().observe(instance,plan))
    if not report.passed:return {**summary,'failure_stage':'checker','status':'checker_failed','message':'full checker rejected finalized candidate; see report violations'}
    from stowage.checker import report_matches_inputs
    summary['report_binding_valid']=report_matches_inputs(report,instance,plan)
    if not summary['report_binding_valid']:return {**summary,'message':'full checker report input binding mismatch'}
    # Freeze the exact structured candidate, then attach fresh private evidence.
    # Nothing in the plan/report may change after this binding.
    judge_result=judge_result.model_copy(update={'plan':plan})
    judge_result._record_full_check(instance,report)
    summary['shippable']=judge_result.shippable
    if not summary['shippable']:return {**summary,'message':'full checker evidence is not shippable'}
    if not _voyage_report_complete(instance,report):
        return {**summary,'shippable':False,'message':'D50 departure/arrival checker evidence missing'}
    limit=remaining_phase_s(config,config.checker_s)
    if limit<=0:return expired('production mask replay',summary)
    try:
        replay,phases['mask_replay']=_phase(_replay_mask,(instance,plan,config),limit)
    except Exception as error:
        replay=dict(verified=False,status='error',error_type=type(error).__name__,message=str(error),
                    c2='off/diagnostic' if config.c2_off else 'on/default')
    if replay is None:replay=dict(verified=False,status='timeout',message='production mask replay deadline')
    summary.update(mask_replay=replay,mask_verified=bool(replay.get('verified')),
                   global_screen=replay.get('global_screen',summary['global_screen']))
    if not summary['mask_verified']:return {**summary,'status':'mask_replay_failed','failure_stage':'mask_replay','message':'production mask replay failed: '+str(replay.get('message',replay.get('status')))}
    if any(p['versions_before']!=p['versions_after'] for p in phases.values()):
        return {**summary,'message':'component versions changed during a phase'}
    epochs=[p.get(edge,{}).get('sha256') for p in phases.values() for edge in ('sources_before','sources_after')]
    if len(set(epochs))!=1:return {**summary,'message':'relevant source epoch changed across planning/finalization/checking'}
    from stowage.moves import count_moves
    objective=count_moves(instance,plan).weighted_nonproductive
    summary.update(accepted=True,objective=objective,checked_objective=objective,plan=plan.model_dump(mode='json'))
    return summary


def _safe_judge(instance,kind,config):
    try:return _judge(instance,kind,config)
    except Exception as error:return dict(accepted=False,status='error',error=f'{type(error).__name__}: {error}')


def record_key(record,config):
    return sha256(json.dumps(dict(input=record.input_sha256,spec_key=record.spec_key,
        **inventory_pin(record=record,config=config),
        epoch_id=record.epoch_id,required_rules=record.required_rules,
        versions=component_versions(),source_sha256=source_fingerprint()['sha256'],seed=config.seed,search_s=config.search_s,
        scalable_s=config.scalable_s,finalize_s=config.finalize_s,checker_s=config.checker_s,
        max_nodes=config.max_nodes,c2_off=config.c2_off),sort_keys=True).encode()).hexdigest()


def process_one(record,config):
    """Execute checked exact/scalable attempts; publish an approved candidate."""
    from eval.splits import spec_from_instance,spec_key
    from stowage.model import Instance
    from stowage.bounds import lower_bound
    path=_validate_record(record);snapshot=read_snapshot(path,expected_sha256=record.input_sha256)
    instance=snapshot.model(Instance)
    if spec_key(spec_from_instance(instance))!=record.spec_key:raise ValueError('instance frozen specification mismatch')
    _require_clean(instance)
    if not _full_hull(instance):raise ValueError('current full hull geometry required')
    policy_pin=required_rule_pin({'required_rules':record.required_rules} if record.required_rules else None)
    versions=component_versions();components=migration_evidence().current_component_epoch()
    if versions.get('checker')!=components['checker_version']:raise ValueError('checker epoch differs from loaded checker component')
    split_pin=inventory_pin(record=record,config=config)
    relevant_source_sha256=source_fingerprint()['sha256']
    started=time.monotonic();exact=_safe_judge(instance,'exact',config);scalable=_safe_judge(instance,'scalable',config)
    bound_limit=remaining_phase_s(config,min(10.,config.checker_s))
    try:
        if bound_limit>0:bound,lb_phase=_phase(_bound,(instance,),bound_limit)
        else:bound,lb_phase=None,dict(timed_out=True,limit_s=0.,message='absolute hard-stop reserve expired before lower bound')
    except Exception as error:bound,lb_phase=None,dict(error=f'{type(error).__name__}: {error}')
    for candidate in (exact,scalable):
        if candidate.get('accepted') and source_fingerprint()['sha256']!=relevant_source_sha256:
            candidate.update(accepted=False,message='relevant source epoch changed before atomic gold publication')
        for gate in ('mask_verified','shippable'):
            if candidate.get('accepted') and candidate.get(gate) is not True:
                candidate.update(accepted=False,message=f'missing independent {gate} admission evidence')
    gold_source='exact' if exact.get('accepted') else 'scalable'
    candidate=exact if gold_source=='exact' else scalable
    quality_gap=None;optimal_quality_gap=None
    if exact.get('accepted') and scalable.get('accepted'):
        e,s=exact['objective'],scalable['objective'];quality_gap=(s-e)/s if s else (0. if e==0 else None)
        if exact.get('raw_status')!='timeout' and exact.get('metrics',{}).get('optimality_status')=='OPTIMAL':optimal_quality_gap=quality_gap
    bound_quality_gap=None
    if candidate.get('accepted') and bound is not None:
        import math
        objective=float(candidate['objective'])
        if math.isfinite(float(bound)) and 0 <= bound <= objective:
            bound_quality_gap=(objective-bound)/objective if objective else 0.
    gap_status=('comparison_available' if quality_gap is not None else
        'bound_available_comparison_unavailable' if bound_quality_gap is not None else 'unavailable')
    optimal_engine=(gold_source=='exact' and exact.get('raw_status')!='timeout'
                    and exact.get('metrics',{}).get('optimality_status')=='OPTIMAL')
    outcome=dict(format='tiny-gold-attempt@1.0',accepted=candidate['accepted'],gold_source=gold_source,
        epoch_id=record.epoch_id,required_rules=policy_pin,component_epoch=components,data_mode='pipeline_test',
        purpose='tiny_g1_exception_pipeline_test',production_eligible=False,
        status=('optimal_engine' if optimal_engine else 'feasible') if candidate['accepted'] else candidate.get('status','error'),
        relevant_source_sha256=relevant_source_sha256,input_sha256=record.input_sha256,
        spec_key=record.spec_key,split='train',group=record.group,instance_id=instance.id,
        rule_binding=candidate.get('rule_binding') or rule_binding().observe(instance),
        exact=exact,scalable=scalable,lower_bound=bound,lower_bound_phase=lb_phase,
        quality_gap=quality_gap,optimal_quality_gap=optimal_quality_gap,
        bound_quality_gap=bound_quality_gap,
        bound_quality_gap_formula='(checked_objective-lower_bound)/checked_objective; zero when both zero',
        bound_quality_gap_scope='weighted_nonproductive_lifts_lower_bound',
        quality_gap_status=gap_status,
        quality_gap_unavailable_reason=('no accepted scalable comparison or valid scoped lower bound' if gap_status=='unavailable' else None),
        quality_gap_formula='(scalable_checked-exact_checked)/scalable_checked; best-found unless exact OPTIMAL',
        wall_time_s=time.monotonic()-started,component_versions=versions,
        mask_verified=candidate.get('mask_verified',False),shippable=candidate.get('shippable',False),
        c2='off/diagnostic' if config.c2_off else 'on/default',**split_pin)
    if not candidate['accepted']:return outcome
    _require_clean(instance)
    read_snapshot(path,expected_sha256=record.input_sha256)
    from stowage.versions import ACTION_SPACE_VERSION
    artifact={**outcome,'format':'tiny-gold@1.0','plan':candidate['plan'],'check_report':candidate['report'],
        'actions_schema':f'boxslot@{ACTION_SPACE_VERSION}','objective':candidate['objective'],
        'gold_status':'optimal_engine' if optimal_engine else 'feasible',
        'optimality_scope':'engine_model_weighted_lifts; tertiary margins not certified' if optimal_engine else 'feasible_incumbent; no optimum certified',
        'tiny_g1_exception':True,'production_g1_required':False,
        'data_mode':'pipeline_test','purpose':'tiny_g1_exception_pipeline_test',
        'production_eligible':False,'required_rules':policy_pin,'component_epoch':components,
        'split_spec':record.spec,'manifest_sha256':record.manifest_sha256,
        'input_relative_path':str(Path(record.group)/record.instance)}
    if config.c2_off:
        # Diagnostic physical evidence never enters the production gold inventory.
        artifact.update(format='tiny-gold-diagnostic@1.0',accepted=False,diagnostic_accepted=True)
        destination=Path(config.output)/'diagnostics'/f'{record.spec_key}.json'
        checksum=atomic_json(destination,artifact)
        read_snapshot(destination,expected_sha256=checksum)
        return {**outcome,'accepted':False,'diagnostic_accepted':True,'status':'diagnostic_only',
                'diagnostic_file':str(destination),'diagnostic_sha256':checksum}
    destination=Path(config.output)/'plans'/f'{record.spec_key}.json'
    checksum=atomic_json(destination,artifact)
    read_snapshot(destination,expected_sha256=checksum)
    return {**outcome,'gold_file':str(destination),'gold_sha256':checksum}


def _bound(instance):
    from stowage.bounds import lower_bound
    return lower_bound(instance).weighted


def now_utc():return datetime.now(timezone.utc)


def _now():return now_utc().timestamp()


def _epoch(value):return datetime.fromisoformat(value).timestamp()


def remaining_phase_s(config,requested):
    return max(0.,min(float(requested),_epoch(config.hard_stop_at)-_now()-2.))


def case_budget(config):
    # Both candidates need separate bounded full-check and mask-replay phases.
    return config.search_s+config.scalable_s+2*(config.finalize_s+2*config.checker_s)+30.


def write_manifest(output,split_manifest=None,*,allowed_splits=('train',),allowed_rungs=('tiny',),campaign_metadata=None,
                   instances=None,resume_config=None):
    """Publish only hash-verified, clean checked envelopes from this source epoch."""
    from eval.splits import spec_key,require_membership
    from stowage.versions import ACTION_SPACE_VERSION
    from stowage.model import MovePlan,CheckReport
    pin=inventory_pin(split_manifest=split_manifest);inventory=_inventory(split_manifest)
    policy_pin=required_rule_pin();components=migration_evidence().current_component_epoch()
    output=Path(output);_ensure_release_pin(output,pin)
    with (output/'manifest.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        entries=[];excluded=[];versions=component_versions();epoch=source_fingerprint()['sha256']
        states=[];state_snapshots=[]
        for state_path in (output/'state').glob('*.json'):
            try:
                state_snapshot=read_snapshot(state_path);states.append(state_snapshot.json());state_snapshots.append(state_snapshot)
            except (OSError,json.JSONDecodeError):continue
        accepted_hashes={}
        for state in states:
            if state.get('accepted') and state.get('gold_file'):
                accepted_hashes.setdefault(Path(state['gold_file']).resolve(),set()).add(state.get('gold_sha256'))
        for path in sorted((output/'plans').glob('*.json')):
            try:
                snapshot=read_snapshot(path);checksum=snapshot.sha256
                if checksum not in accepted_hashes.get(path.resolve(),set()):raise ValueError('gold SHA does not match a completed accepted state')
                payload=snapshot.json()
                if payload.get('format')!='tiny-gold@1.0' or not payload.get('accepted'):raise ValueError('not accepted gold')
                if payload.get('split') not in allowed_splits:raise ValueError('split not enabled for this publication')
                require_membership(payload['split_spec'],inventory=inventory,split=payload['split'])
                if any(payload.get(key)!=value for key,value in pin.items()):raise ValueError('frozen split release pin mismatch')
                if payload['split_spec'].get('rung') not in allowed_rungs or spec_key(payload['split_spec'])!=payload['spec_key']:raise ValueError('frozen spec mismatch')
                if payload.get('diagnostic') or payload.get('evidence_eligible') is False:raise ValueError('diagnostic gold cannot be published')
                data_mode=payload.get('data_mode','pipeline_test')
                if data_mode not in ('production','pipeline_test'):raise ValueError('unsupported gold data_mode')
                if payload.get('required_rules') is not None and payload['required_rules']!=policy_pin:raise ValueError('gold required_rules policy mismatch')
                if data_mode=='production':
                    if payload.get('component_epoch')!=components:raise ValueError('M5_GOLD_COMPONENT_MISMATCH: production gold component epoch absent/stale')
                    if payload.get('tiny_g1_exception') or payload.get('required_rules')!=policy_pin:raise ValueError('unapproved tiny or unpinned policy cannot be production gold')
                    approval=(payload.get('g1_summary') or {}).get('approval')
                    if not isinstance(approval,dict):raise ValueError('production gold requires same-epoch approved G1 with min_yield')
                    rule_policy().validate_g1_approval(approval,payload.get('epoch_id'),[payload['split_spec']['rung']])
                gold_source=payload.get('gold_source','exact')
                if gold_source not in ('exact','scalable'):raise ValueError('unknown gold source')
                candidate=payload[gold_source]
                plan=MovePlan.model_validate(payload['plan']);report=parse_check_report(payload['check_report'])
                _require_clean(plan);_require_clean(report)
                if not report.passed or plan.action_space!=f'boxslot@{ACTION_SPACE_VERSION}' or plan.actions is None:raise ValueError('checker/actions gate')
                if not candidate.get('accepted') or not payload.get('shippable') or not candidate.get('shippable'):raise ValueError('shippable gate')
                if not payload.get('mask_verified') or not candidate.get('mask_verified'):raise ValueError('production mask replay gate')
                if payload.get('c2')!='on/default' or candidate.get('global_screen')!='on':raise ValueError('C2 gate')
                if payload['component_versions']!=versions or payload.get('relevant_source_sha256')!=epoch:raise ValueError('recorded prior model epoch')
                # Freshness is a separate check against the originally validated bytes;
                # never attach a replacement read's digest to the passing claims.
                read_snapshot(path,expected_sha256=snapshot.sha256)
                entries.append(dict(path=str(path.relative_to(output)),sha256=checksum,input_sha256=payload['input_sha256'],
                    spec_key=payload['spec_key'],instance_id=payload['instance_id'],objective=payload['objective'],
                    gold_status=payload['gold_status'],checker_passed=True,mask_verified=True,shippable=True,
                    split=payload['split'],group=payload['group'],gold_source=gold_source,
                    data_mode=data_mode,purpose='sft_training' if data_mode=='production' else 'tiny_g1_exception_pipeline_test',
                    required_rules=policy_pin,component_epoch=payload.get('component_epoch'),
                    component_epoch_status='verified' if payload.get('component_epoch')==components else 'legacy_unverified',
                    epoch_id=payload.get('epoch_id'),g1_summary=payload.get('g1_summary'),
                    rule_binding=payload.get('rule_binding'),
                    g1_approval=(payload.get('g1_summary') or {}).get('approval'),**pin))
            except Exception as error:excluded.append(dict(path=str(path.relative_to(output)),reason=f'{type(error).__name__}: {error}'))
        roots={str(Path(state['instance_root']).resolve()) for state in states if state.get('instance_root')}
        explicit=instances or (resume_config.instances if resume_config is not None else None) or (campaign_metadata or {}).get('instance_root')
        if explicit is not None:roots.add(str(Path(explicit).resolve()))
        if len(roots)>1:raise ValueError('publication mixes instance roots; no safe resume command')
        instance_root=next(iter(roots)) if roots else None
        resume_output=str(output.relative_to(ROOT)) if output.is_relative_to(ROOT) else str(output)
        resume=resume_config or Config(output=output)
        resume_command=(('rm -f '+shlex.quote(str(Path(resume_output)/'STOP_REQUESTED'))+'\n'
            'UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/gold-plans/tiny/driver.py --max-workers 1 --limit 1 --output '+shlex.quote(resume_output)+
            ' --instances '+shlex.quote(instance_root)+
            (' --split-manifest '+shlex.quote(str(Path(split_manifest).resolve())) if split_manifest is not None else '')+
            (' --c2-off' if resume.c2_off else '')+
            ' --stop-at '+shlex.quote(resume.stop_at)+' --hard-stop-at '+shlex.quote(resume.hard_stop_at)) if instance_root else None)
        manifest=dict(format='tiny-gold-manifest@1.0',accepted=len(entries),shards=entries,excluded=excluded,
            **pin,
            failed_attempts=sum(not r.get('accepted') and r.get('status')!='running' for r in states),
            running_attempts=sum(r.get('status')=='running' for r in states),component_versions=versions,
            relevant_source_sha256=epoch,updated_unix_s=_now(),tiny_g1_exception=True,c2='on/default',
            instance_root=instance_root,resume_command=resume_command,
            resume_unavailable_reason=None if instance_root else 'explicit instance root not recorded; supply --instances')
        if campaign_metadata:manifest.update(campaign_metadata)
        terminal=[state for state in states if state.get('status')!='running']
        published={entry['spec_key']:entry.get('rule_binding') for entry in entries}
        manifest['rule_binding_yield']=rule_binding().summarize(terminal,published_spec_keys=set(published),
            published_observations=published)
        manifest['rule_binding_pending_attempts']=sum(state.get('status')=='running' for state in states)
        manifest['rule_binding_note']='All terminal attempts, including failures/timeouts; unknowns explicit. Published counts use admitted envelope observations once per spec. DG binding means a positive separation obligation, not minimum-distance slack.'
        production=bool(entries) and all(entry['data_mode']=='production' for entry in entries)
        manifest.update(data_mode='production' if production else 'pipeline_test',
            purpose='sft_training' if production else 'tiny_g1_exception_pipeline_test',
            production_eligible=production,required_rules=policy_pin,component_epoch=components if production else None)
        if not production:manifest['tiny_g1_exception']=True
        epochs={entry.get('epoch_id') for entry in entries}
        if len(epochs)==1:manifest['epoch_id']=next(iter(epochs))
        # Recheck every admitted publication/state immediately before exposure.
        for state_snapshot in state_snapshots:read_snapshot(state_snapshot.path,expected_sha256=state_snapshot.sha256)
        for entry in entries:read_snapshot(output/entry['path'],expected_sha256=entry['sha256'])
        if migration_evidence().current_component_epoch()!=components:raise ValueError('M5_COMPONENT_EPOCH_MISMATCH: loaded catalogue/components changed during publication')
        if required_rule_pin()!=policy_pin:raise ValueError('required rule policy changed during gold publication')
        if component_versions()!=versions or source_fingerprint()['sha256']!=epoch:
            raise ValueError('source or component versions changed during gold publication')
        atomic_json(output/'manifest.json',manifest)
        return manifest


def run_one(record,config):
    """Resume identity is saved input + frozen spec + versions + solver budgets."""
    _validate_record(record)
    if _now()+case_budget(config)>min(_epoch(config.stop_at),_epoch(config.hard_stop_at)):
        return dict(accepted=False,status='stop_boundary',spec_key=record.spec_key)
    pin=inventory_pin(record=record,config=config);_ensure_release_pin(config.output,pin)
    key=record_key(record,config);state=Path(config.output)/'state'/f'{key}.json'
    if state.exists():
        previous=read_snapshot(state).json()
        if previous.get('accepted') and Path(previous.get('gold_file','')).is_file():
            gold=Path(previous['gold_file'])
            try:input_unchanged=read_snapshot(_validate_record(record)).sha256==record.input_sha256
            except OSError:input_unchanged=False
            if input_unchanged and previous.get('gold_sha256')==read_snapshot(gold).sha256:
                write_manifest(config.output,config.split_manifest,instances=record.root,resume_config=config);return {**previous,'resumed':True}
        if not previous.get('accepted') and previous.get('status')!='running' and not config.retry_failed:return {**previous,'resumed':True}
    atomic_json(state,dict(status='running',accepted=False,key=key,spec_key=record.spec_key,
                          input_sha256=record.input_sha256,**pin,started_unix_s=time.time(),worker_pid=os.getpid()))
    try:outcome=process_one(record,config)
    except Exception as error:outcome=dict(accepted=False,status='error',error=f'{type(error).__name__}: {error}',spec_key=record.spec_key,input_sha256=record.input_sha256)
    outcome.update(key=key,instance_root=str(record.root.resolve()),**pin,finished_unix_s=time.time());atomic_json(state,outcome)
    write_manifest(config.output,config.split_manifest,instances=record.root,resume_config=config)
    return outcome


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--instances',type=Path,required=True,help='explicit frozen epoch instance root; legacy instances-v3 defaults retired')
    p.add_argument('--output',type=Path,default=Path(__file__).resolve().parent)
    p.add_argument('--split-manifest',type=Path,default=None,help='trusted frozen split release (default: v2)')
    p.add_argument('--c2-off',action='store_true',help='diagnostic only; never publishes production gold')
    p.add_argument('--max-workers',type=int,choices=(1,2,3),default=1)
    p.add_argument('--poll-s',type=float,default=15.)
    p.add_argument('--once',action='store_true');p.add_argument('--limit',type=int,default=None)
    p.add_argument('--retry-failed',action='store_true')
    p.add_argument('--stop-file',type=Path,default=None,help='finish in-flight cases and stop when this file exists')
    p.add_argument('--stop-at',default=Config.stop_at);p.add_argument('--hard-stop-at',default=Config.hard_stop_at)
    args=p.parse_args();config=Config(output=args.output,retry_failed=args.retry_failed,
        stop_at=args.stop_at,hard_stop_at=args.hard_stop_at,split_manifest=args.split_manifest,c2_off=args.c2_off,instances=args.instances)
    stop_file=args.stop_file or args.output/'STOP_REQUESTED'
    pin=inventory_pin(config=config);_ensure_release_pin(args.output,pin)
    submitted=set();active={};completed=0
    atomic_json(args.output/'watcher-start.json',dict(pid=os.getpid(),started_unix_s=_now(),
        max_workers=args.max_workers,stop_file=str(stop_file),stop_at=config.stop_at,hard_stop_at=config.hard_stop_at,
        c2='off/diagnostic' if config.c2_off else 'on/default',**pin))
    print(json.dumps(dict(phase='watcher_start',pid=os.getpid(),max_workers=args.max_workers,stop_file=str(stop_file))),flush=True)
    write_manifest(args.output,args.split_manifest,instances=args.instances,resume_config=config)
    with ProcessPoolExecutor(max_workers=args.max_workers,mp_context=multiprocessing.get_context('spawn'),max_tasks_per_child=1) as pool:
        last_manifest=None
        while _now()<_epoch(config.stop_at):
            if stop_file.exists():break
            if args.limit is not None and completed>=args.limit:break
            if _now()+case_budget(config)>min(_epoch(config.stop_at),_epoch(config.hard_stop_at)):break
            try:records,_=inspect_epoch_manifest(args.instances,split_manifest=args.split_manifest,allow_diagnostic=config.c2_off)
            except (FileNotFoundError,json.JSONDecodeError):records=[]
            manifest_marker=tuple((r.spec_key,r.input_sha256) for r in records)
            if manifest_marker!=last_manifest:
                print(json.dumps(dict(phase='manifest_scan',eligible_train_tiny=len(records),unix_s=_now())),flush=True)
                last_manifest=manifest_marker
            for record in records:
                if len(active)>=args.max_workers or (args.limit is not None and completed+len(active)>=args.limit):break
                key=record_key(record,config)
                if key in submitted:continue
                submitted.add(key);active[pool.submit(run_one,record,config)]=key
            if active:
                done,_=wait(active,timeout=min(args.poll_s,1.),return_when=FIRST_COMPLETED)
                for future in done:
                    key=active.pop(future);value=future.result();completed+=1
                    print(json.dumps(dict(key=key,accepted=value.get('accepted',False),status=value.get('status'),gold_file=value.get('gold_file'),resumed=value.get('resumed',False))),flush=True)
            elif args.once:break
            else:time.sleep(min(args.poll_s,max(0.,_epoch(config.stop_at)-_now())))
            if args.once and not active and all(record_key(r,config) in submitted for r in records):break
    for future,key in active.items():
        value=future.result();completed+=1
        print(json.dumps(dict(key=key,accepted=value.get('accepted',False),status=value.get('status'),finished_after_stop_request=True)),flush=True)
    atomic_json(args.output/'watcher-state.json',dict(completed=completed,submitted=len(submitted),stopped_unix_s=time.time(),stop_at=config.stop_at,hard_stop_at=config.hard_stop_at,
        stop_requested=stop_file.exists(),pid=os.getpid(),c2='off/diagnostic' if config.c2_off else 'on/default',**pin))

if __name__=='__main__':main()

"""Diagnostic-only C2-off observations; never a production canary proof.

Run from an isolated dev tree with its catalogue switch physically enabled.
The production gate and its default-C2 criteria are imported without alteration.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time

from eval.snapshot_io import read_snapshot

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def module(name):
    spec=importlib.util.spec_from_file_location('preflip_'+name,HERE/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.partial')
    temporary.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


@contextmanager
def deadline(seconds):
    previous=signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(TimeoutError(f'diagnostic phase exceeded {seconds}s')))
    signal.setitimer(signal.ITIMER_REAL,seconds)
    try:yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)


def mask_observation(instance,built):
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine,FeasibilityConfig
    from llm.stowlm.format import ActionCodec,load_tokenizer
    from llm.stowlm.mask import MaskedDecoder,MaskConfig,MaskError
    config=FeasibilityConfig(extra={'global_screen':0})
    space=get_action_space();engine=make_feasibility_engine(instance,space,config=config)
    result=dict(diagnostic=True,evidence_eligible=False,feasibility_config=asdict(config),
        global_screen=engine.diagnostics.get('global_screen'),production_c2_active=False,rule_ids=[])
    if not str(result['global_screen']).startswith('off: config'):
        raise ValueError('diagnostic must keep C2 explicitly off')
    decoder=MaskedDecoder(engine,ActionCodec(load_tokenizer(),[box.id for box in instance.containers]),MaskConfig())
    actions=[*built.get('prefix_actions',[]),built['action']]
    for index,encoded in enumerate(actions):
        for token in decoder.codec.encode_action(space.decode(encoded)):
            try:decoder.advance(token)
            except MaskError:
                dead=decoder.dead_end
                result.update(status='rejected',action_index=index,denied_token=token,
                    rule_ids=sorted(set(dead.rule_ids)) if dead else [],reason=str(dead))
                return result
    result.update(status='admitted',actions=actions)
    try:
        expanded=space.expand(instance,[space.decode(action) for action in actions])
        result['expanded_plan']=expanded.model_dump(mode='json')
    except Exception as exc:result['expansion_error']=f'{type(exc).__name__}: {exc}'
    return result


def canonical_pin(output):
    """Seal canonical models, never mutable attempt data, before case admission."""
    from stowage.cache_warm import PRODUCTION_SHIPS
    from stowage.vessel.fleet import load_vessel,_parameter_bytes
    from stowage.vessel.fleet_cache import _cache_key
    frozen=module('freeze');transport=module('pin_transport')
    folder=output/'canonical-snapshot';sources={name:str(folder/name) for name in ('vessels','stability','linear')}
    for source in sources.values():Path(source).mkdir(parents=True,exist_ok=True)
    for ship in PRODUCTION_SHIPS:
        # Only baseline ship assembly is needed now; other ships stay pending.
        if ship!=PRODUCTION_SHIPS[0]:continue
        load_vessel(ship)
        filename=f'{ship}.full.hull.{_cache_key(ship,"full",True,_parameter_bytes(ship))}.json'
        original=read_snapshot(ROOT/'experiments/cache/vessels'/filename)
        destination=Path(sources['vessels'])/filename
        if destination.exists():read_snapshot(destination,expected_sha256=original.sha256)
        else:destination.write_bytes(original.data);destination.chmod(0o444)
    files=frozen.numerical_cache_inventory(sources)
    pin=dict(schema_version=1,sources=sources,files=files,sha256=frozen.numerical_cache_digest(sources,files))
    os.environ.update(transport.pin_environment(pin,output))
    return pin


def baseline(output):
    cases=module('qualified_canary_cases')
    path=output/'baseline.json'
    if path.exists():return cases.acquire_baseline(path)[0]
    from stowage.generate.instances_v3 import scenario_specs,generate
    from stowage.cache_warm import PRODUCTION_SHIPS
    spec=next(row for row in scenario_specs(pilot=True,profiles=['small'],generators=['random_yard'],
        c2_screened=False,max_attempts=5) if row['vessel']==PRODUCTION_SHIPS[0])
    spec['port']='small_feeder'
    write(output/'baseline-spec.json',spec)
    with deadline(180):instance,audit=generate(spec)
    write(path,instance.model_dump(mode='json'));write(output/'baseline.audit.json',audit)
    cases.validate_baseline(instance)
    return instance


def variant(output,built,rule,expected_rules,expectation,*,expected_secondary_rules=None,expected_mask_reasons=None):
    from stowage.model import Instance,MovePlan
    from stowage.checker import check,report_matches_inputs
    gate=module('migration_canaries')
    write(output/'input.json',built['input']);write(output/'plan.json',built['plan'])
    instance=read_snapshot(output/'input.json').model(Instance)
    plan=read_snapshot(output/'plan.json').model(MovePlan)
    expected_rules=list(expected_rules) if expectation=='deny' else []
    expected_secondary_rules=list(expected_secondary_rules or []) if expectation=='deny' else []
    expected_mask_reasons=list([rule] if expected_mask_reasons is None else expected_mask_reasons) if expectation=='deny' else []
    result=dict(expectation=expectation,diagnostic=True,evidence_eligible=False,expected_rules=expected_rules,
        expected_secondary_rules=expected_secondary_rules,expected_mask_reasons=expected_mask_reasons)
    start=time.monotonic()
    try:
        with deadline(90):report=check(instance,plan)
        write(output/'checker.json',report.model_dump(mode='json'))
        result['checker']=gate._checker_observation(report,rule,expectation,expected_rules=expected_rules,
            expected_secondary_rules=expected_secondary_rules)
        result['checker']['rule_ids']=sorted({v.rule_id for v in report.violations})
        result['checker']['input_bound']=report_matches_inputs(report,instance,plan)
        result['checker']['parts']={key:dict(ran=p.ran,error=p.error) for key,p in report.parts.items()}
        metrics={key:value for part in report.parts.values() for key,value in part.metrics.items()}
        if report.count is not None:metrics.update(report.count.model_dump(mode='json'))
        result['required_metrics']={key:dict(observed=metrics.get(key),minimum=minimum,
            passed=isinstance(metrics.get(key),(int,float)) and metrics[key]>=minimum)
            for key,minimum in built.get('required_metrics',{}).items()}
    except Exception as exc:result['checker']=dict(error=f'{type(exc).__name__}: {exc}',eligible=False)
    result['checker_seconds']=time.monotonic()-start;start=time.monotonic()
    try:
        with deadline(90):mask=mask_observation(instance,built)
        write(output/'mask.json',mask);result['mask']=mask
    except Exception as exc:result['mask']=dict(status='error',rule_ids=[],reason=f'{type(exc).__name__}: {exc}')
    result['mask_seconds']=time.monotonic()-start
    reasons=set(result['mask'].get('rule_ids',[]))
    matches=(result['mask']['status']=='rejected' and bool(reasons) and reasons<=set(expected_mask_reasons)) if expectation=='deny' else result['mask']['status']=='admitted'
    result['local_behavior_matches']=bool(result['checker'].get('eligible') and result['checker'].get('input_bound')
        and matches and all(value['passed'] for value in result.get('required_metrics',{}).values()))
    result['gate_expectation_passed']=False  # C2-off never satisfies production gate.
    result['gate_missing']=['production_C2_off']
    write(output/'observations.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pilot',action='store_true')
    parser.add_argument('--case',action='append',default=[],help='Run only named cases; preserve other completed reports')
    parser.add_argument('--report-name',default='report.json')
    args=parser.parse_args();output=args.output.resolve()
    if Path(args.report_name).name!=args.report_name or not args.report_name.endswith('.json'):
        raise ValueError('report name must be a JSON basename')
    if not output.is_relative_to(HERE):raise ValueError('diagnostic output must stay within copied campaign-g1')
    import stowage
    if not Path(stowage.__file__).resolve().is_relative_to(ROOT/'src'):
        raise ValueError('diagnostic must import only its copied dev-tree source')
    def owned_write(event,args):
        if event=='open':
            path,mode,flags=args
            writing=(isinstance(mode,str) and any(c in mode for c in 'wax+')) or (
                isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
            if writing and not isinstance(path,int) and not Path(os.fsdecode(path)).resolve().is_relative_to(ROOT):
                raise PermissionError(f'diagnostic write outside owned dev tree: {path}')
    sys.addaudithook(owned_write)
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if not rule_freeze_v1_enabled():raise ValueError('enable only the dev-tree catalogue switch before running')
    output.mkdir(parents=True,exist_ok=True);started=time.monotonic()
    report=dict(diagnostic=True,evidence_eligible=False,switch_on=True,c2='off',cases={})
    try:
        report['canonical_pin']=canonical_pin(output)
        instance=baseline(output)
        report['baseline']=dict(path=str(output/'baseline.json'),sha256=read_snapshot(output/'baseline.json').sha256)
    except Exception as exc:
        instance=None;report['baseline_error']=f'{type(exc).__name__}: {exc}'
    cases=module('qualified_canary_cases')
    if set(args.case)-set(cases.CASES):raise ValueError('unknown diagnostic case')
    for name,spec in cases.CASES.items():
        if args.case and name not in args.case:continue
        if args.pilot and name!='kg_inversion_within_class':continue
        row=dict(built=False,expected_rule=spec['expected_rule'],diagnostic=True,evidence_eligible=False,
            **{field:list(spec[field]) for field in ('expected_rules','expected_secondary_rules','expected_mask_reasons')})
        try:
            pair=cases.build_pair(name,baseline=instance) if instance is not None or name=='inport_list_failure' else dict(status='missing',reason=report.get('baseline_error'))
            row.update(status=pair.get('status'),reason=pair.get('reason'),missing=pair.get('missing',[]))
            if pair.get('status')=='ready':
                row['built']=True;row['variants']={}
                for key in ('baseline','defect'):
                    built=pair[key];expectation='accept' if key=='baseline' else spec['expectation']
                    row['variants'][key]=variant(output/name/key,built,spec['expected_rule'],
                        built.get('expected_rules',pair.get('expected_rules',[spec['expected_rule']])),expectation,
                        expected_secondary_rules=built.get('expected_secondary_rules',[]),
                        expected_mask_reasons=built.get('expected_mask_reasons',[]))
        except Exception as exc:row.update(reason=f'{type(exc).__name__}: {exc}',status='error')
        report['cases'][name]=row;write(output/args.report_name,report)
        print(json.dumps(dict(case=name,built=row['built'],status=row.get('status'),
            reason=row.get('reason'),diagnostic=True,evidence_eligible=False)),flush=True)
    report.update(wall_seconds=time.monotonic()-started,max_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    write(output/args.report_name,report)
    print(json.dumps(dict(report=str(output/args.report_name),built=sum(r['built'] for r in report['cases'].values()),
        cases=len(report['cases']),wall_seconds=report['wall_seconds'],diagnostic=True,evidence_eligible=False)))


if __name__=='__main__':main()

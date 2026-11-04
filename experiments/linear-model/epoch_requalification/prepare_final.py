"""Source-only FINAL preparation; optional fresh-process-validated epoch capture.

Default execution writes no calibration, numerical observation, bank or test.
The leader activates the real catalogue before explicitly passing --capture.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

OWNER=Path(__file__).resolve().parent
REPO=OWNER.parents[2]
SOURCE=OWNER/'production-1028c'
ROOT_SOURCES=('compute.py','compute_worker.py','finish.py','promote_profile.py',
 'verify.py','aliases.py','production_smoke.py','saved_contexts.py','summarize.py','account.py')
FRESH_SOURCES=('profile_run.py','necessary_adapter.py','ordinary_campaign.py',
 'edges_campaign.py','diagnostics_campaign.py','claude-edge-cause.py',
 'claude-face-pilot.py','claude-face-budget-pilot.py','claude_edge_audit.py',
 'freshwater_residual_gate.py','d49_holdout_audit.py','claude_audit_inputs.py','verification_campaign.py')
TEST_NAMES=('test_claude_edge_rows.py','test_d49_holdout_inclusion.py','test_claude_qualification.py')
D64_EXPECTED=dict(passed=True,false_rejections=0,necessary_reserve_breaches=0,
    ships=['feeder_500','feeder_800','feeder_1700'],water_densities_kg_m3=[1000,1025],
    arrival_consumables=True,light_prefixes=True,checker_in_port=True)


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
def replace_once(path,before,after):
    text=path.read_text()
    if text.count(before)!=1:raise ValueError('reviewed source marker changed: '+str(path))
    path.write_text(text.replace(before,after))


def migrate_test_source(name,text,relative):
    """Transform current evidence paths/adapters; preserve every assertion AST."""
    before=text
    old='experiments/linear-model/epoch_requalification/production-1028b'
    if name=='test_claude_edge_rows.py':
        changes=[(old,relative+'/seawater'),
            ('    path = HERE/"claude_edge_audit.py"','    path = HERE.parent/"verify.py"'),
            ('    spec.loader.exec_module(module)\n    model = NS(',
             '    spec.loader.exec_module(module)\n    module = module.load_gate("seawater", "rows")\n    model = NS(')]
    elif name=='test_d49_holdout_inclusion.py':changes=[(old,relative+'/seawater')]
    elif name=='test_claude_qualification.py':
        changes=[("    path=ROOT/'experiments/linear-model/fix-t3-linear/claude-prior-holdout-gate.py'",
                  "    path=ROOT/'"+relative+"/verify.py'"),
                 ('    return module\n\n\n@pytest.mark.parametrize(\'name,admitted,rejected\'',
                  "    return module.load_gate('seawater', 'residual')\n\n\n@pytest.mark.parametrize('name,admitted,rejected'"),
                 (old,relative+'/seawater')]
    else:raise ValueError('unreviewed test migration: '+name)
    changes.append(("EVIDENCE_HASH_SCHEME = '1'","EVIDENCE_HASH_SCHEME = '2'"))
    for source,target in changes:
        expected=2 if name=='test_claude_qualification.py' and source==old else 1
        if text.count(source)!=expected:raise ValueError('current test marker changed: '+name+' '+source)
        text=text.replace(source,target)
    def assertions(value):
        return [ast.dump(node,include_attributes=False) for node in ast.walk(ast.parse(value))
                if isinstance(node,ast.Assert)]
    if assertions(before)!=assertions(text):raise ValueError('test assertion changed: '+name)
    compile(text,name,'exec')
    return text


def d64_producer_paths(configuration):
    """Resolve L4's real Python entry point and any declared helper/input pins."""
    arguments=configuration['arguments']
    if arguments[0]=='-m':
        if len(arguments)<2:raise ValueError('D64 module name missing')
        spec=importlib.util.find_spec(arguments[1])
        if spec is not None and spec.submodule_search_locations is not None:
            spec=importlib.util.find_spec(arguments[1]+'.__main__')
        if spec is None or spec.origin is None:raise ValueError('D64 module source missing')
        entry=Path(spec.origin)
    elif arguments[0].startswith('-'):raise ValueError('D64 requires a Python script or -m module')
    else:entry=Path(arguments[0])
    extra=configuration.get('producer_paths',[])
    if not isinstance(extra,list) or not all(isinstance(value,str) and value for value in extra):
        raise ValueError('D64 producer_paths must list repository files')
    paths=[]
    for value in [entry,*extra]:
        path=Path(value);path=(path if path.is_absolute() else REPO/path).resolve()
        if not path.is_relative_to(REPO) or not path.is_file():
            raise ValueError('D64 producer source must be an existing repository file: '+str(path))
        paths.append(path)
    return sorted(set(paths))


MIGRATE_SOURCE=r'''"""Migrate unchanged evidence assertions only after fresh FINAL gates/replays."""
import hashlib,json
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[3]
sys.path.insert(0,str(HERE/'closure'))
from guard import assert_current

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    assert_current()
    for profile in ('seawater','freshwater'):
        for relative in ('candidate-residual-gate.json','candidate-row-audit.json','holdout-audit/summary.json'):
            path=HERE/profile/relative
            if not json.loads(path.read_text()).get('passed'):raise ValueError('fresh evidence gate incomplete: '+str(path))
        if not (HERE/profile/'claude-current-audit-inputs.json').exists():raise ValueError('fresh manifest missing')
    manifest=json.loads((HERE/'migration-plan.json').read_text())
    outputs=[]
    for item in manifest:
        target=REPO/item['target'];template=HERE/item['template']
        if sha(template)!=item['after_sha256']:raise ValueError('migration template changed')
        current=sha(target)
        if current not in (item['before_sha256'],item['after_sha256']):
            raise ValueError('test changed since preparation; preserve and review: '+str(target))
        if current!=item['after_sha256']:
            backup=HERE/'migration-backup'/target.name;backup.parent.mkdir(exist_ok=True)
            with backup.open('xb') as stream:stream.write(target.read_bytes())
            temporary=target.with_suffix('.final-migration.tmp')
            temporary.write_bytes(template.read_bytes());temporary.replace(target)
        outputs.append(dict(path=item['target'],sha256=sha(target)))
    assert_current()
    (HERE/'test-migration-ready.json').write_text(json.dumps(dict(passed=True,tests=outputs),indent=2)+'\n')
if __name__=='__main__':main()
'''

D64_SOURCE='''def d64_inport_acceptance():
    configuration=read(ROOT/'d64-inport-command.json')
    arguments=configuration['arguments']
    if not isinstance(arguments,list) or not arguments or not all(isinstance(x,str) and x for x in arguments):
        raise ValueError('L4 must configure actual D64 acceptance argv before final capture')
    run('D64-inport-acceptance',arguments)
    report=read(ROOT/'d64-inport-acceptance.json')
    for name,expected in configuration['expected_report'].items():
        actual=report.get(name)
        if isinstance(expected,list):
            if not isinstance(actual,list) or sorted(actual)!=sorted(expected):
                raise ValueError('D64 acceptance coverage mismatch: '+name)
        elif actual!=expected:raise ValueError('D64 acceptance failed: '+name)


'''


def prepare(output,workers):
    if output.exists():raise ValueError('fresh output required; never copy/reuse old evidence')
    output.mkdir()
    for name in ('closure','seawater','freshwater'):(output/name).mkdir()
    for name in ROOT_SOURCES:shutil.copy2(SOURCE/name,output/name)
    shutil.copy2(OWNER/'freeze.py',output/'closure/guard.py')
    shutil.copy2(SOURCE/'seawater/campaign.py',output/'seawater/campaign.py')
    for name in FRESH_SOURCES:shutil.copy2(SOURCE/'freshwater'/name,output/'freshwater'/name)
    replace_once(output/'finish.py',"label='pre-final'","label='final'")
    replace_once(output/'summarize.py','# Pre-final requalification:','# Final requalification:')
    # Pure path generalization: compare to NEW seawater observations, not b.
    replace_once(output/'freshwater/diagnostics_campaign.py',
        'BASELINE=OWNER/"production-1028b"','BASELINE=RUN.parent/"seawater"')
    # Offline checker budget is an existing resource procedure, never a margin.
    replace_once(output/'compute.py','        if env_extra: env.update(env_extra)',
        '        env["STOWAGE_STABILITY_TIME_BUDGET_S"]="120"\n        if env_extra: env.update(env_extra)')
    replace_once(output/'compute.py',"            record['pid']=process.pid;path.write_text",
        "            record['offline_public_time_budget_s']=env['STOWAGE_STABILITY_TIME_BUDGET_S']\n            record['pid']=process.pid;path.write_text")
    # Freshwater comparisons must wait for the corresponding new sea captures.
    replace_once(output/'freshwater/diagnostics_campaign.py',
        '    fresh_path,historical_path=RUN/relative,BASELINE/relative\n    fresh,historical=',
        '    fresh_path,historical_path=RUN/relative,BASELINE/relative\n'
        '    while not historical_path.exists():\n'
        '        from ordinary_campaign import abort_check\n'
        '        abort_check(); time.sleep(2)\n    fresh,historical=')
    # Do not touch live tests now. Generate source-only templates for a later,
    # guarded, idempotent migration after both candidate gates AND exact replays.
    relative=str(output.relative_to(REPO))
    plan=[]
    for name in TEST_NAMES:
        target=REPO/'tests/linear'/name
        text=migrate_test_source(name,target.read_text(),relative)
        template=output/'migration-templates'/name;template.parent.mkdir(exist_ok=True)
        template.write_text(text)
        plan.append(dict(target=str(target.relative_to(REPO)),template=str(template.relative_to(output)),
            before_sha256=sha(target),after_sha256=sha(template),source_template=str(target.relative_to(REPO)),
            assertions_preserved=True))
    write(output/'migration-plan.json',plan)
    (output/'migrate_evidence_tests.py').write_text(MIGRATE_SOURCE)
    replace_once(output/'finish.py',"    run('linear-feasibility-tests',",
        "    run('migrate-evidence-tests',[ROOT/'migrate_evidence_tests.py'])\n    run('linear-feasibility-tests',")
    replace_once(output/'finish.py','def publics():',D64_SOURCE+'def publics():')
    replace_once(output/'finish.py','    def replays():','    d64_inport_acceptance()\n    def replays():')
    configuration=output/'d64-inport-command.json'
    write(configuration,dict(arguments=None,report='d64-inport-acceptance.json',
        owner='L4; fill exact Python argv before --capture; no shell command',
        producer_paths=[],expected_report=D64_EXPECTED))
    # Reuse only immutable saved physical INPUT pins, not prior response data.
    cases=[]
    for item in json.loads((SOURCE/'production-audit.json').read_text())['cases']:
        value=item['saved_input']
        if sha(REPO/value['path'])!=value['sha256']:raise ValueError('saved physical input changed')
        cases.append(dict(id=item['id'],saved_input=value))
    write(output/'production-audit.json',dict(format=1,cases=cases,
        scope='immutable saved physical input pins only; all response contexts freshly validated'))
    write(output/'compute-active.json',[]);write(output/'compute-capacity.json',dict(workers=workers))
    (output/'compute.lock').touch()
    write(output/'compute-estimate.json',dict(cores=workers,ram_gb=24,gpu=False,
        fresh_pilot_and_ledger_admission_required=True))
    originals=[REPO/'experiments/linear-model/fix-t3-linear/claude-edge-seed51/state-011.json',
        REPO/'experiments/linear-model/fix-t3-linear/claude-review/feeder_500/frozen-instance.json']
    # The old records below supply immutable population descriptors only
    # (phase/level/samples/seed), never response observations. Pin their bytes.
    descriptors=[path for ship in ('feeder_500','feeder_1000','feeder_1700')
        for path in (OWNER/'production'/ship).glob('*.json')
        if path.name.startswith(('train-','holdout-','enrichment'))]
    originals.extend(REPO/item['saved_input']['path'] for item in cases)
    paths=sorted(set(output.rglob('*.py'))|set(originals)|set(descriptors)|{configuration})
    write(output/'producer-paths.json',[str(path) for path in paths])
    for path in output.rglob('*.py'):compile(path.read_text(),str(path),'exec')
    write(output/'preparation.json',dict(format=1,source_only=True,output=str(output.relative_to(REPO)),
        preparer=dict(path=str(Path(__file__).relative_to(REPO)),sha256=sha(__file__)),
        profiles=dict(seawater=1025.,freshwater=1000.),ships=['feeder_500','feeder_1000','feeder_1700'],
        sources={str(path.relative_to(REPO)):sha(path) for path in paths if path!=configuration},
        original_numerical_evidence_copied=False,offline_public_time_budget_s=120,
        tests_modified=False,capture_required=True))


def capture(output):
    if not (output/'preparation.json').exists():raise ValueError('prepare this fresh root first')
    if (output/'closure/start.json').exists():raise ValueError('never recapture an epoch baseline')
    expected=json.loads((output/'preparation.json').read_text())['sources']
    for name,value in expected.items():
        if sha(REPO/name)!=value:raise ValueError('prepared source changed; choose a fresh root: '+name)
    if sha(output/'closure/guard.py')!=sha(OWNER/'freeze.py'):
        raise ValueError('canonical guard changed after preparation; choose a fresh root')
    d64=json.loads((output/'d64-inport-command.json').read_text())
    if (not isinstance(d64.get('arguments'),list) or not d64['arguments']
        or not all(isinstance(value,str) and value for value in d64['arguments'])
        or d64.get('expected_report')!=D64_EXPECTED or d64.get('report')!='d64-inport-acceptance.json'):
        raise ValueError('L4 must fill the D64 acceptance argv/report contract before capture')
    d64_paths=d64_producer_paths(d64)
    files=[REPO/'src/stowage/rules/catalogue.py',REPO/'src/stowage/rules/catalogue.toml']
    before={str(path.relative_to(REPO)):sha(path) for path in files}
    # Fresh process: never accept old imported scheduler/catalogue classes.
    code='''import json\nfrom stowage.rules.catalogue import catalogue_snapshot,rule_freeze_v1_enabled\ns=catalogue_snapshot()\nassert rule_freeze_v1_enabled(catalogue=s.catalogue), "leader must activate persistent rule-freeze-v1 first"\nprint(json.dumps({"valid":True,"activated":True}))\n'''
    result=subprocess.run([sys.executable,'-c',code],cwd=REPO,text=True,capture_output=True)
    if result.returncode:raise ValueError('fresh catalogue/schema validation failed: '+result.stderr+result.stdout)
    after={str(path.relative_to(REPO)):sha(path) for path in files}
    if before!=after:raise ValueError('catalogue/schema changed during fresh-process validation')
    proof=output/'closure/catalogue-validation.json'
    write(proof,dict(validated_in_fresh_process=True,activation=True,sources=after,result=json.loads(result.stdout)))
    spec=importlib.util.spec_from_file_location('prepared_final_guard',output/'closure/guard.py')
    guard=importlib.util.module_from_spec(spec);sys.modules[spec.name]=guard;spec.loader.exec_module(guard)
    guard.capture()
    paths=json.loads((output/'producer-paths.json').read_text())+[str(proof),str(Path(__file__))]+d64_paths
    guard.freeze_producers(paths)
    if not guard.check()['passed']:raise ValueError('post-capture source check failed; do not recapture')
    if after!={str(path.relative_to(REPO)):sha(path) for path in files}:
        write(output/'ABORT',dict(reason='catalogue/schema changed during validated capture',
            before=after,after={str(path.relative_to(REPO)):sha(path) for path in files}))
        raise ValueError('catalogue/schema changed during capture; stop without recapture')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OWNER/'production-final')
    parser.add_argument('--workers',type=int,choices=range(1,10),default=9)
    parser.add_argument('--capture',action='store_true',help='leader-only after real activation; no numerical campaign')
    args=parser.parse_args();output=args.output.resolve()
    if output.parent!=OWNER or output==SOURCE:parser.error('fresh direct child of epoch_requalification required')
    if not output.exists():prepare(output,args.workers)
    elif not args.capture:parser.error('output exists; never reuse an old run')
    if args.capture:capture(output)
    print(json.dumps(dict(prepared=str(output),captured=args.capture,numerical_jobs_started=0,tests_modified=False)))


if __name__=='__main__':main()

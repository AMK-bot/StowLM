"""Shared rule-freeze and G1 approval checks for evaluation and gold admission."""
from __future__ import annotations

import ast
import math
from pathlib import Path
import tomllib

from eval.snapshot_io import read_snapshot

HERE = Path(__file__).resolve().parent
REQUIRED_RULES = HERE / 'REQUIRED_RULES.json'


def _version(value):
    if not isinstance(value, str) or not value or any(not p.isdigit() for p in value.split('.')):
        raise ValueError(f'malformed schema version: {value!r}')
    return tuple(int(part) for part in value.split('.'))


def enforce_rule_policy(root, policy_pin=None):
    """Validate the current leader policy and optionally its immutable freeze pin.

    The source catalogue and schema version are parsed from exact byte snapshots;
    this identity check makes no migration claim. Production freeze additionally
    requires migration_gate, and every G1 approval requires its post-freeze
    evidence. A pipeline-test pin cannot bypass either admission gate.
    """
    root = Path(root).resolve()
    policy_snapshot = read_snapshot(REQUIRED_RULES)
    policy = policy_snapshot.json()
    ids = policy.get('required_rule_ids')
    if (policy.get('schema_version') != 1 or not isinstance(ids, list) or not ids
            or len(ids) != len(set(ids)) or not all(isinstance(i, str) and i for i in ids)):
        raise ValueError('required rule policy must contain distinct nonempty rule IDs')
    minimum = _version(policy.get('minimum_schema_version'))
    catalogue = read_snapshot(root/'src/stowage/rules/catalogue.toml')
    values = tomllib.loads(catalogue.data.decode('utf-8'))
    present = {rule['id'] for rule in values.get('rules', [])}
    missing = set(ids)-present
    if missing:
        raise ValueError('required rule IDs absent from catalogue: ' + ', '.join(sorted(missing)))
    versions = read_snapshot(root/'src/stowage/versions.py')
    schema = None
    for node in ast.walk(ast.parse(versions.data.decode('utf-8'))):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id=='SCHEMA_VERSION' for target in targets):
            schema = ast.literal_eval(node.value)
    if _version(schema) < minimum:
        raise ValueError(f'schema version {schema} is below required minimum {policy["minimum_schema_version"]}')
    pin = dict(path=str(REQUIRED_RULES.resolve()), sha256=policy_snapshot.sha256, policy=policy,
        catalogue_sha256=catalogue.sha256, versions_sha256=versions.sha256, schema_version=schema)
    if policy_pin is not None and {k:v for k,v in policy_pin.items() if k!='path'} != {k:v for k,v in pin.items() if k!='path'}:
        raise ValueError('required rule policy/source differs from frozen rule epoch; create a new freeze')
    return pin


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f'{label} must be a filled finite number in [0,1]')
    return float(value)


def _approval_yields(summary, epoch, rungs):
    """Require a same-epoch G1 approval and its explicit per-rung solve yield."""
    def eligible(value):
        if isinstance(value, dict):
            if (value.get('diagnostic') or value.get('allow_drift') or value.get('tiny_run')
                    or value.get('evidence_eligible') is False or value.get('official') is False):
                raise ValueError('G1 approval has diagnostic/ineligible lineage')
            for child in value.values():
                eligible(child)
        elif isinstance(value, list):
            for child in value:
                eligible(child)
    eligible(summary)
    if summary.get('g1_met') is not True or summary.get('epoch_id') != epoch:
        raise ValueError('G1 approval must be passed and match the frozen epoch')
    rungs = tuple(dict.fromkeys(rungs))
    if not rungs or any(rung not in ('tiny','small','medium','full') for rung in rungs):
        raise ValueError('G1 approval requires explicit nonempty valid evaluated rungs')
    threshold = summary.get('min_yield')
    thresholds = {rung: _number(threshold.get(rung), f'min_yield.{rung}') if isinstance(threshold, dict)
                  else _number(threshold, 'min_yield') for rung in rungs}
    yields = summary.get('solve_yield')
    if not isinstance(yields, dict):
        raise ValueError('G1 approval requires solve_yield per rung')
    observed = {rung: _number(yields.get(rung), f'solve_yield.{rung}') for rung in rungs}
    for rung in rungs:
        if observed[rung] < thresholds[rung]:
            raise ValueError(f'G1 solve yield below approved min_yield for {rung}')
    return {**summary, 'min_yield': thresholds, 'solve_yield': observed}


def validate_g1_approval(summary,epoch,rungs):
    _approval_yields(summary,epoch,rungs)
    validate_migration_approval(summary,epoch)
    # Gold and SFT carry this same signed body. Normalizing 1 to 1.0 or a
    # scalar threshold to a mapping would invalidate its byte-derived identity.
    return dict(summary)


def _module(name):
    """Load experiments-only gate code without depending on the caller's sys.path."""
    import importlib.util
    import sys
    spec=importlib.util.spec_from_file_location('_g1_policy_'+name,HERE/(name+'.py'))
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module


def migration_gate(root, *, canary_report_path=None, required=True):
    """Aggregate the source-freeze obligations; future inventories stay pending.

    Diagnostic callers may inspect an incomplete report. Production callers must
    require it; mere catalogue membership is deliberately not an admission gate.
    """
    root=Path(root).resolve()
    identity=enforce_rule_policy(root)
    contract=_module('migration_contract').static_contract_report(root,identity['policy']['required_rule_ids'])
    canaries=_module('migration_canaries').canary_report(root,report_path=canary_report_path)
    evidence=_module('migration_evidence')
    issues=list(contract['issues'])
    issues.extend(dict(code='canary.'+row['case'],detail=','.join(row['missing'])) for row in canaries['issues'])
    components={}
    try:
        components=evidence.current_component_epoch()
        if components.get('catalogue_loaded_sha256')!=contract.get('catalogue_loaded_sha256'):
            issues.append(dict(code='M5_LOADED_CATALOGUE_MISMATCH',detail='worker loaded catalogue differs from the source snapshot'))
    except Exception as exc:
        issues.append(dict(code='M5_COMPONENTS_UNAVAILABLE',detail=f'{type(exc).__name__}: {exc}'))
    implementations={str((HERE/name).resolve()):read_snapshot(HERE/name).sha256 for name in
        ('rule_policy.py','freeze.py','migration_contract.py','migration_canaries.py','migration_evidence.py','trusted_receipts.py','qualified_canary_cases.py','admission-trust.json')}
    report=dict(schema_version=1,phase='source_freeze',passed=not issues,
        issues=sorted(issues,key=lambda row:(row['code'],row['detail'])),contract=contract,
        canaries=canaries,component_epoch=components,implementation_pins=implementations,
        post_freeze=evidence.evidence_report({'component_epoch':components},required=False))
    if required and issues:
        raise ValueError('rule migration gate not ready:\n'+'\n'.join(row['code']+': '+row['detail'] for row in report['issues']))
    return report


def _produce_live_migration_report(summary, epoch):
    """Recompute post-freeze admission; never accept a leader's pass boolean alone."""
    declaration=summary.get('migration_evidence')
    if not isinstance(declaration,dict) or set(declaration)!={'path','sha256'}:
        raise ValueError('G1 approval requires pinned migration evidence {path,sha256}')
    journal_snapshot=read_snapshot(declaration['path'],expected_sha256=declaration['sha256'])
    journal=journal_snapshot.json()
    if journal.get('epoch_id')!=epoch or journal.get('diagnostic') or journal.get('evidence_eligible') is not True:
        raise ValueError('migration evidence must be eligible and match the approved epoch')
    def pin(key):
        item=journal.get(key)
        if not isinstance(item,dict) or set(item)!={'path','sha256'}:
            raise ValueError('migration evidence missing immutable '+key+' binding')
        snapshot=read_snapshot(item['path'],expected_sha256=item['sha256'])
        return snapshot
    frozen_snapshot=pin('freeze_manifest')
    frozen=frozen_snapshot.json()
    if frozen.get('epoch_id')!=epoch or frozen.get('diagnostic') or frozen.get('evidence_eligible') is not True:
        raise ValueError('migration evidence freeze is diagnostic or from another epoch')
    # check_manifest validates source, caches, migration proofs and implementation
    # pins. Bind the checked read back to exactly the declaration admitted here.
    checked=_module('freeze').check_manifest(frozen_snapshot.path)
    if checked['_manifest_sha256']!=frozen_snapshot.sha256:
        raise ValueError('migration evidence freeze changed during admission')
    evidence=_module('migration_evidence')
    evidence.assert_component_epoch(frozen)
    inventories={key:pin(key) for key in ('instance_manifest','campaign_manifest')}
    report=evidence.evidence_report(frozen,**{key:value.path for key,value in inventories.items()},summary=summary)
    # Evidence readers use their own single snapshots. Ensure every byte inventory
    # they validated is the exact declared version, even under replacement races.
    for snapshot in inventories.values():
        if report['bindings'].get(str(snapshot.path.resolve()))!=snapshot.sha256:
            raise ValueError('migration evidence inventory changed during admission')
    if not report['complete']:
        raise ValueError('migration evidence incomplete:\n'+'\n'.join(row['code']+': '+row['detail'] for row in report['issues']))
    if not set(summary['solve_yield'])<=set(summary.get('d62_crossing',{})):
        raise ValueError('migration evidence requires D62 coverage for every evaluated rung')
    final=_module('freeze').check_manifest(frozen_snapshot.path)
    if final['_manifest_sha256']!=frozen_snapshot.sha256:
        raise ValueError('migration evidence freeze changed during final admission check')
    for snapshot in inventories.values():snapshot.require_fresh()
    frozen_snapshot.require_fresh();journal_snapshot.require_fresh()
    return report


def enforce_instance_weight_policy(instance,frozen):
    """Resolve the checker's actual overrides, then require the frozen D61 policy.

    E2 is authoritative for both equivalent weight-rule IDs. The frozen loaded
    catalogue controls the tolerance (currently exactly 0 kg), never a generic
    instance override. Generic checker parameterization remains available outside
    this campaign.
    """
    from stowage.rules.catalogue import catalogue_snapshot
    from stowage.checker.rules import _parameter
    snapshot=catalogue_snapshot()
    expected=(frozen.get('migration') or {}).get('component_epoch') or frozen.get('component_epoch') or {}
    if expected.get('catalogue_loaded_sha256')!=snapshot.content_sha256:
        raise ValueError('resolved weight policy catalogue differs from frozen loaded object')
    params=snapshot.rules['expert.e2.weight_order'].parameters
    mode=params.get('weight_order_mode');tolerance=params.get('weight_order_tolerance_kg')
    mode=mode.value if mode is not None else None
    tolerance=tolerance.value if tolerance is not None else None
    if mode!='gross_mass_kg' or isinstance(tolerance,bool) or not isinstance(tolerance,(int,float)) or not math.isfinite(tolerance) or tolerance<0:
        raise ValueError('resolved frozen weight policy is not a valid gross-mass contract')
    for rule in ('expert.e1.heavy_bottom','expert.e2.weight_order'):
        actual_mode=_parameter(instance,rule,'weight_order_mode')
        actual_tolerance=_parameter(instance,rule,'weight_order_tolerance_kg')
        if actual_mode!=mode or isinstance(actual_tolerance,bool) or not isinstance(actual_tolerance,(int,float)) or not math.isfinite(actual_tolerance) or actual_tolerance!=tolerance:
            raise ValueError(f'resolved instance weight policy differs from frozen catalogue: {rule}')
    e1=instance.rule_overrides.get('expert.e1.heavy_bottom',{})
    e2=instance.rule_overrides.get('expert.e2.weight_order',{})
    if 'weight_order_tolerance_kg' in e1 and 'weight_order_tolerance_kg' not in e2:
        raise ValueError('resolved instance weight policy: E1 override requires equal explicit E2 override')
    return dict(weight_order_mode=mode,weight_order_tolerance_kg=tolerance,catalogue_loaded_sha256=snapshot.content_sha256)


def _approval_basis(summary):
    import hashlib,json
    return hashlib.sha256(json.dumps({key:value for key,value in summary.items() if key!='migration_admission'},
        sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def validate_migration_approval(summary,epoch):
    """Verify a gate-produced admission, including on a different filesystem.

    The local producer executes every plan and seals the historical source and
    cache epoch. Consumers verify that receipt and their loaded components; they
    never infer execution from caller-authored checker JSON or need old caches.
    """
    declaration=summary.get('migration_admission')
    if not isinstance(declaration,dict):
        raise ValueError('migration evidence requires a gate-signed admission; run rule_policy.py --admit-summary')
    try:
        payload=_module('trusted_receipts').verify_execution(declaration,'migration-admission-v2',
            {'epoch_id':epoch,'diagnostic':False,'evidence_eligible':True},verify_artifacts=False)
    except Exception as exc:
        raise ValueError('migration evidence execution receipt is not authentic: '+str(exc)) from exc
    if payload.get('complete') is not True or payload.get('epoch_id')!=epoch or payload.get('approval_body_sha256')!=_approval_basis(summary):
        raise ValueError('migration evidence admission differs from this approved summary')
    evidence=_module('migration_evidence')
    evidence.assert_component_epoch({'component_epoch':payload.get('component_epoch')})
    if not isinstance(payload.get('required_rules'),dict):raise ValueError('migration admission lacks frozen required-rule policy')
    enforce_rule_policy(HERE.parents[1],payload['required_rules'])
    if payload.get('admission_implementation')!=_admission_implementation():
        raise ValueError('migration admission was produced by a different gate implementation')
    counts=payload.get('counts',{})
    if (payload.get('d62_crossing')!=summary.get('d62_crossing') or not counts.get('live_rechecked_solver_plans')
            or counts.get('live_rechecked_solver_plans')!=counts.get('returned_solver_plans')):
        raise ValueError('migration evidence admission lacks complete nonempty solver-plan replay/counts')
    return payload


def _admission_implementation():
    return {name:read_snapshot(HERE/name).sha256 for name in
        ('rule_policy.py','freeze.py','migration_evidence.py','trusted_receipts.py','campaign.py',
         'migration_contract.py','migration_canaries.py','qualified_canary_cases.py',
         'pin_transport.py','cache_adapter.py')}


def produce_migration_admission(summary,epoch,output):
    """Local producer: fresh execution, before/after freeze checks, then signing."""
    from eval.snapshot_io import read_snapshot
    output=Path(output).resolve()
    if not output.is_relative_to(HERE):raise ValueError('admission output must be within campaign-g1')
    if output.exists():raise ValueError('choose a new admission directory; never reuse prior outputs')
    _approval_yields(summary,epoch,list(summary.get('solve_yield',{})))
    output.mkdir(parents=True)
    receipts=_module('trusted_receipts');receipts.initialize_signer()
    declaration=read_snapshot(summary['migration_evidence']['path'],expected_sha256=summary['migration_evidence']['sha256']).json()
    freeze_pin=declaration['freeze_manifest']
    frozen=_module('freeze').check_manifest(freeze_pin['path'])
    if frozen['_manifest_sha256']!=freeze_pin['sha256']:raise ValueError('admission selected freeze checksum differs')
    # This process is an isolated CLI producer. Generated numerical additions
    # remain private and are never written into the frozen shared snapshot.
    import os
    transport=_module('pin_transport')
    os.environ.update(transport.pin_environment(frozen['numerical_caches'],output,dict(os.environ)))
    adapter=_module('cache_adapter');adapter.redirect_cache(output/'cache-work');adapter.enforce_scope()
    report=_produce_live_migration_report(summary,epoch)
    report_path=output/'execution-report.json';_module('freeze').atomic_json(report_path,report)
    final=_module('freeze').check_manifest(freeze_pin['path'])
    if final['_manifest_sha256']!=freeze_pin['sha256']:raise ValueError('admission freeze changed before signing')
    payload=dict(complete=True,epoch_id=epoch,approval_body_sha256=_approval_basis(summary),
        source_sha256=frozen['source_sha256'],rule_epoch_sha256=frozen['rule_epoch_sha256'],
        component_epoch=frozen['component_epoch'],required_rules=frozen['required_rules'],
        counts=report['counts'],d62_crossing=report['d62_crossing'],admission_implementation=_admission_implementation())
    pins={str(report_path):read_snapshot(report_path).sha256,freeze_pin['path']:freeze_pin['sha256'],
        summary['migration_evidence']['path']:summary['migration_evidence']['sha256']}
    for name in ('instance_manifest','campaign_manifest'):
        item=declaration[name];pins[item['path']]=item['sha256']
    receipt=receipts.sign_execution('migration-admission-v2',payload,pins,output/'admission.json',
        context=dict(epoch_id=epoch,source_sha256=frozen['source_sha256'],component_epoch=frozen['component_epoch'],
                     numerical_cache_sha256=frozen['numerical_caches']['sha256'],diagnostic=False,evidence_eligible=True))
    approved={**summary,'migration_admission':receipts.portable_execution(receipt)}
    validate_migration_approval(approved,epoch)
    _module('freeze').atomic_json(output/'approved-summary.json',approved)
    return approved


def main(argv=None):
    import argparse,json,sys
    parser=argparse.ArgumentParser(description='Execute and sign local G1 plan admission before uploading gold/SFT lineage')
    parser.add_argument('--admit-summary',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        snapshot=read_snapshot(args.admit_summary);summary=snapshot.json()
        result=produce_migration_admission(summary,summary['epoch_id'],args.output)
        snapshot.require_fresh()
        print(json.dumps(dict(epoch_id=result['epoch_id'],approved_summary=str(args.output/'approved-summary.json'))))
        return 0
    except Exception as exc:
        print(str(exc),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())

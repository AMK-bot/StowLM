"""D110 pre-registered small-map audit and durable terminal GPU handoff."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

from llm.exit.metrics import compare_validation

ORIGINAL_RULE = dict(name='D110-small30',minimum_pass_gain=2,minimum_relative_move_gain=.05,
            comparison='generation-0 8.1 baseline',failure_action='keep the 8.1 adapter')
RULE = {**ORIGINAL_RULE, 'extension':'D128', 'maximum_relative_move_regression':.05,
        'move_comparison':'paired means on the maps both adapters pass',
        'evidence_label':'weak evidence; chosen adapter quality requires 9.1 test evaluation'}


def audit_decision(baseline,rows):
    comparison=compare_validation(baseline,rows)
    delta=comparison['child_pass_count']-comparison['parent_pass_count']
    pm,cm=comparison['parent_mutual_mean_moves'],comparison['child_mutual_mean_moves']
    move_gain=(pm-cm)/pm if pm is not None and pm>0 and cm is not None else None
    available = not comparison.get('unavailable_ids')
    primary_rule_passed=(delta>=2 or (delta==0 and move_gain is not None and move_gain>=.05))
    # D128: new passes cannot buy arbitrarily inefficient cargo sequences.
    # Compare the same paired maps, not the global 34.05 baseline mean.
    mutual_moves_within_limit=(pm is not None and cm is not None
        and cm<=pm*(1+RULE['maximum_relative_move_regression']))
    passed=available and primary_rule_passed and mutual_moves_within_limit
    reason=('verification_unavailable' if not available else
            'no_mutual_passes' if pm is None or cm is None else
            'mutual_moves_regressed' if not mutual_moves_within_limit else comparison['reason'])
    return {**comparison,'accepted':passed,'pass_gain':delta,'relative_move_gain':move_gain,
            'primary_rule_passed':primary_rule_passed,
            'mutual_moves_within_limit':mutual_moves_within_limit,
            'maximum_relative_move_regression':RULE['maximum_relative_move_regression'],
            'paired_baseline_mean_moves':pm,'paired_child_mean_moves':cm,
            'rule':RULE,'action':'continue' if passed else RULE['failure_action'],
            'unavailable':not available,
            'reason':reason}


def frozen_audit_paths(root,corpus,list_path,out):
    root,corpus,list_path,out=map(Path,(root,corpus,list_path,out))
    paths=[(root/line.strip()).resolve() for line in list_path.read_text().splitlines()
           if line.strip() and not line.lstrip().startswith('#')]
    if len(paths)!=30 or len(set(paths))!=30 or any(
            not p.is_relative_to(corpus.resolve()/'val'/'small') for p in paths):
        raise ValueError('D110 audit requires exactly 30 unique corpus/val/small maps')
    from llm.exit.identity import identity_guard
    from stowage.io import load_instance
    guard=identity_guard(corpus)
    for path in paths:guard.require_allowed(load_instance(path),role='audit',input_path=path)
    specification=dict(rule=RULE,list_sha256=hashlib.sha256(list_path.read_bytes()).hexdigest(),
        maps=[dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths])
    target=out/'audit-rule.json'
    from llm.exit.runner import write_json
    if target.exists():
        original=json.loads(target.read_text())
        old_specification={**specification,'rule':ORIGINAL_RULE}
        if original not in (specification,old_specification):
            raise RuntimeError('pre-registered audit rule or frozen maps changed')
        if original==old_specification:
            # Explicit D128 extension preserves the original pre-registration
            # byte-for-byte; it never rewrites the measured baseline or maps.
            extension=dict(decision='D128',original_rule=ORIGINAL_RULE,extended_rule=RULE,
                original_registration_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                list_sha256=specification['list_sha256'],maps=specification['maps'])
            extension_path=out/'audit-rule-D128-extension.json'
            if extension_path.exists() and json.loads(extension_path.read_text())!=extension:
                raise RuntimeError('D128 audit extension or original registration changed')
            write_json(extension_path,extension)
    else:
        write_json(target,specification)  # Always before a new baseline GPU decode.
    return paths


def clock_due(deadline,out):
    return (deadline is not None and not (Path(out)/'first-gate.json').exists()
            and datetime.now(timezone.utc)>=datetime.fromisoformat(deadline))


class ClockAuditDue(RuntimeError):
    pass


def check_clock(args):
    if clock_due(getattr(args,'audit_clock',None),args.out):
        raise ClockAuditDue('05:30 clock audit due at cooperative phase boundary')


def _original_fallback_adapter(out,provided=None):
    """Resolve the original SFT parent, never the child whose audit failed."""
    registered=None
    for path in (Path(out)/'audit-rule.json',Path(out)/'audit-baseline'/'baseline-binding.json'):
        registration=json.loads(path.read_text()) if path.exists() else {}
        value=registration.get('fallback_adapter') or registration.get('baseline_adapter')
        if value is not None:
            if registered is not None and Path(value).resolve()!=Path(registered).resolve():
                raise RuntimeError('original 8.1 fallback registrations disagree')
            registered=value
    if provided is not None and registered is not None:
        if Path(provided).resolve()!=Path(registered).resolve():
            raise RuntimeError('original 8.1 fallback adapter differs from registered baseline')
    adapter=registered or provided
    if adapter is None or not str(adapter).strip():
        raise ValueError('original 8.1 fallback adapter is required; failed child cannot supply it')
    return str(Path(adapter).resolve())


def persist_stop(out,*,generation,adapter,audit,reason='no_detectable_audit_gain',
                 fallback_adapter=None):
    from llm.exit.runner import write_json
    fallback=_original_fallback_adapter(out,fallback_adapter)
    if audit.get('unavailable') is True and reason=='no_detectable_audit_gain':
        reason='audit_verification_unavailable'
    terminal=dict(stopped=True,stop_reason=reason,generation=generation,adapter=fallback,
        evaluated_adapter=str(adapter),
        audit=audit,fallback='keep_sft81',
        fallback_adapter=fallback,
        action=RULE['failure_action'],
        utc=datetime.now(timezone.utc).isoformat())
    # Publish the safe model first: a crash must never leave a terminal pointing
    # readers at a failed child via an older survivor record. Gate reconciliation
    # can recreate the terminal after this write without changing the fallback.
    write_json(Path(out)/'survivor.json',dict(adapter=fallback,generation=-1,frozen=True,
        fallback='keep_sft81',stopped_after_generation=generation,
        evaluated_adapter=str(adapter),evidence=str(Path(out)/'terminal.json')))
    write_json(Path(out)/'terminal.json',terminal)
    return terminal


def reconcile_failed_gate(out,*,fallback_adapter=None):
    """A crash after the measured verdict cannot erase a failure on resume."""
    out=Path(out);gate=out/'first-gate.json'
    if gate.exists() and not (out/'terminal.json').exists():
        row=json.loads(gate.read_text())
        if row.get('audit',{}).get('accepted') is False:
            persist_stop(out,generation=row.get('generation',-1),adapter=row.get('adapter',''),
                audit=row['audit'],reason='resumed_failed_audit_gate',
                fallback_adapter=fallback_adapter or row.get('fallback_adapter'))

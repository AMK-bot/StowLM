"""Validate development-only evidence that the fixed tier cap actually binds.

The profile study supplies fresh-audited no-rule rows and their intended-rule
counterfactual checks. This module performs no planning and cannot open sealed
inputs. A receipt cannot be substituted after the run is frozen.
"""
from __future__ import annotations

from .common import HERE, HarnessError, read_json, sha256
from .instructions import CAP_REDUCTION


def verify_calibration(path):
    from .driver import public_path
    if not path:
        raise HarnessError('Development max_tiers calibration awaits audited no-rule plans')
    receipt = read_json(public_path(path))
    from .budget import standard_profile_hash
    if (receipt.get('schema_version') != 'sealed-tier-calibration/1'
            or receipt.get('split') != 'development'
            or receipt.get('fixture_surrogate') is not False
            or receipt.get('cap_reduction') != CAP_REDUCTION
            or receipt.get('profile_sha256') != standard_profile_hash()
            or receipt.get('binding_sha256') != sha256(HERE.parent/'binding/adapter.py')
            or receipt.get('instructions_sha256') != sha256(HERE/'instructions.py')):
        raise HarnessError('Tier calibration must pin real development evidence and the registered tier reduction')
    dev_list = public_path(HERE.parent/'maps/dev.txt')
    if receipt.get('development_list_sha256') != sha256(dev_list):
        raise HarnessError('Tier calibration differs from fixed development cohort')
    expected = {str(public_path(line.split('#')[0].strip()))
                for line in dev_list.read_text().splitlines() if line.split('#')[0].strip()}
    if len(expected) != 32:
        raise HarnessError('Tier calibration requires the registered 32 development maps')
    evidence = receipt.get('evidence', [])
    if len(evidence) != len(expected) or {str(public_path(r['map_path'])) for r in evidence} != expected:
        raise HarnessError('Tier calibration must account for every development map without selection')
    files = {str(dev_list): sha256(dev_list)}
    from .common import canonical
    from .worker import _load
    from .instructions import public_facts, select_rule
    instances = {name: _load(dict(path=name, sha256=sha256(name), map_id=name,
                                arm='pipeline', mode='dry-run')) for name in expected}
    ranks = {name: rank for rank, name in enumerate(sorted(expected,
             key=lambda name: (canonical(public_facts(instances[name])), name)))}
    broken = checked = 0
    for item in evidence:
        map_path, row_path = public_path(item['map_path']), public_path(item['audit_row'])
        for source, digest in ((map_path, item['map_sha256']), (row_path, item['audit_sha256'])):
            if sha256(source) != digest:
                raise HarnessError('Tier calibration evidence hash differs')
            files[str(source)] = digest
        row = read_json(row_path)
        if row.get('map_sha256') != item['map_sha256'] or row.get('arm') != 'pipeline':
            raise HarnessError('Tier calibration baseline identity differs')
        audited = (row.get('audit_verified') is True and row.get('audit_fresh_process') is True
                   and row.get('audit_pid') != row.get('planning_pid')
                   and row.get('passed') is True and row.get('full_checker_pass') is True
                   and row.get('level') in (0, 'L0'))
        rule = row.get('counterfactual_rule', {})
        selected_rule = select_rule(instances[str(map_path)], (ranks[str(map_path)]//4)*4+1)
        valid_rule = (rule == selected_rule and item.get('cap_reduction') == CAP_REDUCTION
                      and row.get('counterfactual_rule_sha256') == item.get('rule_sha256'))
        from .common import object_hash
        valid_rule = valid_rule and object_hash(rule) == item.get('rule_sha256')
        if audited and valid_rule:
            checked += 1
            broken += row.get('counterfactual_instruction_recheck') is False
    # Use the whole development cohort as denominator: missing/failed baselines
    # cannot manufacture a majority among a selected successful subset.
    if broken <= len(expected)/2:
        raise HarnessError('Registered tier cap has not bound on most development maps')
    return dict(maps=len(expected), checked=checked, binding=broken,
                cap_reduction=CAP_REDUCTION, files=files, receipt_sha256=sha256(public_path(path)))

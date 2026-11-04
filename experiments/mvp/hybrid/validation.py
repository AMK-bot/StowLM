"""Shared, fail-closed evidence validation for resuming and reporting a cohort.

The validator reads no live source tree. A sealed manifest binds the immutable
runtime and exact validation inputs; a fresh audit binds the actual final plan.
"""
from __future__ import annotations

import hashlib
import json
from math import isfinite
from pathlib import Path
try:
    from .contribution import decision_contribution
except ImportError:
    import importlib.util
    spec = importlib.util.spec_from_file_location('hybrid_validation_contribution', Path(__file__).with_name('contribution.py'))
    contribution = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contribution)
    decision_contribution = contribution.decision_contribution
try:
    from .admission import RULE as ADMISSION_RULE, evidence_errors as admission_errors
except ImportError:
    import importlib.util
    spec = importlib.util.spec_from_file_location('hybrid_validation_admission', Path(__file__).with_name('admission.py'))
    admission = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(admission)
    ADMISSION_RULE, admission_errors = admission.RULE, admission.evidence_errors

ARMS = ('none', 'llm', 'random')


def plan_sha256(plan: dict) -> str:
    """Match the fresh checker's existing stable plan serialization exactly."""
    return hashlib.sha256(json.dumps(plan, sort_keys=True, allow_nan=False).encode()).hexdigest()


def cohort_id(manifest: dict) -> str:
    payload = {k: v for k, v in manifest.items() if k != 'cohort_id'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _sha(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def manifest_errors(manifest: dict) -> list[str]:
    if not isinstance(manifest, dict):
        return ['sealed manifest is required']
    errors = []
    if manifest.get('cohort_status') != 'active' or manifest.get('quarantined') or manifest.get('aborted'):
        errors.append('manifest cohort is not active')
    try:
        if not _sha(manifest.get('cohort_id')) or manifest['cohort_id'] != cohort_id(manifest):
            errors.append('manifest cohort digest does not match its contents')
    except (TypeError, ValueError):
        errors.append('manifest is not canonical JSON')
    if manifest.get('decision') != 'D199' or manifest.get('arms') != list(ARMS):
        errors.append('manifest is not the D199 three-arm cohort')
    maps, inputs = manifest.get('maps'), manifest.get('inputs')
    if not isinstance(maps, dict) or not maps or not isinstance(inputs, dict):
        errors.append('manifest must bind instance IDs and input hashes')
    elif (any(not isinstance(path, str) for path in maps.values())
          or len(maps) != manifest.get('n_maps') or len(set(maps.values())) != len(maps)
          or set(maps.values()) != set(inputs)):
        errors.append('manifest maps and inputs are not a bijection')
    else:
        for iid, path in maps.items():
            if (not isinstance(iid, str) or '.small.' not in iid or not isinstance(path, str)
                    or not path.startswith('experiments/mvp/corpus/val/small/')
                    or any(p in ('..', 'test', 'test-v3', 'showcase') for p in path.split('/'))):
                errors.append('manifest contains an input outside validation-small')
                break
        if any(not _sha(value) for value in inputs.values()):
            errors.append('manifest input hashes are invalid')
    groups = manifest.get('groups')
    if groups is not None:
        if (not isinstance(groups, dict) or set(groups) != {'entering', 'aa'}
                or any(not isinstance(group, list) or any(not isinstance(iid, str) for iid in group)
                       for group in groups.values())):
            errors.append('manifest groups must declare entering and aa instance-ID lists')
        else:
            declared = groups['entering'] + groups['aa']
            if len(declared) != len(set(declared)) or not isinstance(maps, dict) or set(declared) != set(maps):
                errors.append('manifest entering/aa groups must be disjoint and cover every declared map')
    amendments = manifest.get('amendments', [])
    if not isinstance(amendments, list) or any(not isinstance(item, str) for item in amendments):
        errors.append('manifest amendments must be a decision-ID list')
    if isinstance(amendments, list) and 'D205' in amendments:
        if (manifest.get('n_maps') != 40 or not isinstance(groups, dict)
                or not isinstance(groups.get('entering'), list) or len(groups['entering']) != 32
                or not isinstance(groups.get('aa'), list) or len(groups['aa']) != 8):
            errors.append('D205 must declare exactly 32 entering maps and 8 A/A maps')
    if not isinstance(manifest.get('runtime'), dict) or not _sha(manifest['runtime'].get('id')):
        errors.append('manifest lacks immutable runtime identity')
    if not isinstance(manifest.get('candidates'), dict) or any(
            not _sha(value) for value in manifest.get('candidates', {}).values()):
        errors.append('manifest candidate hashes are invalid')
    if not isinstance(manifest.get('candidate_paths'), dict) or set(manifest['candidate_paths']) != {'llm', 'random'}:
        errors.append('manifest candidate paths must name llm and random')
    for name in ('watchdog_policy', 'admission_rule'):
        if not isinstance(manifest.get(name), dict) or not manifest[name]:
            errors.append(f'manifest lacks declared {name}')
    policy = manifest.get('watchdog_policy') if isinstance(manifest.get('watchdog_policy'), dict) else {}
    if any(policy.get(key) != value for key, value in dict(
            planner_s=3600, audit_s=600, terminate_grace_s=5, kill_grace_s=5).items()):
        errors.append('manifest watchdog differs from the declared symmetric policy')
    admission = manifest.get('admission_rule') if isinstance(manifest.get('admission_rule'), dict) else {}
    if manifest.get('workers') != 4 or admission != ADMISSION_RULE:
        errors.append('manifest admission must use four workers and the fixed D206 fresh-file/popcount/load rule')
    if (manifest.get('seed') != 0 or manifest.get('search_s_per_box') != 6
            or manifest.get('search_cap_s') != 120 or manifest.get('finalize_s') != 300):
        errors.append('manifest budgets differ from fixed D199 settings')
    return errors


def require_manifest(manifest: dict) -> None:
    errors = manifest_errors(manifest)
    if errors:
        raise ValueError('; '.join(errors))


def require_active_directory(run_dir) -> None:
    for marker in ('QUARANTINED.json', 'ABORTED.json'):
        if (Path(run_dir) / marker).exists():
            raise ValueError(f'cohort directory has {marker}; it cannot supply report or resume evidence')


def expected_binding(manifest: dict, input_path: str) -> dict:
    require_manifest(manifest)
    if input_path not in manifest['inputs']:
        raise ValueError('input is not in the sealed manifest')
    return dict(cohort_id=manifest['cohort_id'], runtime_id=manifest['runtime']['id'],
                input_sha256=manifest['inputs'][input_path], candidates=manifest['candidates'],
                candidate_paths=manifest['candidate_paths'], seed=manifest['seed'],
                finalize_s=manifest['finalize_s'], search_cap_s=manifest['search_cap_s'],
                watchdog_policy=manifest['watchdog_policy'], admission_rule=manifest['admission_rule'])


def _cost(value) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value >= 0
    except OverflowError:
        return False


def validation_errors(row: dict, *, manifest: dict, expected_iid=None, expected_input=None,
                      expected_arm=None, expected_binding=None, require_complete=True) -> list[str]:
    errors = manifest_errors(manifest)
    if errors:
        return errors
    if not isinstance(row, dict):
        return ['trial row is not an object']
    iid, inp, arm = row.get('instance_id'), row.get('input'), row.get('arm')
    if not all(isinstance(value, str) for value in (iid, inp, arm)):
        return ['row instance, input, and arm must be strings']
    if iid not in manifest['maps'] or manifest['maps'].get(iid) != inp:
        errors.append('row instance/input does not belong to the sealed cohort')
    if expected_iid is not None and iid != expected_iid:
        errors.append('row instance differs from its expected trial')
    if expected_input is not None and inp != expected_input:
        errors.append('row input differs from its expected trial')
    if arm not in ARMS or (expected_arm is not None and arm != expected_arm):
        errors.append('row arm differs from its expected trial')
    if row.get('cohort_id') != manifest['cohort_id']:
        errors.append('row belongs to a foreign cohort')
    if row.get('quarantined') or row.get('aborted'):
        errors.append('row is quarantined or aborted')
    if inp in manifest['inputs']:
        sealed_binding = globals()['expected_binding'](manifest, inp)
        if row.get('binding') != sealed_binding or (expected_binding is not None and expected_binding != sealed_binding):
            errors.append('row binding differs from the sealed manifest/trial')
    if require_complete and row.get('completed') is not True:
        errors.append('trial has not completed')
    if row.get('error'):
        errors.append('trial has an execution or audit error')
    if not isinstance(row.get('passed'), bool):
        errors.append('row must explicitly declare passed true or false')
    if row.get('repaired') is not False:
        errors.append('row must explicitly declare no repair')
    evidence = row.get('admission')
    errors.extend(admission_errors(evidence))
    if isinstance(evidence, dict) and row.get('start_utc') != evidence.get('utc'):
        errors.append('trial start differs from the admission observation time')
    if row.get('passed') is not True:
        return errors
    plan, audit = row.get('plan'), row.get('audit')
    if not isinstance(plan, dict) or plan.get('instance_id') != iid:
        errors.append('passing row lacks its expected final plan')
    if not isinstance(audit, dict):
        return errors + ['passing row lacks a full fresh audit']
    for flag in ('passed', 'stored_passed', 'fresh_process', 'legal', 'mask_ok', 'complete', 'all_loaded'):
        if audit.get(flag) is not True:
            errors.append(f'audit {flag} must be true')
    if audit.get('repaired') is not False or audit.get('error'):
        errors.append('audit is repaired or has an execution error')
    for key, value in (('instance_id', iid), ('input', inp), ('arm', arm),
                       ('cohort_id', manifest['cohort_id']), ('runtime_id', manifest['runtime']['id'])):
        if audit.get(key) != value:
            errors.append(f'audit {key} differs from the current trial')
    costs = (row.get('weighted'), audit.get('weighted'), audit.get('stored'))
    if not all(_cost(cost) for cost in costs) or not costs[0] == costs[1] == costs[2]:
        errors.append('row, audited, and stored costs do not all agree')
    try:
        digest = plan_sha256(plan)
        if not row.get('plan_sha256') == audit.get('plan_sha256') == digest:
            errors.append('row/audit plan hash does not bind the final plan')
    except (TypeError, ValueError):
        errors.append('final plan is not canonical JSON')
    seed_origin = row.get('final_search_origin')
    boxes, seed, evidence = row.get('final_load_list'), row.get('final_seed_decisions'), row.get('final_contribution')
    if (not isinstance(seed_origin, str) or not seed_origin or seed_origin.startswith('search-')
            or seed_origin != row.get('final_origin')
            or not isinstance(row.get('final_search_changed'), bool)):
        errors.append('final producing seed origin/change evidence is invalid')
    if (not isinstance(boxes, list) or not boxes or any(not isinstance(b, str) for b in boxes)
            or boxes != sorted(set(boxes)) or not isinstance(seed, dict) or not isinstance(evidence, dict)):
        errors.append('final retained-contribution evidence is missing or malformed')
    else:
        final = evidence.get('final_decisions')
        if not isinstance(final, dict) or set(final) != set(boxes):
            errors.append('checked final decisions do not cover the complete load list')
        else:
            expected = decision_contribution(seed_origin, boxes, seed, final)
            if evidence != expected or audit.get('final_contribution') != expected:
                errors.append('retained contribution differs from recomputation or fresh audit')
        starts = row.get('starts')
        if not isinstance(starts, list) or not any(
                s.get('origin') == seed_origin and s.get('seed_decisions') == seed
                for s in starts if isinstance(s, dict)):
            errors.append('final seed decisions are not bound to a recorded start')
    witnesses = row.get('final_reaching_runs')
    if (not isinstance(witnesses, list) or not isinstance(row.get('final_shared_stow'), bool)
            or row.get('final_shared_stow') != (len(witnesses) > 1)):
        errors.append('shared-stow provenance is missing or inconsistent')
    metadata, extra = row.get('candidates', {}), row.get('extra_starts')
    admitted = metadata.get('admitted', []) if isinstance(metadata, dict) else None
    audited_seeds = audit.get('admitted_seed_decisions')
    if (not isinstance(admitted, list) or not isinstance(extra, list)
            or not isinstance(audited_seeds, dict)):
        errors.append('admitted candidate decisions lack fresh-audit binding')
    else:
        labels = [entry.get('label') for entry in admitted if isinstance(entry, dict)]
        extra_labels = [entry.get('origin') for entry in extra if isinstance(entry, dict)]
        valid_labels = (len(labels) == len(admitted) and all(isinstance(label, str) and ':' in label
                        and label.partition(':')[0] in ('llm', 'random', 'adapter')
                        and bool(label.partition(':')[2]) for label in labels))
        if (not valid_labels or len(set(labels)) != len(labels) or len(extra_labels) != len(extra)
                or len(set(extra_labels)) != len(extra_labels) or set(labels) != set(extra_labels)
                or set(labels) != set(audited_seeds) or (arm == 'none' and labels)):
            errors.append('admitted labels differ from audited/extra-start labels or name software starts')
        else:
            extra_seeds = {entry['origin']: entry.get('seed_decisions') for entry in extra}
            if (any(not isinstance(seed_map, dict) for seed_map in extra_seeds.values())
                    or extra_seeds != audited_seeds):
                errors.append('admitted seed decisions differ from the fresh raw-candidate replay')
            if seed_origin in audited_seeds and seed != audited_seeds[seed_origin]:
                errors.append('final producing seed differs from its audited admitted candidate')
            for start in row.get('starts', []):
                if start.get('origin') in audited_seeds and start.get('seed_decisions') != audited_seeds[start['origin']]:
                    errors.append('recorded extra start differs from its audited admitted candidate')
            for witness in witnesses if isinstance(witnesses, list) else []:
                if (isinstance(witness, dict) and witness.get('origin') in audited_seeds
                        and witness.get('seed_decisions') != audited_seeds[witness['origin']]):
                    errors.append('shared extra witness differs from its audited admitted candidate')
        known = {'twostep', 'blocked', 'master', 'yard_first'} | set(labels) if valid_labels else set()
        if seed_origin not in known:
            errors.append('final producing seed is neither software nor an audited admitted candidate')
        if isinstance(witnesses, list) and any(not isinstance(witness, dict)
                or witness.get('origin') not in known for witness in witnesses):
            errors.append('shared-stow witness is neither software nor an audited admitted candidate')
    return errors


def validate_row(row: dict, **context) -> bool:
    try:
        return not validation_errors(row, **context)
    except (TypeError, ValueError, OverflowError):
        return False


def require_row(row: dict, **context) -> None:
    errors = validation_errors(row, **context)
    if errors:
        raise ValueError('; '.join(errors))

"""Bind recorded release evidence to the exact instance, plan and rule set."""
from __future__ import annotations

from experiments.mvp.pipeline.contract.schema import sha256_json
from .store import canonical_rule_set


def _json(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value


def recorded_gate_pass(instance, plan, rules, gate):
    """Validate recorded bindings only; this does not run or replace a gate."""
    try:
        instance, plan = _json(instance), _json(plan)
        if not isinstance(instance, dict) or not isinstance(plan, dict) or not isinstance(rules, list):
            return False
        canonical = canonical_rule_set(rules)
        audits = gate.get('rules')
        gauges = gate.get('gauges') or {}
        return (isinstance(plan.get('moves'), list) and plan.get('instance_id') == instance['id']
                and gate.get('instance_sha256') == sha256_json(instance)
                and gate.get('plan_sha256') == sha256_json(plan)
                and gate.get('passed') is True and (gate.get('checker') or {}).get('passed') is True
                and gauges.get('passed') is True and gauges.get('all_frames') is True
                and gauges.get('outside_limits_steps') == [] and gauges.get('unavailable_steps') == []
                and gauges.get('n_frames') == len(plan['moves']) + 1
                and isinstance(audits, list) and len(audits) == len(canonical)
                and all(isinstance(audit, dict) and audit.get('passed') is True for audit in audits)
                and canonical_rule_set([audit['envelope'] for audit in audits]) == canonical)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def repair_context(source, instance):
    """Resolve confirmed rules and baseline only after checking source identity."""
    from stowage.model import MovePlan
    content = _json(instance)
    if (source.get('instance_sha256') != sha256_json(content)
            or source.get('instance') is not None and source['instance'] != content
            or source.get('instance_id', content['id']) != content['id']):
        raise ValueError('repair source does not match the loaded original instance')
    plan = MovePlan.model_validate(source['plan'])
    if plan.instance_id != content['id']:
        raise ValueError('repair source plan belongs to another instance')
    rules = canonical_rule_set(source.get('canonical_rules', source.get('rules', [])))
    if 'canonical_rules' in source and 'rules' in source and canonical_rule_set(source['rules']) != rules:
        raise ValueError('repair source rule identities differ')
    base = MovePlan.model_validate(source['base_plan']) if source.get('base_plan') is not None else None
    if base is not None and base.instance_id != content['id']:
        raise ValueError('repair baseline belongs to another instance')
    return plan, rules, base


def require_repair_gate(instance, plan, rules, gate, *, base_plan=None):
    if not recorded_gate_pass(instance, plan, rules, gate):
        raise ValueError('repair output gate is not bound to its exact instance, plan and confirmed rules')
    if base_plan is not None and gate.get('base_passed') is not True:
        raise ValueError('repair output gate did not pass the supplied baseline')

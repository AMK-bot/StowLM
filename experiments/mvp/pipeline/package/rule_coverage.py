"""Catalogue coverage of the full checker, independent of packet applicability.

Read metadata and inspect the actual activation sites; do not enable the freeze
switch or infer coverage from an empty violation list. The six gated contracts
are distinct from their legacy companions (height, physical open-hatch guard,
weight ordering and OOG), which remain enabled with their legacy semantics.
"""
from __future__ import annotations

import ast
from hashlib import sha256
from pathlib import Path
import re

from stowage.rules.catalogue import DEFAULT_CATALOGUE_PATH, catalogue_snapshot


SCOPE = (
    'Catalogue rules enabled for full-checker enforcement, not rules exercised '
    'by this packet; cargo, port, geometry and inherited-state applicability still apply. '
    'Solver-only soft objectives are not checker rules. Legacy companions retain '
    'their off-state semantics; active does not mean the pending freeze contract is enabled.'
)


def _strings(node):
    return {part.value for part in ast.walk(node)
            if isinstance(part, ast.Constant) and isinstance(part.value, str)}


def _function(tree, name):
    matches = [part for part in ast.walk(tree)
               if isinstance(part, (ast.FunctionDef, ast.AsyncFunctionDef)) and part.name == name]
    if len(matches) != 1:
        raise ValueError(f'Rule coverage audit: expected one {name} function')
    return matches[0]


def _early_gate(function, condition):
    for index, statement in enumerate(function.body):
        if (isinstance(statement, ast.If) and ast.unparse(statement.test) == condition
                and len(statement.body) == 1 and isinstance(statement.body[0], ast.Return)):
            return ast.Module(body=function.body[index + 1:], type_ignores=[])
    raise ValueError(f'Rule coverage audit: missing early gate in {function.name}')


def _gated_call(function, condition, target):
    for statement in ast.walk(function):
        if isinstance(statement, ast.If) and ast.unparse(statement.test) == condition:
            if any(isinstance(part, ast.Call) and isinstance(part.func, ast.Name)
                   and part.func.id == target for body in statement.body for part in ast.walk(body)):
                return
    raise ValueError(f'Rule coverage audit: {target} is not behind {condition}')


def _assignment(tree, target, expression):
    for part in ast.walk(tree):
        if (isinstance(part, ast.Assign) and ast.unparse(part.value) == expression
                and any(ast.unparse(name) == target for name in part.targets)):
            return
    raise ValueError(f'Rule coverage audit: missing {target} = {expression}')


def _constant(tree, name):
    for part in tree.body:
        if isinstance(part, ast.Assign) and any(isinstance(target, ast.Name)
                and target.id == name for target in part.targets):
            if isinstance(part.value, ast.Constant) and isinstance(part.value.value, str):
                return part.value.value
    raise ValueError(f'Rule coverage audit: missing {name} rule constant')


def _audit_gates(sources, catalogue_ids):
    """Derive rule ids at the audited runtime gates; fail closed on drift."""
    trees = {name: ast.parse(content) for name, content in sources.items()}
    port = _function(trees['moves/_port_rules.py'], 'port_move_violation')
    port_rules = _strings(_early_gate(port, 'not rule_freeze_v1_enabled()')) & catalogue_ids
    crossing = _function(trees['moves/_physical.py'], '_crossing')
    _assignment(_function(trees['moves/_physical.py'], '__init__'),
                'self.crossing_enabled', 'rule_freeze_v1_enabled()')
    crossing_rules = _strings(_early_gate(crossing, 'not self.crossing_enabled')) & catalogue_ids
    _gated_call(_function(trees['checker/full.py'], '_check'),
                'rule_freeze_v1_enabled()', '_in_port')
    in_port = trees['checker/in_port.py']
    replay = _function(in_port, '_replay')
    _assignment(replay, 'd74', 'rule_freeze_v1_enabled()')
    _gated_call(replay, 'd74', 'check_programme')
    criteria = _constant(in_port, '_RULE')
    rate = _constant(trees['checker/in_port_programme.py'], 'RULE')
    if (len(port_rules) != 2 or len(crossing_rules) != 2
            or criteria not in catalogue_ids or rate not in catalogue_ids):
        raise ValueError('Rule coverage audit: runtime gate rule ids changed')
    gates = {rule: 'moves/_port_rules.py:port_move_violation' for rule in port_rules}
    gates.update({rule: 'moves/_physical.py:Replay._crossing' for rule in crossing_rules})
    gates[criteria] = 'checker/full.py:_check -> checker/in_port.py:_replay'
    gates[rate] = 'checker/full.py:_check -> checker/in_port.py:_replay:d74 -> check_programme'
    return gates


def _requires_freeze(rule):
    parameter = rule.parameters.get('activation_path')
    path = parameter.value if parameter is not None else ''
    return isinstance(path, str) and ('INACTIVE' in path or bool(
        re.search(r'(?:activation\.)?rule_freeze_v1\s*=\s*true', path)))


def rule_coverage() -> dict:
    """Return JSON-able names, counts and exact catalogue/code identities.

    This is an audited enabled-rule inventory, not a claim that every rule
    applies or ran on the instance. Unknown metadata gates fail closed so a
    future catalogue cannot silently inherit the current count.
    """
    snapshot = catalogue_snapshot()
    root = Path(DEFAULT_CATALOGUE_PATH).parent.parent
    paths = sorted([root / 'rules/catalogue.py',
                    *(root / 'checker').rglob('*.py'), *(root / 'moves').rglob('*.py')])
    sources = {str(path.relative_to(root)): path.read_bytes() for path in paths}
    ids = set(snapshot.rules)
    gates = _audit_gates(sources, ids)
    metadata_gates = {rule.id for rule in snapshot.catalogue if _requires_freeze(rule)}
    if metadata_gates != set(gates):
        raise ValueError('Rule coverage audit: catalogue/runtime activation gates differ: '
                         + ', '.join(sorted(metadata_gates ^ set(gates))))
    enabled = snapshot.catalogue.activation.rule_freeze_v1
    inactive = {}
    for rule in snapshot.catalogue:
        if 'checker' not in rule.enforced_by:
            inactive[rule.id] = 'Not checker-enforced; catalogue targets: ' + ', '.join(rule.enforced_by)
        elif not enabled and rule.id in gates:
            inactive[rule.id] = 'rule_freeze_v1 is false; ' + gates[rule.id]
    active = sorted(ids - set(inactive))
    return {
        'active_rules': active, 'inactive_rules': sorted(inactive),
        'active_rule_count': len(active), 'inactive_rule_count': len(inactive),
        'catalogue_sha256': sha256(snapshot.content).hexdigest(),
        'rule_freeze_v1': enabled,
        'inactive_rule_reasons': dict(sorted(inactive.items())),
        'rule_coverage_scope': SCOPE,
        'rule_gate_sources': dict(sorted(gates.items())),
        'rule_source_sha256': {name: sha256(content).hexdigest() for name, content in sources.items()},
        'rule_coverage_notes': [
            'stability.open_hatch_immersion represents the existing open-coaming physical guard; '
            'checker/strength.py reports that legacy guard as port.stability.cargo_operations. '
            'The every-lift in-port stability contract remains flag-gated.',
            'expert.e6.heavy_under_deck retains its legacy hard fallback while the freeze is off; '
            'the pending soft preference is not substituted.',
        ],
    }

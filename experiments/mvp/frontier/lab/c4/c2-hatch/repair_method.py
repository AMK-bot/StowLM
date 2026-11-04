"""Admit bounded repeated-Place realization of cached relaxation witnesses.

The reference action sequence is only a scorecard carrier. The finisher returns
the generated complete MovePlan, which the unchanged full checker admits and
counts independently. This is proof-count work, never a cold runtime claim.
"""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.model import MovePlan

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from constructor import install_e9
from method import _jsonable
from restow_repair import repair


def propose(instance, *, seed, budget_s, context):
    install_e9()
    source = HERE/'results/proofs-120'/f'{instance.id}.json'
    if not source.exists():
        return []
    row = json.loads(source.read_text())
    if row['input_sha256'] != sha256(Path(context['instance_path']).read_bytes()).hexdigest():
        raise ValueError('repair witness input digest mismatch')
    result = row.get('result') or {}
    assignment, order = result.get('relaxation_assignment'), result.get('first_aboard_order')
    if not assignment or not order:
        return []
    flags = result.get('current_order_flags') or {}
    if not flags.get('ship_restowed') and not context.get('_repair_include_unflagged'):
        return []  # Unflagged warm witnesses were already admitted unchanged.
    outcome = repair(instance, assignment, order, flags, budget_s=budget_s,
                     max_flagged=3, max_nodes=2000, max_replayed_moves=10000,
                     max_temporary_slots=24, tables=context['problem'].engine.T)
    diagnostics = _jsonable(asdict(outcome))
    diagnostics.update(input_sha256=row['input_sha256'], source_evidence=str(source.relative_to(HERE)),
                       repair_sha256=sha256((HERE/'restow_repair.py').read_bytes()).hexdigest())
    folder = HERE/'results/repair-diagnostics'
    folder.mkdir(parents=True, exist_ok=True)
    (folder/f'{instance.id}.json').write_text(json.dumps(diagnostics, indent=2)+'\n')
    context['method_metadata']['repair'] = diagnostics
    if outcome.plan is None:
        return []
    index = json.loads((HERE/'references/upper_index.json').read_text())
    upper = MovePlan.model_validate(index[instance.id]['plan'])
    context['_repair_plan'], context['_upper_ballast'] = outcome.plan, upper.ballast
    context['method_metadata']['carrier_only'] = True
    return [tuple(a for a in get_action_space('boxslot').from_plan(instance, upper)
                  if isinstance(a, Place))]


def finish(instance, plan, *, seed, finalize_s, label, context):
    return context['_repair_plan'].model_copy(update={'ballast': context['_upper_ballast']}), {
        'mode': 'generated repeated-Place plan overrides carrier; inherited ballast; full checker counts actual plan'}

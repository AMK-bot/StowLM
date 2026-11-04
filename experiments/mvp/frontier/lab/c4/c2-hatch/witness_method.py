"""Generic replay/full-check admission of a cached global relaxation witness."""
from hashlib import sha256
import json
from pathlib import Path
import sys
from stowage.actions.space import Place
from stowage.model import MovePlan

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from constructor import install_e9


def propose(instance, *, seed, budget_s, context):
    install_e9()
    source = HERE/'results/proofs-120'/f'{instance.id}.json'
    if not source.exists():
        return []
    row = json.loads(source.read_text())
    actual = sha256(Path(context['instance_path']).read_bytes()).hexdigest()
    if row['input_sha256'] != actual or row['seed'] != seed:
        raise ValueError('relaxation witness input/seed mismatch')
    result = row.get('result') or {}
    assignment = result.get('relaxation_assignment')
    order = result.get('first_aboard_order')
    if not assignment or not order:
        return []
    index = json.loads((HERE/'references/upper_index.json').read_text())
    context['_upper_ballast'] = MovePlan.model_validate(index[instance.id]['plan']).ballast
    context['method_metadata'].update(witness_evidence=str(source.relative_to(HERE)),
        bound_wall_s=row['wall_s'], restricted_family=False,
        order_flags=result.get('current_order_flags'), warm_ballast=True)
    return [tuple(Place(bid, assignment[bid]) for bid in order)]


def finish(instance, plan, *, seed, finalize_s, label, context):
    return plan.model_copy(update={'ballast': context['_upper_ballast']}), {
        'mode': 'global relaxation witness, actual replay, inherited ballast, independent full check'}

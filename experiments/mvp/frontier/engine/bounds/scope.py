"""Shared certificate labels and objective guards."""
import math


def validate_objective(instance):
    w = instance.objective
    values = (w.relocate, w.restow, w.hatch_cover, w.future_restow, w.future_hatch_cover)
    if any(not math.isfinite(float(value)) or float(value) < 0 for value in values):
        raise ValueError('certified lower bounds require finite nonnegative weights')
    per_box = w.future_restow_lifts_per_box
    if isinstance(per_box, bool) or not isinstance(per_box, int) or per_box < 1:
        raise ValueError('future restow lifts per box must be a positive integer')
    return values


def proof_details(name, *, family='arbitrary_current_moves', eligible=True):
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    return dict(bound=name, current_move_family=family,
                objective='D11_weighted_lifts',
                later_ports='D15_fixed_stow_no_later_loads',
                claim='fewest lifts among checker-passing plans, counting later ports by D15 fixed stow with no later loads',
                equality_requires='fresh unchanged full-checker pass at exactly the bound',
                rule_freeze_v1=rule_freeze_v1_enabled(),
                certificate_eligible=eligible, full_checker_pass_claim=False)



def proof_scope(name, *, family='arbitrary_current_moves', eligible=True):
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    freeze = 'on' if rule_freeze_v1_enabled() else 'off'
    return (f'{name}: fewest lifts among checker-passing plans; '
            f'D11 with D15 fixed stow, no later loads; {family}; L0 under the current rule set, '
            f'rule freeze {freeze}, including provisional expert rules')

def source_hashes():
    """Record the native rules and local mathematical implementation used."""
    import hashlib
    import importlib
    from pathlib import Path
    out = {}
    for name in ('stowage.checker.full', 'stowage.checker.rules',
                 'stowage.checker.rules_special', 'stowage.checker.rules_weight',
                 'stowage.checker.rules_imdg', 'stowage.checker.lashing',
                 'stowage.feasibility.tables'):
        module = importlib.import_module(name)
        out[name] = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
    here = Path(__file__).parent
    for path in sorted((here/'exact').glob('*.py')):
        if not path.name.startswith('test_'):
            out['engine.bounds.exact.'+path.stem] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out

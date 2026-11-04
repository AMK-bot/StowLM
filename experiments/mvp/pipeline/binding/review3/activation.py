"""One-way, row-boundary upgrade for the already-detached campaign.

Imported by the tiny top-level discovery module only after policy tests pass.
No process is stopped, replaced, or added here.
"""
import sys

_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return True
    from ..hooks import binding_active
    if binding_active():
        raise RuntimeError('review3 activation must occur outside an active engine binding')
    from . import api, campaign
    campaign.install()
    for name in ('experiments.mvp.pipeline.binding', 'binding'):
        package = sys.modules.get(name)
        if package is not None:
            for symbol in ('plan_with_rules', 'execute_brief', 'confirmation_text', 'planner_message'):
                setattr(package, symbol, getattr(api, symbol))
    _INSTALLED = True
    return True

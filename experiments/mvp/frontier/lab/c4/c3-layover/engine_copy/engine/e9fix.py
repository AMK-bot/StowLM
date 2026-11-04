# Copied from experiments/mvp/frontier/lab/c1/leader/e9fix.py; engine integration changes are local.
"""Make the search mask's E9 clearance lookup agree with the checker, in this process only.

`_e9_batch` reads the clearance with `rules_special._parameter`, which sees only explicit
values, gets None on maps that rely on the catalogue default of 0.0 m, and then refuses every
width or length OOG box whatever its slot. The checker reads the same value with
`_clearance_parameter`, resolves the default and tests the real envelopes. This wrapper gives
`_e9_batch` the checker's lookup for its own call. `src/` and the checker are untouched, and
the checker still decides every plan. Reviewed diff: `experiments/mvp/scale/native_e9_parameter.diff`.
"""
from __future__ import annotations

import stowage.feasibility._engine as engine
from stowage.checker import rules_special

RULE = 'expert.e9.over_width_length'
_installed = False
_owner = None
_original_batch = None
_wrapper = None


def _lookup(original):
    def parameter(instance, rule, *names):
        if rule == RULE and 'oog_clearance' in names:
            return rules_special._clearance_parameter(instance, rule, *names)
        return original(instance, rule, *names)
    return parameter


def install() -> None:
    global _installed, _owner, _original_batch, _wrapper
    if _installed:
        return
    owner = next(c for c in vars(engine).values() if isinstance(c, type) and '_e9_batch' in vars(c))
    batch = owner._e9_batch

    def e9_batch(self, st, bs, ss, *, all_boxes):
        original = rules_special._parameter
        rules_special._parameter = _lookup(original)
        try:
            return batch(self, st, bs, ss, all_boxes=all_boxes)
        finally:
            rules_special._parameter = original

    owner._e9_batch = e9_batch
    _owner, _original_batch, _wrapper = owner, batch, e9_batch
    _installed = True


def uninstall() -> None:
    """Permit an ablation in a process that previously ran the default engine."""
    global _installed
    if _installed and _owner._e9_batch is _wrapper:
        _owner._e9_batch = _original_batch
    _installed = False

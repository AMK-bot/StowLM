Copied `bound.py`, `method.py`, `pages.py`, `page_base.py`, `test_bound.py` and
`test_pages.py` from `lab/c3/c3-layover/`. Imports now resolve entirely inside
engine or frozen native helpers. The copied page base is arrival-specific and
keeps fixed-prefix identities in its equivalence types; it does not import the
experimental engine snapshot. The tiny TRAIN input is a test fixture only.

The lower bound handles retained arrival cargo, current discharge and new
loads. Through-box movement flags count at most one necessary current restow,
including a direct shift; cover/column clearance can force a move even when a
box returns to its old coordinates. Current relocation accounting is disjoint
from packing/restow terms. Retained arrival slots survive current handling
unary restrictions. DG slots are widened. Inherited soft E1/E2 inversions are
not forced repairs. The certificate names arbitrary current MovePlans and D11
with D15 fixed stow/no later loads; only exact equality to a fresh full-checker
pass proves fewest lifts in that model. It rejects invalid weights/overrides
and declines forecasts/joint later loads. No replay witness is a pass.

Construction prepares real Discharge/RestowOff/direct-shift actions through
native Expander, exposes forced quay boxes to pricing, fixes the remaining
bottom prefixes and subtracts their tiers, mass and height. Existing hold PODs
and deck blockers affect layout/pricing. Native replay scores every proposal
against the original instance, including all current prefix costs. The normal
finisher/full checker remains the pass gate. Mixed arrival bases remain outside
this conservative page family; bounded failure proves nothing about the map.

`method.propose` accepts `context['layover_switches']` for `e9_fix`,
`prefix_restows`, `direct_shift` and `layouts`; all default on. A
`page_model_factory` callback is the seam for future mixed/prefix models. Root
controls whole arrival constructor/bound switches. No production saved plan or
cohort-id tuning is used. An additional deadline check precedes first-fit, but
model construction and pricing still have cooperative atomic deadline limits.

Regression coverage preserves 19 original arrival-bound tests and ten prefix,
explicit-discharge, voluntary-restow, direct-shift and full-check tiny tests,
plus new scope/invalid-objective checks. The original tiny direct shift saves
one counted current restow; construction result alone remains replay evidence.
No numerical run was launched by this agent while the leader reserved the CPU.

`mask.py` and `screen.py` are copied from `lab/c2/c2-gates/`, along with the
screen safety suite and standalone mask differential harness. The prefix mask
uses native fallback outside the measured unchanged-instance, native-engine,
legacy pure-load domain with empty, closed arrival and no future loads or
forecasts. Instance and table identities are also pinned; unsupported families
use native fallback. `bind(problem)` patches only process-local search
aliases for one planning call, restores them even on exceptions, and uses the
saved native callable for fallback. Neither checker nor rule functions change.

The diagnostic screen is fail-only. `ladder_verdict` preserves the Cycle 1 scout
learning predicates and source pins, including both copied engine scout
consumers. The layout consumer uses the same visibility/securing/E6/bow-down,
trim/draft and all-iscode flags; its bow-down arm supersedes the trim/draft
arm, so stability saturation preserves every decision. A consumer with different learning flags
must use full checking until equivalence is established. Possible passes,
freeze-v1, stubs, unknown or changed diagnostic sources use the full checker.
The copied source-root calculation was corrected for the engine directory.

The standalone differential can run as `python -m engine.gates.mask_differential`
with Frontier and lab on PYTHONPATH. It never runs during ordinary pytest.

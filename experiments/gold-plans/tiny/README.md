# Tiny gold pipeline (6.1)

`driver.py` admits available tiny shards from an explicitly selected frozen split
release. This tiny runner selects train only; it checks inventory membership and the claimed train label
before opening each shard; policy hash assignment alone is insufficient. Input
SHA256 and specification identity are checked again after loading. Every output
and resume identity records `split_version` and `split_entries_sha256`; changing
the release pins refuses resume.

Production admission requires current full hull geometry, clean recursive
provenance, a D50 voyage declaration and the pinned checker epoch. The exact and
scalable planners receive the released feasibility defaults: D49 necessary C2
and D55 local visibility caps. Exact owns its ballast (`sets_ballast=True`):
missing ballast rejects before direct finalization, including timeout incumbents.
Scalable declares `sets_ballast=False`, so the canonical ballast finisher may
supply it. A timeout incumbent is considered only as a candidate for fresh full
checking and replay; accepting it never claims an optimality certificate.

The full checker evaluates departure and derived arrival. Admission requires
both stability/strength endpoint parts, all canonical shipping evidence through
`PlannerResult.shippable`, and `report_matches_inputs` content linkage to the
exact structured plan. Raw planner `shippable` or `mask_verified` metrics cannot
authorize publication. The action-space tag comes from the current released
space rather than a hardcoded older version.

**Every candidate must replay through the production CPU `MaskedDecoder`, using
the real local Glimmer tokenizer, canonical action tokens and the end-of-plan
marker. There is no tiny mask exception.** The tiny G1 exemption only waives the
campaign rule-freeze gate. Admission additionally checks that the replay engine
actually reports C2 on. Mask 1.2's temperature changes behavior probabilities,
not allowed token sets; replay for admission performs no model inference or
probability calculation. No weights, GPU, cloud or remote API are used.

Replay is a separately bounded phase. Rejected tokens, construction/backend
exceptions, absent/stale qualification, incomplete replay and deadlines produce
an explicit failed `mask_replay` diagnostic and `mask_verified=False`. Error type
and message are retained; none are silently waived. C2-off smoke mode is always
labelled diagnostic and cannot publish production gold under `plans/` or the
production manifest. It keeps every local hard check, the ballast finisher and
the complete D50 checker active.

Only a fresh eligible result with full checking and completed replay can become
an atomic gold envelope. Failed/tainted attempts remain diagnostic state. Gold
records include the source planner, full report, structured plan, checked
objective, solve/phase times, bound and explicitly scoped gap metadata. Exact
`OPTIMAL` is a D51 engine-domain weighted-lift certificate, not a guarantee of
unrestricted physical or tertiary-margin optimality. A missing comparator is an
unavailable comparison, not a fabricated numerical gap.

Relevant implementation, parameter, split and tokenizer fingerprints are recorded
before/after phases. Source changes reject admission; workers are replaced after
each shard. Resume additionally verifies saved input and output hashes. Every
phase is clipped by the absolute hard stop with cleanup reserve, including mask
replay. Explicit stop requests prevent new work while in-flight bounded cases
checkpoint. Worker counts and budgets must follow fleet admission.

Run `uv run python experiments/gold-plans/tiny/driver.py --help` for the current
release-selection, diagnostic, worker and deadline options. The historic
default October 27 stop times must be overridden deliberately for a new admitted
run. No full campaign is authorized by this repair: it still needs G1 and a
source freeze. See the fix log for current bounded evidence and artifact paths.
The full train/validation/test campaign runner and campaign evidence remain
deferred behind G1 and the source freeze; this repair does not claim them complete.

## Historical pilot artifacts

The checked-in original watcher/results describe an earlier epoch: train-only,
checker 1.3, zero eligible inputs, zero processed instances and zero gold plans.
The former mask-exception claim was incorrect and is superseded above. Its
405 upstream rejected requests were not processed solver instances. Original
watcher PID 53439 stopped cleanly via its own stop flag at 22:03:40 UTC; no claim
is made that this historic process is currently running. Original mocked tests,
warm-up and full-suite logs are historical verification, not a current-epoch
positive gold witness.

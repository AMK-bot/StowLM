# C2 yield diagnostic — sampled evidence, campaign still running

Inspection on 2026-10-27, approximately 21:49 UTC. This is a diagnosis of the
current qualified linear screen, **not a proof of true physical infeasibility**.
No source, canonical shard, constraint, margin or cargo fact was changed.

The inspected epoch is campaign 3.1.0, linear-model 3.4 and background condition
`c3-fixed-background-5`, with admitted physics fingerprint
`2497bc54df98534644eee1b1e3da469bdda74068599f89ef076b4563354ab7ae`.
Whole-fleet cache warm qualification passed with exit 0 in 34.303888167 s
(`c2-qualification.json`). Qualification establishes model availability; it does
not promise that any particular booking passes C2.

The source fingerprint does not include `linear/margin_policy_data.json`.
A supplemental experiment pin was therefore captured **after main start**, at
2026-10-27T21:51:56.978374 UTC: bank SHA-256
`509a7ea96711c6941889e7d9207d1b2addd1ec8ddb8858c38aff7fe6610918fe`.
`policy-bank-pin.json` records the observed mtime (21:01:51.865982 UTC), capture
time, qualified warm log checksum and 15 per-geometry margin-policy certificate
hashes parsed from its JSON lines. The earlier mtime is supporting evidence
only: neither it nor this late content snapshot proves that every prior
candidate used unchanged bank bytes. Certificate hashes identify individual
qualified policies, not the checksum of the whole bank. The runner has been
asked to check the supplemental bank hash periodically and before finalization;
final verification checks it again.

## Observed inventory snapshot

At the sampled manifest snapshot, random-yard tiny had **253 rejected requests,
zero accepted**, each with a cap of 20 candidate attempts: 5,060 attempts total.
Its completion counters were `c2: 4246`, `c1a: 716`, `c1g: 713`, `c1h: 716`.
Overlapping C1 counters must not be added as disjoint failures. There were no
background-balancing rejections in that group. Sampled audits explicitly recorded
`c2`, rather than `checker.missing_vessel_data`. They retained six targets and six
physical cells. The earlier simulated-yard tiny pilot also accepted zero of five
requests, with 24 C2 failures across 25 attempts.

These are in-progress observations, not final campaign totals or a statistical
yield estimate for every other profile. The zero tiny yield is a material risk
for the intended solver corpus: rejected request audits are not solver inputs.
Increasing a rejection cap does not by itself establish that the small decision
region can satisfy the current global bounds.

## Independent single-candidate check

One isolated, single-thread process drew the C1-only random-yard candidate for
tiny, seed `100000000`, feeder_500, laem_chabang_like, noise 0, DG enabled. This
was an in-memory diagnostic; it saved no instance or audit. The first candidate
passed every C1 check. It had six targets, six cells and 95,327.517992 kg of
planned cargo. A newly constructed engine used `boxslot`, `lookahead=0`, and
`extra={"global_screen": 1}`. Its diagnostics reported global screen `on`, one
root LP call, zero unresolved slot LPs; completion returned every C1 key true
and C2 false. Thus the failure was not an unavailable-model fallback, next-action
search, slot-LP budget exhaustion, or a single-shipment probe.

The slice initializer reported `target_reached: true`, departure mass
9,368,368.213288 kg against design displacement 9,713,353.260589 kg, expected
even-keel mean draught 6.852602 m and estimated solid GM 1.8 m. These initializer
targets are not checker approval and do not certify the linear error margins.

The diagnostic reconstructed a plain feasibility LP from the engine's own
`GlobalScreen` matrices, **omitting its centering objective and slack variable**:

1. Obtain `(fixed, free, R)` from the root state; `R=95327.517992` kg.
2. Use columns `[A[:, free], At]`, constants `A @ fixed + const`, and the finite
   upper/lower bounds returned by `_rows`.
3. Constrain the six free cell masses to sum to `R`, each between zero and
   `u0=27317.387415` kg; retain all original adjustable-tank bounds.
4. Solve with `scipy.optimize.linprog(method="highs", options={"threads": 1})`.

This LP had 1,334 inequality rows and 24 variables. HiGHS returned status 2,
`Infeasible`, independently of the campaign wrapper and reference-centering
objective. Basic remaining mass was below the sum of the cell upper bounds.

For diagnosis only, a second LP added a nonnegative slack to every normalized
inequality and minimized their sum. Row scale was
`max(1, max(abs(row coefficients))*max(R,1), abs(rhs))`; slacks below are converted
back to the original row units. Its returned solution required:

| Qualified LP row | Required upper-bound slack in that elastic solution |
|---|---:|
| `draught_aft` | 0.463930676671 m |
| `draught_forward` | 0.045391276521 m |

The slack allocation is an elastic diagnostic solution, not a unique physical
certificate or permission to relax either limit. Solving each row in isolation
found no individually impossible row; the contradiction is joint. The endpoint
draught rows in `linear/operations.py` include the qualified one-sided error
margins before C2 receives them. No margin was disabled in this investigation.

The exact diagnostic method was executed with
`UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python -`
and the procedure above in a temporary stdin script. Wall time was
19.125755875 s; engine construction accounted for 12.632303709 s and its root LP
for 0.016900500 s. All LP calls requested one native thread. Peak RSS was not
measured; no GPU or paid service was used.

## Interpretation and limits

No campaign wiring bug was found in this sample. A C1-passing booking really is
infeasible under the currently qualified C2 linear constraints; removing the
reference-centering objective does not make it pass. The diagnostic points to
the combined endpoint-draught bounds and their error margins, with limited cargo
control in a six-cell slice, despite a successful background initializer.

This does **not** prove that the nonlinear full checker would reject every real
completion, that all 20 candidates of the request fail for identical LP rows, or
that all remaining tiny requests have zero probability of acceptance. Conservative
linear constraints can reject physically feasible bookings. Reviewing the
background operating point and the qualified endpoint-draught envelope belongs
to the fleet/linear owners; this campaign must preserve the current hard rules
and report its actual accepted yield separately from processed requests.

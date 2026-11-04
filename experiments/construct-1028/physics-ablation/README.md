# Constructive physics adapter and diagnostic findings

Implemented in `src/stowage/solver/construct_physics.py`; production module frozen after the 12-test pass below.

## Model and scope

A binary assignment variable is a switch: `x[box,slot]=1` selects one complete D18 placement for that real box. A long box still has one switch; its coefficient contributes half its mass to each physical cell. Adjustable departure ballast masses are continuous variables between zero and each instance tank's declared capacity. The same variables serve all three D57 states. Consumables use the model's proportional departure/midpoint/arrival fractions; ballast never changes during the voyage.

Linearisation means replacing local nonlinear hull responses with sums of cargo/tank masses times stored influence coefficients. This adapter copies every row returned by `build_linear_model` / `linear_constraints(direction='conservative')`: GM, signed trim, LIST, displacement, cargo mass, tank windows, mean/end draught, and every station's SF/BM. Retained cargo is a constant; D42 aggregate fixed loads already belong to source-model constants and are never counted twice. The source owns instance limits, calibration, D49 margins and domain restrictions. No source margin was weakened or omitted. Departure retains current-port limits; midpoint and arrival use the checker's seagoing context.

APPROXIMATION: these 3.7 physics coefficients and reserves are empirical and local. D57 checks three voyage samples rather than the full continuous voyage. A construction passing this model still requires the full checker.

APPROXIMATION: D56 upper GM uses zero free-surface correction and the source absolute point-error guard, with the instance's evidenced design-GM limit. Using maximum FSM for an upper-GM bound would reverse conservatism. The separate source minimum-GM rows retain maximum FSM.

APPROXIMATION: planned-column securing uses D54's worst-motion construction at design GM and a shallow draught. Positive equivalent joint loads satisfy `Q=.45*F_bottom+.5*W_bottom+sum_upper(F+W)`. Passive rod horizontal reaction and absolute frame racking are bounded by Q; rod vertical reaction is bounded by Q times the maximum anchor slope. Since both moments are nonnegative, `abs(M-R*z) <= max(M,R*z)` gives two linear corner-load rows. Triangle inequalities bound fitting shear; hold vertical loads and fitting forces have separate rows. Physical mass/height ratings are enforced by the assignment model. External fixed-background columns remain checker-only. The proxy is a conservative envelope for the declared synthetic design, not an independent class approval or complete background securing model.

The 30500 kg gross rating, IRS racking/post limits and fitting derating are immutable checker literals, not missing per-instance operating limits. SWLs use exported checker constants. Source comments identify `_check_lashing` as the owner of the remaining literals.

Necessary D49 rows are exposed but **never authorize an instance-infeasibility certificate**: the source explicitly calls their envelopes empirical, and local validity restrictions can exclude valid exact conditions. Removing constraints gives a larger feasible set (a relaxation); only infeasibility of a sound relaxation rules out the original problem. The separate assignment/capacity certificates are owned by `construct.py`.

Sources: Pacino, Delgado, Jensen & Bebbington (2011), *Fast generation of near-optimal plans for eco-efficient stowage of large container vessels*; [OR-Tools MIP documentation](https://developers.google.com/optimization/mip/mip_example); existing 3.7 linear-model, D57 voyage and `checker/lashing.py` source contracts.

## Verified findings

The label “small025” in early coordination meant the diagnostic seed, not saved request 025. The actual instance is `v3.random_yard.small.110380001`, **feeder_800.small**, saved request 032. All JSON artifacts carry its real instance ID.

- Removing both new securing proxies still gives SCIP INFEASIBLE on this small instance (7.43 s solve, 9.12 s build). Therefore the failure does not depend on the proxy.
- The assignment owner independently solved that same assignment-only model and passed the static checker. Individual source-family MIPs lacked an incumbent in their 10 s diagnostic budgets; those runs are inconclusive.
- A saved, full-checker-passing tiny000 plan satisfies every assignment row (integration-owner witness). After the sound bottom-joint refinement, it violates **zero securing-proxy rows** and five conservative source rows: arrival trim and departure/midpoint endpoint draught.
- A continuous phase-I diagnostic relaxes integrality and adds nonnegative slack only to source rows. Its objective is the sum of normalized row violations, not a physical score. Tiny000 still needs positive slack 0.0742497597: departure trim upper 0.0535820902 and aft-draught upper 0.0206676696. The small instance needs 0.0201510930, entirely departure aft-draught upper. Thus even the continuous conservative source model is empty on these cases. This is a **model gap**, not evidence that either instance is infeasible.
- Tiny source margins turn manual trim `[-1.078,+1.078] m` into predicted trim `[+0.00896699,+0.60998757] m`, while departure aft draught is capped at `7.05-0.21793143=6.83206857 m`.
- Small source margins turn manual trim `[-1.2441,+1.2441] m` into predicted trim `[+0.02800246,+0.86565041] m`; departure aft draught is capped at `8.7-0.17362574=8.52637426 m`.
- These diagnostics are source-model observations, with no physical infeasibility claim. They were not admitted as campaign success evidence. Concurrent source changes are handled by the root campaign's source-epoch records. Initial diagnostic scripts used existing numerical cache hits; later phase-I/source-row runs redirect all cache writes into this scoped directory.

The `source-rows.json` artifact preserves source limits, bands, constants and reserves. The `*-source-phase1.json` artifacts preserve the normalized slack witnesses. Multiplying slacks back by their complete-row scales gives approximately 0.07368 m trim and 0.14463 m aft draught for tiny, and 0.17182 m aft draught for small; those are **affine model violations**, not exact physical violations.

## Verification and compute

Command: `UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run pytest tests/solver/test_construct_physics.py`

Result: **12 passed in 0.52 s**, including direct comparison of proxy-accepted one/two-tier deck chains against the checker's separate IRS elastic spring calculation, three-state substitution, long-box mass sharing, fixed/discharged cargo, necessary-direction certificate refusal, fixed-consumable draw-down, budget expiry and duplicate station-name preservation. Integration independently reported the earlier full solver suite at 753 passed before the last proxy refinement; root owns the final full-suite run.

All physics diagnostics/tests used one CPU core, single-threaded BLAS, no GPU/cloud. Four SCIP ablations: estimate ~50 s, actual 36.85 s summed wall; row-family diagnostics: estimate ~1 min, actual ~78 s including model build; final source-only phase-I pair: estimate ~30 s, actual 17.74 s; tests <1 s. Peak RAM was not instrumented for these sub-agent jobs; root owns the running reservation and campaign memory measurements. No diagnostic was an accepted stow or an instance-infeasibility certificate.

## Authorized follow-up: necessary candidates and slow fixtures

The leader subsequently authorized **necessary-direction construction as the default**, retaining explicit conservative mode. The default now copies all three states' independently qualified outward D49 rows and shared ballast variables, but includes neither conservative D56 upper-GM nor force-envelope rows. These optimistic rows only propose candidates: exact finishing, the full checker and lazy rejection/cuts still decide acceptance. An infeasible empirical necessary model still never proves instance infeasibility. Metadata reports only approximations actually enabled in the chosen mode. Historical ablation scripts explicitly request conservative mode, so their recorded interpretation remains reproducible.

Reverification after the default change: **12 physics tests passed in 0.59 s**; the default-mode test verifies all three source calls use `necessary`, D56 proxies are absent and certificate eligibility remains false.

Two explicitly authorized slow fixture fixes in `tests/solver/test_ballast_integration.py`:

- Feeder500's synthetic balancing mass at z=4.5 m produced actual GM 3.298169 m, above the unchanged D56 design limit 3.211141 m. Moving that declared synthetic mass to z=5.3 m yields GM 2.822969 m at the same actual 5.5 m draught. Its explicit silhouette remains submerged (top 5.4 m), and there is room for trim-correcting low ballast. No operational or checker limit changes.
- The signed-trim correction fixture now tightens `iscode.loading.trim` through the supported instance override contract instead of mutating canonical fleet hull limits/provenance. The test still requires an initially failing exact trim and a checker-passing corrected condition.

Command: `UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run python experiments/construct-1028/physics-ablation/slow_fixtures.py`

Result: **both authorized slow tests passed in 26.03 s**, versus the admitted estimate of <=75 s, one core/~2 GB/no GPU. The runner installs task-local cache paths in both the parent and spawned ballast worker. No random-yard test was edited by this agent. Peak RAM was estimated rather than instrumented.

## Exact cell-mass aggregation and final verification

Global physics rows now use shared continuous `cargo_mass[cell]` variables. Each has an exact equality to the sum of selected box masses divided by their physical footprints. This gathers repeated terms without changing the feasible assignment/ballast set. All three D57 states and the optional conservative upper-GM proxy use these cell masses. Equality definitions remain hard constraints outside the physical-row diagnostic prefix.

Build-only measurements (single core, no MIP solve):

| Instance | Binary choices | Cell-mass variables | Physical rows | Build seconds | Process peak GB |
|---|---:|---:|---:|---:|---:|
| saved tiny000, seed110280000 | 24 | 6 | 2001 | 6.74 | 0.734 |
| actual saved025, seed110380003 | 1568 | 68 | 2001 | 2.13 | 0.786 |
| medium121, seed120482000, partial only | 17866 | 240 | not built | 10.63 | 0.459 |

Small's dense cargo-term upper bound falls from 3,137,568 to 136,068. Medium's qualified physics construction stopped at the unchanged production-policy epoch guard; the partial measurement covers assignment and exact mass equalities only. Its projected <=3 GB complete-model estimate remains unverified. Tiny/small combined build time 8.87 s was below the admitted ~20 s /2 GB estimate; medium partial time and memory were below its <=50 s /3 GB estimate, but **no complete medium physics model was measured**.

Final source rejection messages include phase, water density, instance, vessel and the expected response-identity SHA when qualification already computed one. The SHA is read from the existing exception diagnostic; it never triggers unqualified rebuilding or a qualification bypass. Concurrent numerical-source changes reported by the leader invalidated the necessary-profile bank during follow-up. The model continues to fail closed until the source owner restores qualification.

A separate read-only `construct_audit.py` compares a complete assignment and chosen ballast with a prefix engine's existing C2 matrix, reports exact row residuals and checks source-row alignment. It neither changes a mask nor runs a new LP/checker. Integration's saved025 witness already found the fixed final C2 relaxation feasible and rejected child C1/C2 completion checks passing; its per-action mask attribution is owned by the sequencing agent.

Final quick command: `UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run pytest tests/solver/test_construct_physics.py tests/solver/test_construct_audit.py`

Result: **16 passed in 0.57 s**, including exact cell-mass substitution and error-context preservation. The two authorized slow fixtures remain separately verified at **2 passed in 26.03 s**. Production physics/audit files are frozen after this pass. No version file was changed.

# Evaluation (v3)

Every number in the demo or write-up comes from this file and a reproducible run folder in `experiments/`.
The harness is plan task 5.2.

## What 'good' is measured against

Navis and human-planner performance are unknown. **Frontier 0.9.0 is the industry-practice bar for our own methods on
tiny and small maps (D177, D198); two-step stays as a comparison row.** It is our emulation of published practice, never a
replica of a commercial planner. Medium isn't solved, so this is a small-map bar. The yardsticks, in order:

1. **Exact solver** (ours, tiny instances; small proofs aren't required, D78): proven-optimal move counts only where the status is `OPTIMAL`. `TIME_LIMIT` rows are best-found plans, not optima (D151).
2. **Lower bounds** (plan 2.6) + **scalable solver** (plan 4.7) on full-size instances. The gap to the bound is an
   upper limit on how far from optimal we can be.
3. **Published benchmarks.**
   - Yard part: BRP / pre-marshalling instance sets with known optima (plan 0.4, 3.6).
   - Ship part: DTU / Larsen–Pacino where comparable.
   - Cite LMASS and ITU numbers; don't equate them (different problems).
4. **Frontier 0.9.0 industry-practice emulation**: port-blocked and two-step starts, master plan, yard-aware construction
   and search on our lift objective, followed by the V4 finisher and full checker, no repair. See the results and limits below.
5. **Two-step baseline** (plan 4.8, E10): a pre-loading plan from the cargo details, then a terminal adjustment to the yard,
   with the two steps decided separately. This retains the expert-described split as a row; it is no longer the bar.
6. **Muse Spark zero-shot**: a frontier LLM without our training or mask.
7. **Base Glimmer + mask**, plus random and rule-based chance arms: what the model contributes beyond the mask and search.
8. A planner's review, if the expert can arrange one.

## Instances

- **Stage A:**
  - Synthetic feeders: ~500 / ~800 / ~1,700 TEU (516 / 816 / 1,604 cells), plus sub-ship slices for exact solving.
  - Rotations of 3–5 ports.
  - Yards either from the port-state simulator (with a noise level) or randomly stacked.
- **Splits** (plan 5.3):
  - Fixed before any solving.
  - The test split includes held-out families: a held-out vessel–slice combination (feeder_1700 medium slice; the vessel
    itself appears in training), a held-out port (US West Coast-like) and a held-out yard noise level (0.8). See
    `src/eval/splits.py` and `experiments/splits/v2/README.md`.
- **Report per instance:** vessel TEU, boxes to load, ports, yard noise level.

### How instances were selected (wave 2 logic review, `experiments/review/w2/logic-backgrounds.md`)

- Instances are drawn, then redrawn until the cargo screens (C1, IMDG, displacement), the medium-rung C2 completion screen
  (D58 addendum) and the D58 background gate pass.
  Impossible DG shipments are redrawn one at a time.
- So the emitted instances are **conditioned on these screens**: they are lighter and less DG-dense than the requested
  profile. That's selection bias (Hernán & Robins, *Causal Inference: What If*, ch. 8). Each split gets a table of requested
  vs emitted mass, DG classes, reefer and OOG shares.
- Gross masses are truncated at 30.5 t (D60: 1.1 % of boxes, 15.8 % of flat racks).
- **DG coverage today:** 3 of 15 requested classes (2.2, 3, 8) reach emitted instances, so results don't yet test
  segregation-heavy DG. Traced causes (D59 amendment, `experiments/review/w2/dg-trace.md`): provisions the checker can't
  assess yet (H1, SW2, SW1, SW19, which fail closed), the synthetic vessel service policy (1.4G, 6.2, 7), a class-1 label
  bug, and redraw selection. Every evaluation row reports requested, drawn, redrawn, rejected and emitted counts per DG
  class. The checker-side assessors have landed (H1, SW1, SW2, SW19). Classes 2.1, 2.3, 6.1 and 9 (and 4.1/4.3 with the declared-fact H1
  model) become testable once the generator supplies facts. Each row reports emitted counts per class; a class is called
  "tested" only with emitted boxes. Never tested under the current service policy: 1.x except 1.4S, 4.2, 5.1, 5.2, 6.2, 7.
  - **Coverage table (D70):** per class and per rung, the test instances with at least one admitted box, the instances with a
    binding segregation pair, and the UNs per class (e.g. 4.3 is UN1402 calcium carbide). A class is "tested" only with ≥ 10
    instances (main or DG-stress); a segregation pair only with ≥ 5 binding instances. In the main set, 4.1/4.3 appear in
    tiny/small test instances only rarely, so claims for them come from full rungs and the DG-stress panel. In the main set,
    H1/SW19 use favourable declared scenarios and don't constrain placement. The 1.4S share is a deliberate coverage
    injection (D66), not traffic. The unknown battery special provisions concern packing and documentation, not stowage.
- **Denominators:** every allocated test spec is reported as feasible-witnessed, proven-infeasible, unknown or
  generation-rejected. Pass rates use all allocated specs, and rejected specs are never backfilled (columns:
  allocated / emitted / rejected).
- **Held-out families:** the feeder_1700 × medium combination (not an unseen vessel), the US West Coast-like port and yard
  noise 0.8 (simulated yards only).
  - With 15 specs per family and rung, each pass rate carries a Wilson 95 % interval (Wilson 1927; Agresti & Coull 1998):
    15/15 supports only "≥ 80 %", and 12/15 gives [55 %, 93 %].
- **Stability-binding:** a family is called stability-binding only from the final plans' measured arrival B 2.3 ratio and
  GM margin. The medium 800/1700 backgrounds barely bind (B 2.3 ratio 3.3–3.7), so they test region size and moves more
  than stability.
- **Yard height and port comparability (D62 amendment, user decision 29 Oct):** H is the quay crane's height (Laem
  Chabang-like 8). Each yard has its own labelled height (Laem Chabang-like 5, Singapore-like and US West Coast-like 5, small
  feeder 3), and yard crossing blocks at that height at every port. So the held-out port faces the same yard rule as the
  training ports. The real Laem Chabang yard figure is still open with the expert (Q25). The Singapore-like
  port has no inbound transshipment, so it's modelled as a gateway port with a hub's name (labelled).
- **D62 bucket and per-generator reporting:** random-yard instances include a labelled D62 bucket (default 25 %), built so
  crane crossing binds. They're larger and harder by construction, so metrics are reported for bucket and non-bucket
  instances apart, and per yard generator. D62 binding is counted per family after regeneration (from the move engine's
  observations); a family with none, among instances where crossing was actually probed, is labelled "crossing never binds
  here" rather than given an artificial quota. Ship-side crossing is probed, not built into instances (D69).
- **Relocation policy confound:** the yard simulator, the action expander (StowLM) and the scalable solver each choose yard
  relocation destinations their own way, so part of a move difference between methods can come from that choice rather
  than planning skill. Stated beside every cross-method move claim until they share one policy.
- **Weight-order tolerance sensitivity:** the D61 kg order uses tolerance 0 kg until the expert answers Q24. Before any
  StowLM-vs-two-step move claim is quoted, a paired sensitivity run at 0, 500 and 2,000 kg is reported. The tolerance
  probably moves the gap, because the two-step terminal step swaps boxes within a weight class.
- **Expert preferences (level-4 tie-breaks):** `heavy_on_deck_count` and broken space (`killed_slots`) are reported per
  plan, so the expert's "heavy under deck" and "less broken space" are visible even where they don't change the move
  count. Every planner, StowLM's best-of-N selection included, ranks by moves first and then by the same shared tie-break
  order, so these columns are comparable across methods.
- **What "optimal" means for the exact solver:** proven optimal for the move model. Ballast, in-port (D64) and seagoing
  physics are checked after the search, so each exact row is labelled "optimal for the move model; checker-passed: yes/no".
- **Gold coverage of the new rules:** gold yield is reported per rule-binding bucket (crossing used, direct delivery present,
  kg-tight stacks with adjacent masses within 1 t, DG segregation binding). An under-represented bucket gets gold from the
  D66/D62 coverage instances; no rule is relaxed to raise yield.
- **Two-step terminal (D73):** the terminal swaps within the booked category (weight class, not exact kg) and may adjust
  slots within the carrier's pattern; the number of approved adjustments is reported per row.
- **Where the crane-height rule (D62) binds:** with our fleet's 5 / 5 / 6 on-deck tiers, ship-side crossing can bind only at
  the small feeder port (H = 4). Yard crossing binds at every port's yard at its own yard height (D62 amendment). The tiny exact instances don't
  exercise it, so "optimal on tiny" is measured on instances where D62 never fires. Results are reported separately for
  instances where D62 binds, and generation adds a bucket where it does (D66).
- **Rules implemented but not evaluated:** Laem Chabang-like class-7 refusal (every synthetic DoC already excludes class 7) is
  unit-tested only. Class-1 direct delivery is evaluated once the 1.4S share is in the cargo profile (D66 coverage note).
  Any claim about these rules says which.
- **Mask override rate:** reported at the box-index and slot positions, where the model actually chooses. Opcode-position
  overrides measure format compliance and are reported separately. (The first traces measured the opcode token only; any
  number from them, such as the A/B run's "37 % at action starts", is not a planning statistic.)
- **Own draft vs after repair:** StowLM is reported as "own draft passes" and "passes after repair" separately (D68), never
  pooled with solver success. In the prompt-3.0 A/B (C2 off), 48 of 48 small samples dead-ended, so every small plan there
  came from repair.
- **Unevaluated (internal-error) column per method:** checker numerical failures (e.g. strength equilibrium not converging
  at extreme in-port states) are counted per method and cause, never as physical violations or passes; they cluster in
  late, extreme states, so they correlate with a method's ordering.
- **Berth draught not constrained** until the production ports declare berth depths (D75 note); the only berth failure
  seen so far came from a test fixture.
- **Lift order (D75):** every method's plan passes through the same in-port-aware resequencer before checking; lifts
  moved and list margin before/after are reported per plan.
- **6.2 sample sizes (leader decision 29 Oct, GPU budget):** the base-Glimmer-plus-mask ablation row uses all tiny/small
  test specs, a stratified paired 40 of the 75 medium at best-of-4, and 10 paired full instances; C1 keeps all 75 medium at
  best-of-8. The 6.2 row's medium confidence intervals are wider accordingly, and every 6.2-vs-C1 comparison is paired.
- **What the mask does to the base model (pilot, 29 Oct, pre-freeze fixtures, not a result):** untuned Glimmer's top choice
  was illegal at 55 % of box/slot decisions and 19 % of opcodes. Quote the frozen-epoch figure from 6.2, not this one.
- **What the explainer does (5.6, v3.0):** Muse Spark selects and orders a menu of fixed planner statements, each rendered
  from the one checker fact it counts; it writes no free text, so it can't invent numbers, boxes or claims. Describe it as
  "Muse Spark selects and orders checker-grounded statements", never as Spark analysing the plan.
- **4-bit fidelity at long prompts (0.1; review `experiments/review/w2/w2-rev-calib.md`):** Against an independent PyTorch bf16 reference, on 3 prompt-3.0 instances of 4.7k–14.0k tokens with format-valid action continuations, 4-bit mean KL is 0.027 / 0.041 / 0.030 nats/token. That is the same magnitude as the short-prompt calibration (0.0754 averaged); the 0.1376 threshold was not re-calibrated at these lengths. MLX bf16 matches PyTorch bf16 to ≤ 0.0015. Full-ship prompts above 14k are untested. The
  action-token agreement figure isn't quoted: its continuations were easy by construction. Known-bad variants (3-bit, layer swap) at one long length: done below; also open: real solver-plan continuations, a DG-dense feeder_1700 prompt and 17–22k tokens. Known-bad check (`long-prompts/separation.json`): at ~7.3k tokens the 4-bit model's mean KL is 0.041, about 2× below a
  layer-swapped 4-bit control (0.084) and 5× below 3-bit (0.198), so the ranking holds; but the 0.1376 threshold would accept
  the swapped control at this length, so it is not a full-length acceptance test until more full-length prompts calibrate a
  length-specific threshold.
  Still open: a length-specific threshold, real solver continuations, a DG-dense feeder_1700 prompt, 17–22k tokens.
- **Sample budget on every row (D76):** each row states k. StowLM: pass@1 beside the checker-selected best of 8. Muse
  Spark: one sample, compared with StowLM's pass@1, given a rules-and-limits appendix (a compact-only subset is the
  labelled information ablation); own plans only. Medium/full Spark results are worded as a single-response API limit
  under that prompt and setting.
- **Ballast delay (D74, user decision):** hours by which the ballast change exceeds the crane time, max(0, P/(ρ·R) − T(k)),
  reported per plan and per method beside moves; it never rejects a plan. The HEEL-rate and pumping-window checks are hard.
- **Mask time limit:** a per-instance time-limit hit is its own outcome column, not a model failure.
- **Timeouts and unevaluated checks:** a checker timeout or an unevaluated part is never a pass. It's reported in its own
  column, not as a rule violation.

### Checker latency (checker 2.2, M5 Max, one BLAS thread; `experiments/checker/perf-1028/`)

| Case | feeder_500 | feeder_800 | feeder_1700 |
|---|---|---|---|
| Warm, identical plan re-checked (median) | 0.45 s | 0.65 s | 0.85 s |
| Warm, novel plan (the RL/eval case; 2 probes, not benchmarked) | ≈ 1.0 s | ≈ 1.5 s | not measured |
| Cold, fresh process (median) | 5.9 s | 15.5 s | 6.7 s |

The ≤ 0.6 s target is met only for feeder_500 in the warm-identical case. Cache transparency ("result-identical") is shown
on an 84-pair corpus: 72 failing, 12 passing, no passing generated plan, no DG-segregation case. It'll be extended before
the freeze. The cold-timeout fix was verified on 1 of 11 timed-out instances; all 11 are re-run before it's claimed.

## Planning metrics (per method × instance size)

| Metric | Target (provisional; set after plan 5.4) |
|---|---|
| Hard-rule pass rate (full checker, shipped plans) | **100%**, non-negotiable |
| Shut-outs (boxes on the load list left behind) | 0 on feasible instances |
| Total moves; non-productive moves by type (yard rehandles, reported also as **avoidable** rehandles = total − the yard lower bound (D72); restows now, restows at later ports, hatch-cover moves) | Close to the proven optimum on tiny instances and to frontier 0.9.0 on small ones; retain two-step as a row. Medium/full remain unsolved; report checked plans and lower bounds without an industry-level claim. |
| Gap to lower bound / to exact | Report both `(C+F) − L` and `C − L`. A Stage A ship arrives empty, so there's no arrival restow term, and the bound's future terms are 0: on Stage A the bound counts only forced hatch-cover work (the forced-hold component, `experiments/review/w2/logic-bounds.md`). On the whole D58 pilot it was 0 before that component. Stage A gaps to L are loose by construction, so the main Stage A yardsticks are the exact solver (tiny, where proven) and the best known plan. When L = 0, report "no certificate", not a gap. Averages state how many cases were undefined. |
| Stability and strength margins (GM above minimum, max SF/BM % of limit) | Within limits; tie-break only |
| Time to plan | Seconds to ~1 min (vs solver minutes) |
| Mask dead-end rate; repair fallback rate | Report honestly |

How to read the rows (wave 2 logic review, `experiments/review/w2/logic-drivers.md`):
- Gold and solver rows are conditional on generator-accepted instances, and gold on solver success. Each row reports
  n accepted / n allocated and the yield per family.
- A solver's success is "checker-passed / attempted". A timeout counts as attempted, not as missing.
- **StowLM method label:** "StowLM (LoRA on 4-bit Muse Glimmer 30B), checker-selected best of 8 at T = 1". The 6.2 base
  row is the same command without the adapter ("4-bit base Glimmer + mask"). Columns: Own pass % [Wilson] | Shipped pass % |
  Fallback %. The full rung uses 20 test specs stratified by ship × generator (`experiments/eval-c1`).
- **4-bit conversion (task 0.1):** against an independent PyTorch bf16 reference, the shipped 4-bit model has mean KL
  0.075 nats/token and top-1 agreement 76–100 % on 6 prompts of ≤ 573 tokens (`experiments/glimmer-bench/calibration/`).
  The tolerance (mean KL ≤ 0.138) sits between q4 and the rejected controls, so it shows q4 is close to bf16, not that it
  is "faithful"; full-ship prompts (~17k tokens) are untested.
- **StowLM rows report two populations (D68).** *Own plan*: the model's plan passed the full checker without repair.
  *Shipped*: the delivered plan passed, solver repair included. Model-skill claims and paired move differences against
  baselines use own plans; both are shown, with the fallback rate.
- Feasibility status and "stability-binding" are properties of the instance, taken over all methods. Stability-binding
  means the tightest stability criterion in the best passing plan is within 10 % of its limit (a chosen threshold).
- Pass rates separate generation rejections, specs not attempted and method failures.
- Cross-method move claims ("A uses fewer moves than B") come only from **paired** comparisons on instances both methods
  pass, with n pairs and n excluded printed. A per-row mean over each method's own passing plans can't be compared
  across rows: a method that fails the hard instances would look better (survivorship bias).

| Date | Method | Instances | Pass % | Moves (non-prod.) | Gap to LB | Gap to solver | Time | Fallback | Run |
|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | |

## Stage B–D metrics (when built)

- **B (layover):** the same metrics. Also: restows of boxes already on board, and inherited soft-rule violations
  (count; tolerated per the catalogue).
- **C (port optimisation):** total moves over a simulated horizon (many ships, seeds) against the simulator's default
  "good enough" policy. Pre-marshalling moves included.
- **D (planning for a layover):** total moves over both load ports and discharge, against planning each port
  separately.

**How to read a lower bound (plain words):** a lower bound L is a proved minimum: no plan can use fewer moves. Our plan's
cost C and L bracket the optimum, which lies somewhere between them. The gap C − L is how much improvement *might* remain,
not how far we are from optimal; where L is weak, the gap mostly measures the bound. On tiny instances we compare with
the exact solver's proven optimum where it finished (`OPTIMAL`). Small instances have no proven optimum, so there we compare
with frontier 0.9.0 as the small-map bar, alongside the best known checked plan and the retained two-step row.

## Ablations (the technical-depth story)

- StowLM: SFT, SFT + mask, SFT + GRPO + mask; base Glimmer + mask vs fine-tuned.
- Mask on vs off: what the feasibility mask buys (pass rate, dead ends).
- Fine-tuned Glimmer 30B vs Muse Spark zero-shot.
- Generalisation: held-out vessel–slice combination / port / noise level (not an unseen vessel).

## Parked (D12)

The booking LLM (field extraction, hidden-DG recall/calibration) is not evaluated unless it returns.

## Instructions reader (beside stage D, D13)

Metrics:
- schema-valid rate (100% by construction)
- intent accuracy on held-out instructions, including the expert's real phrasings
- share of replans that pass the checker and honour the instruction

### Unofficial pilot rows (27 Oct; not results — C2 off or tiny-run stubs; for planning only)

- **6.2 base Glimmer + mask, pilot** (stow-gpu, `experiments/base-mask/`): best-of-32 at T = 1 on 12 tiny + 12 small val instances
  from the v3 generators, C2 off, no ballast → checker pass 0/24. 19/24 instances had ≥ 1 complete sample; sample completion
  82 % (tiny) / 21 % (small). D11 objective vs the scalable solver: tiny 120 vs 136–146 (base best-of-32 better on 7/10), small
  460 vs 323. Generation 15.9 s (tiny) / 64 s (small) per instance at batch 32; host share 2–6 %.
- **Gold-plan quality check**: exact optimum vs scalable on a pinned tiny instance: 6 vs 12 lifts; after exact-window LNS the scalable
  solver matches (6). Fresh tiny: scalable mean excess over optimum 16.7 % (historical C2-off diagnostic, n = 3, against
  prior-source same-input proofs; no independently proved optima for the fix-wave revision, so this is not current production
  quality or approved gold evidence).

### First official rows (27 Oct; tiny samples — not quality claims)

- **6.2 base Glimmer 4-bit + mask, official path** (stow-gpu, `experiments/base-mask/`, README "official" section): best-of-32 at
  T = 1, C2 on, no stub, `tiny_run=False`, through `run_planner` (finalize → check → repair), on 6 C2-screened small v3-generator val
  instances. **Checker pass 2/6** (scalable solver 1/6); complete plans 5/6 (12.5 % of samples complete); D11 objective 44 and 67
  on its two passing plans (lower bounds 4 and 0); where both completed, scalable was lower (40 vs 44; 36 vs 44, neither ballasted).
  85–218 s per instance including finalize/check/repair; mask host share 52–62 % with C2 on (2–6 % with C2 off).
- **Tiny rung:** 0/29 tiny val specs pass the C2 screen under current physics (draught/trim reserve conflict,
  `experiments/instances/v3-tiny-diagnosis.md`), so there is no tiny row.

### Public yard benchmarks (4.10, fix wave 27 Oct)

- **Instances.** All 12,500 public Zhu instances load. Their canonical stack layouts match the independent Bacci Group 6 copy.
- **Bounds.** Both tested lower bounds stay below all 12,408 certified restricted optima; 92 cases have mapping-only coverage.
- **Published solution.** Bacci's Figure 1 reproduces all nine simulator events: 3 relocations and 6 productive retrievals.
  The free-order engine bound is zero; the ordered LB1 is separate.
- **Limits.** No claim of unrestricted optimality or ship safety follows.
- **Cost.** The whole evidence workflow takes 16.6 s and 80 MiB on one CPU (`experiments/review/fixes/fix-t4-bench.md`).

### Voyage-condition columns (from the fix wave, D50)

New evaluation rows add `Departure pass | Arrival pass | Condition failures`.
- **What they summarise.** Stability and strength at D50's two declared endpoints: departure, and arrival with consumables
  drawn down.
- **What they don't certify.** Unsearched intermediate conditions, or an unknown next berth.
- **Rows recorded before the fix wave** checked departure only, and are marked so.

## v4 MVP results (D78/D79: rule-freeze switch off, C2 off; the full checker gates every plan)

### Frontier 0.9.0: the small-map bar (30–31 Oct; D177, D198)

Source: `experiments/mvp/frontier/RESULTS.md` (a working note, not in the public repo), D192 (01:50) and D193 (05:55).
**Quote the blind run first:** on 30 small test + unseen maps, **0.8.1 used 39 % fewer lifts than same-run two-step**:
14.28 vs 23.40 on 25 mutual maps (95 % −47.3 to −30.3). **0.9.0 measures 44 % fewer on a repeat run**:
13.59 vs 24.19 on 27 mutual maps (−43.8 %, 95 % −51.3 to −35.7), with passes 28 vs 27.

**Limits that travel with those figures:** this is an **emulation, never a replica**; Navis and human performance remain unknown.
Small means **12–22 boxes, 2 discharge ports and 2 hatch covers** (13–22 boxes on the 30 headline maps), so one port per cover
fits exactly. Port blocking with compaction supplies most of the saving; **search adds at most 4.4 % on the pooled test + unseen
comparison**. The frontier **can't lose to a start finished in the same run**, by design; the floor disappears if that start's finish
fails. It takes **about 9 to 10 times the wall time** of stored two-step. On test, **most of the saving is projected later-port lifts**
(8.1 of 10.0 lifts saved; this port −17 %, later ports −67 %), under D15's fixed-coordinate/no-future-load projection and the
provisional 2 lifts per restowed box. **Medium isn't solved: this is a small-map bar.** Validation below is **development data**,
not an unseen claim. These limits apply to every frontier table here and must accompany any extracted claim.

The emulation uses two-step and port-blocked starts, a master plan, yard-aware construction and search on our objective, then the
same V4 finisher and full checker with no repair. Final version 0.9.0, seed 0: 300 s finisher budget per start, then up to 3 plans;
305–357 s mean per small map across sets (630 s max). Tiny maps have 3–4 boxes. Lifts are weighted non-productive lifts now and
at projected later ports. Intervals are 95 % paired bootstrap over maps; "mutual" means both arms passed.

**Blind 0.8.1, seed 0, against same-run two-step:**

| Set | Passes | Mutual | Mean lifts | Change (95 %) | Fewer / equal / more |
|---|---:|---:|---:|---:|---:|
| Test v2.1 tiny, 15 | 15 vs 15 | 15 | 5.93 vs 6.47 | −8.2 % (−14.4 to −2.2) | 5 / 10 / 0 |
| Test v2.1 small, 15 | 13 vs 13 | 13 | 13.77 vs 22.23 | −38.1 % (−49.4 to −25.8) | 12 / 1 / 0 |
| Unseen small, 15 | 13 vs 12 | 12 | 14.83 vs 24.67 | −39.9 % (−51.4 to −26.7) | 10 / 2 / 0 |
| **Test + unseen small, 30** | **26 vs 25** | **25** | **14.28 vs 23.40** | **−39.0 % (−47.3 to −30.3)** | **22 / 3 / 0** |

**Current bar, 0.9.0, seed 0; frontier first in each comparison:**

| Set | Comparison arm | Passes | Mutual | Mean lifts | Change (95 %) | Fewer / equal / more |
|---|---|---:|---:|---:|---:|---:|
| Test v2.1 tiny, 15 | Two-step, same run | 15 vs 15 | 15 | 5.93 vs 6.47 | −8.2 % (−14.4 to −2.2) | 5 / 10 / 0 |
| Test v2.1 tiny, 15 | StowLM 8.2 | 15 vs 15 | 15 | 5.93 vs 7.33 | −19.1 % (−28.0 to −9.0) | 8 / 7 / 0 |
| Test v2.1 small, 15 | Two-step, same run / stored | 14 vs 14 | 14 | 12.86 vs 22.86 | −43.8 % (−53.5 to −33.8) | 14 / 0 / 0 |
| Test v2.1 small, 15 | Block-rule, same run | 14 vs 11 | 11 | 13.82 vs 15.00 | −7.9 % (−17.3 to −1.7) | 4 / 7 / 0 |
| Test v2.1 small, 15 | StowLM 8.2 | 14 vs 14 | 14 | 12.86 vs 34.36 | −62.6 % (−71.9 to −51.3) | 14 / 0 / 0 |
| Unseen small, 15 | Two-step, same run | 14 vs 13 | 13 | 14.38 vs 25.62 | −43.8 % (−54.9 to −31.1) | 11 / 2 / 0 |
| Unseen small, 15 | Block-rule, same run | 14 vs 11 | 11 | 14.91 vs 15.91 | −6.3 % (−13.8 to −1.0) | 4 / 7 / 0 |
| **Test + unseen small, 30** | **Two-step, same run** | **28 vs 27** | **27** | **13.59 vs 24.19** | **−43.8 % (−51.3 to −35.7)** | **25 / 2 / 0** |
| Test + unseen small, 30 | Block-rule, same run | 28 vs 22 | 22 | 14.36 vs 15.45 | −7.1 % (−12.6 to −2.7) | 8 / 14 / 0 |
| Validation small, 55 (**development**) | Two-step, same run / stored | 51 vs 50 | 50 | 11.86 vs 24.58 | −51.7 % (−56.9 to −46.3) | 49 / 1 / 0 |
| Validation small, 55 (**development**) | Block-rule, same run | 51 vs 46 | 46 | 12.04 vs 13.57 | −11.2 % (−15.6 to −7.3) | 25 / 21 / 0 |
| Validation small, 55 (**development**) | StowLM 8.2 | 51 vs 44 | 44 | 11.57 vs 30.43 | −62.0 % (−67.9 to −55.6) | 43 / 1 / 0 |
| Tiny validation, 48 (**diagnostic development data**) | Two-step | 47 vs 47 | 47 | 6.02 vs 6.55 | −8.1 % (−12.0 to −4.4) | 14 / 33 / 0 |
| Tiny validation, 48 (**diagnostic development data**) | StowLM 8.2 | 47 vs 47 | 47 | 6.02 vs 9.40 | −36.0 % (−42.4 to −28.9) | 34 / 13 / 0 |

- **Version exposure.** 0.9.0 is the second full run on test and the third on unseen, after looking at 3 of the 30 maps,
  on a machine about twice as fast; the improvement isn't code alone. No map got worse on the 30, 1 on validation.
  Without the 3 maps: −43.8 %. Seed 1 on the same code: 13.85 vs 24.19 (−42.7 %, 95 % −50.2 to −34.6), same 28 passes.
  No start's cost changed, so the seed tests construction and search only. The 0.8.1 rows have no source hash, and the planner
  changed during that run without a version bump, apparently bookkeeping only. All 148 final seed 0 rows share a planner-source
  hash, which does not cover the checker, finisher or two-step; every stored pass was independently rechecked.
- **Port blocking.** With compaction it supplies 88–92 % of the saving where it finished, and 76 % on the 27 mutual maps
  counting failures as no saving. Everything after the rule adds 7.1 % on 22 maps; search itself at most 4.4 % on that pooled
  comparison. These shares aren't tested with more ports than blocks. The other starts pass 28 of 30 against the rule's 22.
  Two-step ranks port overstow box by box; it lacks the one-port-per-block rule (D193, 05:55).
- **Tiny.** Equal to the proven optimum on 13 of 13 test maps with a proof; two-step alone hits 8. Small has no proven optimum.
- **Finisher and medium.** All 62 gated plans passed. 9 of 340 finishes timed out, all ungated starts; none clears the sight
  and lashing gate at default tanks, against 172 of 173 finished starts. This is a warning: 4 of 13 refused plans finished.
  An intermittent timeout on the same plan is unexplained. Frontier finished neither of 2 current medium maps tried (at 0.8.2);
  two-step and block-rule are 0 of 12. No medium or full-ship industry bar is established.

### Frontier lab, cycle 1 (D228, D229): development evidence, not a headline

Fixed sets `quick` (8 tiny, 10 small, 3 medium) and `medium12` (the 12 D185 maps), validation and TRAIN only, through the full checker. 0.9.0 is the stored row. Medium has no stored 0.9.0 row; V6 is the only stored certificate there. One seed.

| Method | Tiny passes, lifts | Small passes, paired lifts | Small s/map | Medium passes (lifts) | `medium12` passes |
|---|---|---|---:|---|---:|
| 0.9.0 stored | 8/8, 6.38 | 9/10, 13.89 | 305–357 | 1/3 (129, its two-step start), measured since with the E9 bug in place | 0, measured since |
| Stacks as pages, cold (fx-scout) | 8/8, 6.88 | 9/10, 14.00 | 52 | 2/3 (79, 184; V6 131, 214) | 0, 8 complete stows |
| Lazy fusion from stored start (fx-vlsn) | 8/8, 6.38 | 9/10, 13.78 | 32 | no start | not run |
| Class exchange from stored start (fx-fvs) | 8/8, 6.38 | 9/10, 13.78 | 40 | no start | not run |
| Cut refinement from stored start (fx-cuts) | 8/8, 6.38 | 9/10, 13.78 | 53 | 0/3 | 0, no complete stow |
| Inherited-ballast finisher (fx-finish) | 8/8, 6.38 | 9/10, 13.89 | 86 | 1/3 (131) | not run |

Warm-started rows reuse stored plans and ballast, so their times aren't cold planning times. The three 13.78 rows are the same single lift on `sim.small.41100007`.

Bounds on the same maps: hatch-packing lower bound 10.78 mean against 13.89 on 9 maps, proven optimal on 3 (4, 10 and 22 lifts); relocation minimum reached on 17 of 17 plans. `sim.small.40100025` reads 26 against 8 because restows are relaxed, and holds 18 of the 28 lifts of total gap. Medium forced relocations 47, 84 and 24; the first two medium plans attain them.

### Frontier Engine 0.10.0 (D229, D236, D237): benchmark on 45 small and 40 tiny validation maps not used in the lab, plus 8 medium

**Read with D237.** Comparing with stored rows overstates the gain: a fresh 0.9.0 often beats its own stored row, because the stored rows ran on a busier machine. All extra passes are the E9 fix. The same-load result is in the 0.11.0 section below (D239).

Fixed before any engine result (`engine/bench/MANIFEST.json`): 45 validation small maps, 40 validation tiny, 8 medium. Seed 0, 10 workers, one numerical thread each, 43 minutes wall, 5.6 CPU hours, on a frozen copy of the code, with other executives on the machine. Every pass is through the unchanged full checker. 0.9.0 is the stored `val-0.9` row. Not test data.

| Size | Engine passes | 0.9.0 passes | Paired lifts, engine / 0.9.0 | Change (95 %) | W/T/L | Mean s/map |
|---|---:|---:|---|---|---:|---:|
| Tiny | 39/40 | 39/40 | 5.95 / 5.95, 39 pairs | 0 | 0/39/0 | 78 (0.9.0: 125) |
| Small | 45/45 (all 3 new passes look like the E9 fix) | 42/45 | 11.33 / 11.76, 42 pairs | −0.43 lifts, −3.6 % against stored rows (−0.74 to −0.14); about −1.6 % estimated against a fresh 0.9.0 | 8/34/0 | 320 (301) |
| Medium | 3/8 | no stored row | none | | | 312 |

Over the 42 small pairs the saving is 0.33 relocations, 0.05 hatch lifts and 0.05 restows a map, though the logic review reran 5 of the 8 wins: fresh 0.9.0 gets 39 lifts on them against stored 49 and the engine's 34. The −16 % on `quick` shouldn't be used. 37 plans equal a lower bound: fewest lifts among plans the checker passes, counting later ports by the D15 projection (fixed stow, no later loads). 20 are tiny, 17 small. Mean gap on the small maps is 3.6 lifts with the 0.10.0 bound, which reads 0 on many `sim` maps; c2-hatch's bound is much tighter.

Medium is load-sensitive. In this run the engine got 121 on the 88-box map and no plan on the 162-box map, against 79 and 184 on `quick` at 2 workers, because the page ladder runs on wall-clock budgets. The other passes are 70 and 71. Nothing passes on the D185 maps. 0.9.0's `plan_big` on the three `quick_medium` maps, with the E9 bug in place: 129, none, none. The 129 is its two-step start; search added nothing. With the E9 fix the 162-box map gets a 206-lift stow and still no pass. The medium savings are entirely projected later-port lifts, and these maps were picked as ones V6 could pass.

### Frontier Engine 0.11.0 against a fresh 0.9.0 at the same load (D239): the fair comparison

Run together from frozen copies, 09:28–10:32 EST on 1 Nov: engine 0.11.0 against 0.9.0 plus the E9 mask fix, same seed, same machine load, same 85 validation maps (`lab/c1/leader/bench-0.11.0/`). These maps aren't sealed. They're development data.

| Size | Engine passes | Fresh 0.9.0 + E9 passes | Paired lifts, engine / fresh | Change (95 %) | W/T/L | Mean s/map |
|---|---:|---:|---|---|---:|---:|
| Tiny | 39/40 | 39/40 | 5.95 / 5.95, 39 pairs | 0 | 0/39/0 | 58 (fresh 70) |
| Small | 45/45 | 45/45 | 11.18 / 11.40, 45 pairs | −0.22 lifts, −1.9 % (−0.42 to −0.07) | 7/38/0 | 362 (fresh 236) |

Fewest-lifts saving by part, small: 0.18 relocations and 0.04 later restows a map, hatch lifts level. Fresh 0.9.0 + E9 also passes all 45 small maps, which settles it: every extra pass is the E9 mask fix, and the solver's own gain is the 1.9 %. 72 of the 84 passing plans equal the engine's own lower bound (36 tiny, 36 small): fewest lifts among plans the checker passes, counting later ports by the D15 projection, fixed stow and no later loads.

Medium, engine alone, no fresh arm in this run, `--budget-s 900`: 5 of 17 pass, cold. All 5 are D192 60 % fill maps: 88 boxes 71 and 71, 87 boxes 70, 162 boxes 112, 163 boxes 112, in 102–362 s. Every relocation on these 5 plans is forced by the yard: 47, 47, 46, 84 and 80 relocations used, the same 5 numbers forced by the map. So the gap on them is hatch lifts alone (24–32 here). Zero restows. These totals include projected later-port hatch lifts, half of the hatch figure, so the `docs/headline.md` caveat applies, and V6 had passed some of these maps, so they're preselected as passable. Against this, the 12 D185 80 % fill maps: 0 of 12 cold, no complete candidates returned.

The tiny failure, `random.tiny.20000040.0.2`, has no legal plan: all 192 physically supported stows break `expert.e8.away_from_heat` (D240). So the engine passes every validation tiny and small map that has a plan.

The leader rechecked all 5 medium passes and all 7 small wins in a fresh process with the unchanged checker: input hash matches, stored lifts match, all boxes aboard, 0 violations.

### Engine 0.12.0, and the assignment LP (D243, D244): same load, then tiers

All through the full checker, cold, seed 0. Tiny and small maps are validation; medium maps are development data. "Lifts" are projected later-port lifts as well as real ones (D15), on 12–22 boxes, 2 ports and 2 hatch covers on small maps, and most of the saving is projected later-port lifts.

**0.12.0 against a fresh 0.9.0 with the E9 fix, same maps, same load:**

| Set | Maps | Engine passes | Fresh passes | Mean lifts, mutual | Paired | Mean wall |
|---|---:|---:|---:|---:|---|---:|
| Tiny | 40 | 39 | 39 | 5.949 = 5.949 | 0 / 39 / 0 | 67 s vs 75 s |
| Small | 45 | 45 | 45 | 10.96 vs 11.40 | 10 / 35 / 0, −0.44 lifts, −3.9 %, 95 % bootstrap −0.76 to −0.20 | 524 s vs 247 s |
| Medium | 17 | 5 | fresh `plan_big` on 8 of the 17: 2 passes, 129 and 110, none on the 3 D185 maps it tried | 71, 70, 63, 104, 100 | On the 2 mutual maps 71 vs 129, 70 vs 110 | 565–3,637 s |

The one tiny map neither passes has no legal plan (D240). All 12 D185 maps are no-candidate on 0.12.0. Leader rechecked 15 plans in a fresh process: the 10 small wins and the 5 medium passes, all pass at stored lifts with 0 violations.

**Assignment LP with securing rows (`lab/c5/c5-securing/`), cold. A lane for medium ships, not small ones:**

| Set | Maps | Boxes | Full passes | Complete stows | Note |
|---|---:|---:|---:|---:|---|
| Fill 60 % | 12 | 87–163 | 11 | 12 | Development |
| Fill 70 % | 12 | 101–190 | 12 | 12 | Development |
| Fill 80 % | 12 | 116–217 | 5 | 12 | Development |
| D185 | 12 | 121–428 | 0 | 11 | Development, one map no complete stow |
| `feeder_1700` 60 % | 6 | 300–306 | 3 | 6 | Not used in tuning |
| `feeder_1700` 70 % | 6 | 351–357 | 4 | 6 | Not used in tuning |
| `feeder_1700` 80 % | 6 | 401–409 | 0 | 6 | Not used in tuning |

Most passes take 25–80 s on the first 48 maps and 32–184 s on `feeder_1700`. The leader rechecked all 35 passes in a fresh process: every box aboard, 0 violations, stored lifts. On f60 maps the LP spends more lifts than the page engine: 77, 74 and 73 against 71, 70 and 63. It fails all 10 `quick` small maps on draught, so it's for medium ships only. `feeder_1700` lifts are 193–278, relocations mean 164.7. No fresh 0.9.0 arm ran on the `feeder_1700` maps.

**Sealed holdout (D247): 72 new maps, generated for this and run once by the leader, with nothing tuned on them.** New seeds, disjoint from about 22,700 split and metadata files, both yard generators, both mass levels. The LP's source hashes match its freeze.

| Ship | Fill 60 % | Fill 70 % | Fill 80 % | Boxes |
|---|---:|---:|---:|---:|
| `feeder_500` | 8 of 8, 60.4 lifts | 8 of 8, 76.8 | 6 of 8, 81.0 | 82–121 |
| `feeder_800` | 8 of 8, 98.5 | 8 of 8, 105.8 | 4 of 8, 142.5 | 165–223 |
| `feeder_1700` | 6 of 8, 168.3 | 5 of 8, 226.4 | 0 of 8 | 304–408 |
| **All** | 22 of 24 | 21 of 24 | 10 of 24 | |

**53 of 72 full passes, 74 %.** All 72 got a complete legal stow. Median 33 s on a passing map, 124 s mean over all 72. Lifts are means over passing maps only, so they aren't comparable across cells. By mass at 80 % fill: high 2 of 12, low 8 of 12. Leader rechecked all 53 in a fresh process: every box aboard, 0 violations, stored lifts, input hashes match. No 0.9.0 or page-engine arm has run on these maps yet, so this is a pass rate, not a comparison.

**Improved LP on the same 72 sealed maps (D260), one read, frozen hashes, budgets 300 s and 180 s:**

| Ship | Fill 60 % | Fill 70 % | Fill 80 % | First LP total | Improved LP total |
|---|---:|---:|---:|---:|---:|
| `feeder_500` | 8 of 8, 64.4 lifts | 8 of 8, 70.2 | 6 of 8, 85.3 | 22 | 22 |
| `feeder_800` | 8 of 8, 96.5 | 8 of 8, 123.1 | 7 of 8, 166.0 | 20 | 23 |
| `feeder_1700` | 8 of 8, 164.2 | 5 of 8, 214.0 | 0 of 8 | 11 | 13 |
| **All** | 24 of 24 | 21 of 24 | 13 of 24 | 53 | **58** |

**58 of 72 full passes, 81 %,** 7 gained and 2 lost against the first LP. Leader rechecked all 58: every box aboard, 0 violations, stored lifts, input hashes match. On the 51 maps both pass, 111.08 lifts against 108.39: 24 wins, 4 ties, 23 losses, so no gain in lifts. Median 110 s on a passing map, 175 s mean over all 72; the shards ran with other work on the machine. No polisher in this read. No 0.9.0 arm yet; see the final benchmark below.

**Completion by tier, no peel search yet (D242):**

| Set | Maps | T5 | T4 | T3 | T2 | T1 | T0 only | Not completed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.12.0 tiny and small | 85 | 84 | 0 | 0 | 0 | 0 | 0 | 1 |
| 0.12.0 medium | 17 | 5 | 0 | 0 | 0 | 0 | 0 | 12 |
| LP, 48-map ladder | 48 | 28 | 0 | 0 | 0 | 0 | 0 | 20 |
| LP, `feeder_1700` | 18 | 7 | 0 | 0 | 0 | 0 | 0 | 11 |
| First LP, sealed holdout | 72 | 53 | 0 | 0 | 0 | 0 | 0 | 19 |
| Improved LP, sealed holdout | 72 | 58 | 0 | 0 | 0 | 0 | 0 | 14 |
| Improved LP, 48-map ladder | 48 | 36 | 0 | 0 | 0 | 0 | 0 | 12 |
| Peel ladder on the first LP, 18 of 36 maps reached (6 at 70 % fill, 12 D185) | 18 | 6 | 0 | 0 | 0 | 6 | 4 | 2 |
| Engine 0.13.0 with ladder, 18 holdout maps (D261) | 18 | 15 | 0 | 0 | 0 | 1 | 1 | 1 |

In the rows without a ladder, "not completed" means only "no full pass": those maps may complete lower down. The ladder row is the only one with a peel search. Leader rechecked all 19 of its stored plans: every breach sits in a tier that plan's level peeled, no T0 breach, lifts match. Of the 2 not completed, `sim.medium.185100011` has a safe partial plan with 87.6 % of boxes aboard and `random.medium.185100200.0.2` has no plan at any level. Lifts on relaxed plans aren't comparable with passes.

### Engine 0.13.0 final benchmark (D261): 15 of 18 holdout maps

**Scope.** 18 maps of the 72 holdout (`lab/c6/leader/holdout18.txt`): for each ship and fill, the first `random_yard` low and the first `simulated_yard` high. The leader had already read this set with the first and improved LP, so it isn't a fresh sealed number; nobody tuned on it. **Technical write-up only, never the pitch or the video (`docs/headline.md`).** One seed, no ablations. Rows: `lab/c6/leader/final/` (`e0`–`e3`, `f0`–`f2`, `quick_tiny`, `quick_small`), plus 3 fresh rows in `lab/c6/leader/arm-fresh-big/`.

**Benchmarked tree `a3e52c7c`; pinned tree `e768ee9e`** (recomputed with `engine.provenance.source_digest()` at 21:58 EST). They differ by the two code-review fixes only: the ladder's handling of a full pass, and a solver deadline. Neither fix's symptom shows in these rows: no row reads as a failed level-0 plan.

| Ship | Boxes | Fill 60 % | Fill 70 % | Fill 80 % | Engine 0.13.0 | Improved LP alone | Fresh 0.9.0 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `feeder_500` | 82–118 | 2 of 2, 50.0 lifts | 2 of 2, 55.5 | 2 of 2, 80.0 | 6 of 6 | 6 of 6 | 2 of 6 |
| `feeder_800` | 165–220 | 2 of 2, 86.5 | 2 of 2, 122.5 | 2 of 2, 159.0 | 6 of 6 | 6 of 6 | 0 of 6 |
| `feeder_1700` | 304–405 | 2 of 2, 157.5 | 1 of 2, 229.0 | 0 of 2 | 3 of 6 | 3 of 6 | 0 of 6 |
| **All** | | 6 of 6 | 5 of 6 | 4 of 6 | **15 of 18** | 15 of 18 | 2 of 18 |

Lift cells are means over passing maps only, so they aren't comparable across cells. Lifts include projected later-port lifts (D15).

- **Against fresh 0.9.0:** 15 passes against 2. On the 2 maps both pass, 62 against 116 and 38 against 56. 15 fresh rows ran beside the engine for the first 90 minutes; the 3 in `arm-fresh-big`, including both fresh passes, ran earlier the same day, so those aren't at the same load.
- **Against the improved LP alone:** same 15 maps pass, so the engine adds no passes on this set. Lifts on those 15: 110.07 against 117.33, −7.27, −6.2 %, 95 % bootstrap −13.07 to −3.00, 10 wins, 5 ties, 0 losses. The LP row ran at another time.
- **Winning lane on the 15:** 5 pages, 5 assignment LP, 5 lift polish. The polisher's 5 wins saved 3, 1, 4, 1 and 9 lifts against the best plan it started from. This isn't an ablation: nothing here says what removing a lane would cost.
- **Bound:** no plan is proved optimal. Plan minus certified lower bound runs from 10 to 131 lifts, mean 42.6, so true distance from best is unknown.
- **Ladder on the 3 failures, all `feeder_1700`:** `simulated_yard-f80-high` completed at T1 (level L4, 150 lifts, 404 boxes aboard, breaks T2–T5 rules including container securing and visibility; "breaks a class or statutory limit, needs a person's sign-off"). `simulated_yard-f70-high` completed at T0 only (L5, 256 lifts, 353 boxes aboard, breaks T1–T5 rules; "not safe to sail, shown only to say what would have to change"). `random_yard-f80-low` has no plan at any level and no safe partial. No claim that no plan is possible on any of them.
- **Cheaper stows rejected:** on `feeder_800-simulated_yard-f70-high` the page search found a stow at 67 lifts, and on `feeder_800-random_yard-f80-low` one at 141, against final plans of 121 and 175. Both fell to `expert.e9.over_width_length` before ballast, so other rules were never tested on them. A lead for later engine work, not a result.

**Regression, same maps:** tiny 8 of 8, 6.375, all 8 tied with 0.12.0 and fresh. Small 10 of 10, 11.5 against 11.4 for 0.12.0: 9 ties and 1 map worse (`sim.small.41100007`, 9 against 8). Stored fresh arm on those small maps 13.1, a difference of −1.6, 95 % bootstrap −5.1 to +0.3, 1 win, 8 ties, 1 loss, not at the same load. `frontier` won every tiny and small map, so no gain is proved there.

**Recheck:** leader, fresh process, unchanged checker. All 35 full passes in this section's comparison (15 engine, 2 fresh, 8 tiny, 10 small): input hash, every box aboard, 0 violations, stored lifts match. Both relaxed plans: every breach sits in a tier that plan's level peeled, no T0 breach, lifts match, all boxes aboard.

**Time, in-envelope maps (8 of the 18; not a pass-rate claim, the envelope was chosen after reading this holdout):**

| Stage | Mean wall s | Max wall s |
|---|---:|---:|
| `starts` | 3,007 | 7,251 |
| `warm_polish` | 236 | 577 |
| `assignment_lp` | 139 | 202 |
| `post_pass_exchange` | 20 | 31 |
| `lift_polish` | 16 | 52 |
| `bound` | 8 | 21 |
| First full pass from the LP lane | 139 (median 131) | 202 |

**Don't quote any of these times as the engine's speed.** From about 19:10 EST the run shared the machine with reader v3's GPU training and the fleet, with load between 30 and 80 and spikes to 90 (D255). They only show where the time goes. One finding stands anyway: every map overran the nominal 900 s + 600 s budget, 1,511 s at best and 7,830 s at worst, so `--budget-s` can't be read as a wall limit. Cause not investigated.

**Entry point:** `experiments/mvp/frontier/engine/lab_method.py`, `propose` and `finish`; manifests `source-manifest-0.13.0-final.json` and `source-manifest-0.13.0-review-fixes.json`.

**Not done, by the user's order to end early (D252):** ablations, a second seed, a heavy-load test, a logic review of 0.13.0. Layover, multi-port and ships above 1,700 TEU aren't in the engine.


### RCSPPSuite: public multi-port benchmark (D197, D227)

Fullest public test of the problem: 73 instances, four ships of 1,040–18,854 TEU, whole rotations of 6–19 ports, with ballast, trim, GM, bending, lashing, reefers, dangerous goods, the long crane and paired blocks. Evaluation only, never used to train or tune a model. A separate planner built from published methods (`experiments/mvp/frontier/public/rcspp/`, full note in `VALIDATION.md`), which shares no code with `frontier.planner`, so it says nothing about our own maps. Published plans are example plans, not bounds. Every one breaks at least one rule under the current files.

| Ship | Instances | No fault of its own | Boxes per instance, ours vs published | Objective against published (95 %) | Master budget |
|---|---:|---:|---:|---:|---:|
| s | 21 | 21 | 999 vs 965 (+3.6 %) | +3.7 % (+2.5 to +4.9) | 200 s |
| m | 21 | 15 | 3,263 vs 3,339 (−2.3 %) | −2.4 % (−4.6 to +0.1) | 400 s |
| l | 18 | 5 | 3,203 vs 3,696 (−13.3 %) | −13.2 % (−16.2 to −9.8) | 1,200 s |
| xl | 13 | 1 | 8,388 vs 9,434 (−11.1 %) | −11.2 % (−14.4 to −7.7) | 1,200 s |

- **Moves don't separate the plans.** Restows and hatch restows are 0 on all 73 published plans and all of ours, so this compares boxes and ballast, not lifts. It has no yard.
- **The small-ship win is the mixed-bay limit.** Our plans peak at a median of 8, the published plans at 3. At a limit of 3, on the 15 comparable instances, we trail by −2.3 % and −3.7 % on two seeds. The fair one-line reading for S and M is 2–4 % behind at a tight limit, while meeting rules the published plans break.
- **Medium isn't a time problem.** Three times the budget moved three instances from −7.7 % to −6.6 %, with the same faults. A second seed cleared 1 of 6 faulty plans. Faults sit in how the master prices trim and ballast.
- **Large and extra-large aren't solved.** The master beats greedy alone (−21.9 % and −12.6 %, no clean plan), on 17 of 18 and 9 of 13. It proved its own optimum on `l_02` and `l_17`. Three wide-gap instances at 3,600 s moved from −19.2 % to −16.7 % together: `l_10` −20.7 % to −12.0 %, `l_01` −20.6 % to −20.5 %, `l_15` −16.6 % to −16.3 %, all still with faults. On those last two the master assigns more boxes and the slot stage doesn't keep them. At 1,200 s, about two thirds of the L gap is in the master and one third in the slot stage.
- **Audit.** A separately written evaluator agreed on boxes, ballast and both objectives on all 177 plans, and on physical numbers within 1e-9. It isn't blind: the reviewer read the checker first.
- **Segregation is a stand-in** for the undefined set S^seg, so any dangerous-goods statement here is approximate.

### Arm inventory: test v2.1 and validation (D198)

Cells give **full-checker passes / allocated maps; mean lifts on that arm's own passes**. These own-pass means are an inventory,
not paired effect sizes: use the mutual-map comparisons above or below when pass sets differ. Test and unseen results lead this
section; all validation numbers here are **development data for frontier, placing only**. The final-test arms and current strategy
arms are all retained, including unavailable measurements. Earlier solver, Spark, base-mask and adapter diagnostics remain below.

| Arm / recipe | Test v2.1 tiny (15) | Test v2.1 small (15) | Validation small (55; development) |
|---|---|---|---|
| **Frontier 0.9.0, current bar** | **15/15; 5.93** | **14/15; 12.86** | **51/55; 12.14** |
| Two-step v4.4, stored, 120 s finisher | 15/15; 6.47 | 14/15; 22.86 | 50/55; 24.58 |
| Two-step start, same frontier run, 300 s finisher | 15/15; 6.47 | 14/15; 22.86 | 50/55; 24.58 |
| Block-rule start, same frontier run, 300 s finisher | 15/15; 6.47 | 11/15; 15.00 | 46/55; 13.57 |
| StowLM 8.2, greedy own plan | 15/15; 7.33 | 14/15; 34.36 | 44/55; 30.43 |
| StowLM 8.2 + 15 s polish (hybrid) | 15/15; 7.13 | 14/15; 33.93 | **Not run** (0/44 passing plans polished) |
| Two-step + 15 s polish (hybrid) | 15/15; 6.47 | 14/15; 22.21 | **Not run** (0/50 passing plans polished) |
| Stored block-rule, compacted, selected with default two-step | Not run as this recipe | Not run as this recipe | 51/55; 14.69 |
| K1b: 8.2, 16 candidates with compaction | Not run | Not run | 51/55; 19.47 |
| K1b null: untrained Glimmer, same recipe | Not run | Not run | 51/55; 20.37 |
| K1r: uniform random, 16 candidates with compaction | Not run | Not run | 51/55; 19.31 |
| K1c: rule-masked 8.2 selected with its own K1b fallback | Not run | Not run | 51/55; 13.27 |
| Rule-masked 8.2 alone | Not run | Not run | 44/55; 12.20 |
| Post hoc best of K1b / rule-masked / block-rule | Not run | Not run | 51/55; 12.82 (post hoc only) |
| K1c random, with its own unmasked random fallback | Not run | Not run | **Pending chance arm; no final claim** |
| K1c null, with its own unmasked null fallback | Not run | Not run | **Pending chance arm; no final claim** |

Sources (working notes, not in the public repo): frontier `runs/test-0.9` and `runs/val-0.9`; `test-91/SUMMARY.md`;
`K1B.md`, `NULL.md`, `K1C.md`, `BLOCKRULE.md`; D198 and `rev-k1c.md` independently reconstruct the chance comparisons.
`final-validation/POLISH.md` records the missing validation polish results.
The K1b/K1c/null/random/selected-rule recipes used validation only; a missing test cell is not a test failure.

**D198 placing on the same 51 mutual validation maps (development data):**

| Arm | Passes / 55 | Mean lifts | More than frontier | Fewer / equal / more than frontier |
|---|---:|---:|---:|---:|
| Frontier 0.9.0 | 51 | 12.14 | — | — |
| K1c, own fallback | 51 | 13.27 | +9.4 % | 3 / 23 / 25 |
| Stored selected compacted block-rule | 51 | 14.69 | +21.0 % | — |
| K1r uniform random | 51 | 19.31 | +59.1 % | — |
| K1b 8.2 | 51 | 19.47 | +60.4 % | 1 / 1 / 49 |
| K1b untrained Glimmer | 51 | 20.37 | +67.9 % | — |

The stored block-rule portfolio chooses between rule and default and used a 600 s finisher, unlike frontier's plain same-run
rule start at 300 s. Frontier starts from that rule, so a no-loss result against it isn't independent evidence. K1b's historical
compacted two-step baseline is another distinct row: 49/55, 23.29; block-rule's later compacted default is 50/55, 23.58. Neither
replaces plain two-step's 50/55, 24.58. Frontier's validation paired changes versus K1b and stored selected block-rule are
−37.7 % (95 % −43.6 to −31.5) and −17.4 % (−23.2 to −11.7), respectively, on those 51 maps.

**Chance and timing limits (D198).** Unmasked 8.2 versus random shows **no measurable advantage here** (sign p = 0.77),
not equivalence. Random versus null is −5.2 %, outside the coded ±5 % band in the direction favouring random; that band was
not in D193 (10:05). Finisher-limit trials were 3/54 for K1b 8.2, 49/100 null, 31/75 rule-masked 8.2 and 48/99 random,
judged in different windows under different load. The cause is unknown, and timeouts can select a dearer plan. If D196's
completed chance reading gives the model credit, all arms need a matched, interleaved re-judge under one frozen judge before
claiming that credit; otherwise no re-judge. The public story and app-label changes wait for those chance arms; see
`experiments/mvp/frontier/SWAP-LIST.md` (a working note, not in the public repo).

### 9.1 final test: 8.2 on test v2.1 (historical two-step comparison; 30 Oct 14:09–14:23; D164; `experiments/mvp/runs/test-91/SUMMARY.md`)

**Test, run once.** Final adapter 8.2 (D163), D128 recipe: greedy, mask + securing + look-ahead, V4 finisher 120 s for both methods,
full checker, no repair. Label: *cargo sequence decoded by StowLM under the feasibility mask (+ bounded rewinds); ballast by the
deterministic V4 finisher; full checker passed.* **D158 confirmed: Worse** (test small: Δ +50 %, passes equal; validation also Worse).

Historical comparison: StowLM and two-step both pass 29/30, with StowLM using ~50 % more moves on their 14 mutual small
maps. Frontier 0.9.0 is now the bar: it also passes 29/30 and uses 12.86 vs StowLM's 34.36 lifts on those same 14 small maps
(paired comparison and mandatory emulation limits above). The one test map none passes is `sim.small.28100011`.

| Row | Rung | Pass | Mean weighted moves (passes) | Paired vs two-step (n; Δ; better / equal / worse) | s/map |
|---|---|---:|---:|---:|---:|
| StowLM own plan (greedy) | tiny | 15/15 | 7.33 | 15; +0.87; 2 / 6 / 7 | 13.32 |
| | small | 14/15 | 34.36 | 14; +11.50; 2 / 1 / 11 | 28.45 |
| | all | 29/30 | 20.38 | 29; +6.00; 4 / 7 / 18 | 20.88 |
| Two-step v4.4 (same pipeline, same finisher budget) | tiny | 15/15 | 6.47 | — | 21.26 |
| | small | 14/15 | 22.86 | — | 35.31 |
| | all | 29/30 | 14.38 | — | 28.28 |
| StowLM + 15 s solver polish (hybrid, never own plan) | tiny | 15/15 | 7.13 | 15; +0.67; 3 / 7 / 5 | 16.07 |
| | small | 14/15 | 33.93 | 14; +11.07; 3 / 0 / 11 | 31.19 |
| | all | 29/30 | 20.07 | 29; +5.69; 6 / 7 / 16 | 23.63 |
| Two-step + 15 s solver polish (hybrid) | tiny | 15/15 | 6.47 | 15; 0.00; 0 / 15 / 0 | 24.84 |
| | small | 14/15 | 22.21 | 14; −0.64; 4 / 10 / 0 | 40.06 |
| | all | 29/30 | 14.07 | 29; −0.31; 4 / 25 / 0 | 32.45 |

The hybrid rows' "paired" column compares with plain two-step. Equal polish on both sides, and own plans, on mutual passing maps:

| Comparison | Rung | Mutual | StowLM mean | Two-step mean | Fewer / equal / more (StowLM) | Exact sign p |
|---|---|---:|---:|---:|---:|---:|
| Own plans | tiny | 15 | 7.33 | 6.47 | 2 / 6 / 7 | 0.180 |
| | small | 14 | 34.36 | 22.86 | 2 / 1 / 11 | 0.022 |
| | all | 29 | 20.38 | 14.38 | 4 / 7 / 18 | 0.004 |
| Both + 15 s polish (D157) | tiny | 15 | 7.13 | 6.47 | 3 / 7 / 5 | 0.727 |
| | small | 14 | 33.93 | 22.21 | 2 / 0 / 12 | 0.013 |
| | all | 29 | 20.07 | 14.07 | 5 / 7 / 17 | 0.017 |

Exact two-sided sign tests, ties dropped, no multiple-comparison adjustment. n = 15 per rung, slices of a ship. Timings are pipeline
s/map including finisher and checker; two-step's rows were pre-run sealed in 4 shards while 8.3 trained (D155), so they aren't a controlled speed
comparison. Polish changes little on either side. Own-plan pass counts tie here, while validation had 44 vs 50 of 55.

Frozen test set `experiments/mvp/corpus/test/` (tiny rung 15 instances; small/medium re-freeze as test v2 after 4.7).
Weighted lifts = yard rehandles + restows + hatch-cover lifts, now and at later ports (checker voyage projection).

### Tiny rung, 29 Oct (`experiments/mvp/runs/test-references.md`, `experiments/mvp/runs/base-mask/`)

| Method | Own plans passing the full checker | Shippable | Mean weighted lifts | Notes |
|---|---:|---:|---:|---|
| Two-step baseline (expert-described split, E10; retained row) | 15/15 | 15/15 | 6.47 | plan 0.7 s, pipeline ~29 s per instance |
| Scalable solver | 15/15 | 15/15 | 6.27 | |
| Exact solver (≤ 60 s) | 15/15 | 13/15 | 5.69 (n = 13, all `OPTIMAL`) | 13 engine-model optima; 2 `TIME_LIMIT` best-found timeouts, not shippable and excluded from the mean: `random.tiny.8000012.0.8` (9, bound 7), `sim.tiny.8000013` (6, bound 4) |
| Base Glimmer 30B + mask (untuned), best-of-1, no repair | 15/15 | 15/15 | 9.20 | mask overrode the model at 56 % of 244 decisions; 43.6 s per plan |

Paired (bootstrap 2,000, seed 0): two-step − exact = +0.62 lifts [0.15, 1.08] on 13 pairs; two-step − solver = +0.20
[−0.60, 0.87] on 15; base+mask − two-step = +2.73 (1 better, 3 equal, 11 worse). Tiny leaves little room above
two-step (~10 %); the headline comparison has to come from small/medium.

**Muse Spark zero-shot (6.7), tiny, 29 Oct** (`experiments/mvp/runs/spark-zeroshot/`, one sample each, no repair; scored
as whole plans: parse → move engine → ballast finisher → full checker, labelled APPROXIMATION in `score.py` because the
stepwise engine path wasn't used):

| Condition | Grammatical + complete | Move engine accepts | Full-checker pass | Total weighted lifts | $/instance | Median latency |
|---|---:|---:|---:|---:|---:|---:|
| Observation + D76 rules appendix (the 6.7 row) | 15/15 | 15/15 | **15/15** | 94 (mean 6.27) | $0.044 | 36 s |
| Ablation: observation only | 15/15 | 13/15 | 13/15 | 87 on 13 (82 with the appendix on the same 13) | $0.072 | 60 s |

Spark equals the proven lower bound on 5/15 and ties the scalable solver wherever both have checked plans. Both ablation
failures are slots that aren't cells of the vessel, refused by the move engine (the mask makes that class of error
impossible). Reading: on tiny, a frontier model told the rules plans legally and near-optimally; StowLM's tiny case rests
on the mask guarantee, on-device planning, cost and latency, and the comparison has to be made on small/medium.

**Muse Spark zero-shot (6.7), small, test v2 (10 of 15: feeder_500 + feeder_800), 29 Oct** (one sample, D76 appendix,
whole-plan scoring, labelled APPROXIMATION; `experiments/mvp/runs/spark-zeroshot/`):

| Metric | Value |
|---|---|
| Single response completed the plan | 9/10 (one malformed slot at action 13) |
| **Full-checker pass** | **5/10** (tiny: 15/15) |
| Violations (failing instances) | yard footprint overlap from Spark's own relocations 3; IRS securing (20' under 40' in hold) + forward draught 1; heat distance (1.96 m vs 2.4 m) 1 |
| Cost / latency | $0.132 per instance; median 121 s, ~99 % reasoning tokens |

Reading: Spark degrades from tiny to small mainly on geometry it can't see (footprints, tank positions) and on securing;
the production mask encodes the geometry, and the checker catches the rest. Feeder_1700 small (5) pending.

**Small test v2 references (6.3), first 10 (feeder_500 + feeder_800), scalable solver without the securing screen, V4
finisher, C2 off** (`experiments/mvp/runs/test-references.md`): 8/10 full-checker passes; weighted moves on passing
plans 11–30; lower bounds 0–14 (loose). Both failures hit `iscode.loading.draft` after a constructor / arrival-ballast
timeout (not proofs of infeasibility). The official solver row is being re-run with the gold configuration (securing
screen + V4) on all 15 small; two-step follows 4.8.

**Muse Spark small, all 15 of test v2.1 (update):** **6/15 full-checker passes** (feeder_500 + 800: 5/10; feeder_1700:
1/5); a single response finished the plan on 14/15; $0.134 per instance; median 130 s. Of the 9 failing plans, the
production mask's existing screens would have stopped the failing action in 8 (yard footprint ×3, yard size-fit,
ship-cell double booking, heat clearance 1.96 vs 2.4 m, OOG clearance, heavy box on deck with a free hold slot); the
9th is securing (20' under 40' in hold; now covered by the D95 necessary-envelope layer) plus forward draught (checker
only). Not replayed through the mask action by action (stepwise replay unavailable for Spark's plans in this epoch).
Spend for 6.7: $3.77 measured + one $1.51 unknown hold.

**Small test v2.1 references (6.3), all 15, official solver = gold configuration (securing screen + heavy-first
ordering + V4 finisher, C2 off):** **12/15 shippable**; without the screen 10/15 (ablation). Failures: draught ×2
(28100007, 28100011), E6 heavy-under-deck (28100010); the no-screen ablation additionally fails IRS securing on 28100013
and draught on 28100009. Weighted objectives on passing plans 9–44; lower bounds 0–18 (loose). Two-step small running.

**Small test v2.1, two-step v4.3 (PROVISIONAL: v4.4 fixes two review bugs; rerun pending):** two-step vs the official
solver on 11 mutually shippable pairs: two-step 22.27 vs solver 23.18 weighted moves (two-step 6 wins / 4 ties / 1 loss;
per ship 500: 16.2 vs 15.4, 800: 23.75 vs 24.75, 1700: 34.5 vs 39.5); bound mean 5.0. Two-step failures: one dead end,
one IRS securing. This confirms D96's historical comparison: the scalable constructor alone doesn't beat two-step on small; the D96
warm-start + LNS gold mode (interim: 18→15, 27→24, 31→27 on three instances, ~2.3 s of LNS) is what the StowLM headline
rests on.

**Two-step v4.4.0 (final, D98), small test v2.1 + tiny:** rerun with `twostep.py` sha256 78dec70b…aeaf2: identical to
the v4.3 rows above. Small paired (11 mutual): two-step 22.27 vs official solver 23.18 (Δ −0.91); per ship 500 +0.80,
800 −1.00, 1700 −5.00; bound 5.0. These are the baseline rows of record (`experiments/mvp/runs/test-references.md`).

**Base Glimmer 30B + mask (6.2), small test v2.1, 15 instances** (`experiments/mvp/runs/base-mask/small/`; best-of-1,
no repair, C2 off; label "MVP v4"): **6/15 own plans shipped**, all 6 pass the full checker (no repair used); 6 dead
ends, 3 timeouts (600 s cap). Decode 15.4 tok/s; mask host 0.11 ms/step; 23 % GPU-host stall; 167 s mean pipeline per
instance; peak 17.8 GB. (Tiny: 15/15.) This is the "what fine-tuning has to fix" row for small.

**Tiny SFT pilot (not 8.1), 15 tiny test, masked + securing layer, no repair** (`experiments/mvp/runs/sft-pilot-tiny/`):
T=1: 15/15 pass, 14.93 mean weighted moves, mask override 8.7 %; greedy T=0: 15/15, **8.53** (4 equal, 11 worse vs
two-step 6.47), override 3.8 %. Base + mask (T=1, run without securing/V4; rerun pending): 15/15, 9.20, override 56 %.

**Base vs SFT pilot, like for like (tiny test 15, securing layer + V4 finisher, no repair; all rows 15/15 own passes):**

| Decode | Base Glimmer + mask | SFT pilot (tiny gold only; gold is time-limited exact, D151) | Two-step v4.4 | Exact (13 proven optimal; 2 best-found timeouts excluded) |
|---|---:|---:|---:|---:|
| Greedy T=0 | 10.53 (override 59 %) | **8.53** (override 3.8 %) | 6.47 | 5.69 (n = 13) |
| Sampled T=1 | 9.20 (override 56 %) | 14.93 (override 8.7 %) | 6.47 | — |

Greedy SFT is 2.0 weighted moves per instance better than greedy base; neither beats two-step yet. The Exact column covers
the 13 `OPTIMAL` maps of `experiments/mvp/runs/test-exact` and the other columns all 15, so read it as a yardstick, not a
paired gap (paired: two-step − exact = +0.62 on those 13).

**Pilot 2 on validation (val15, 29 Oct; not 9.1, not test)** — 15 small VAL maps (`experiments/mvp/valsplit/ablation_small15.txt`),
adapter `experiments/mvp/sft/pilot2/adapter` (1 epoch, 852 rows), all rows: feasibility mask + D95 securing, V4 ballast
finisher (90 s wall budget per candidate), no repair, full checker on every shipped plan
(`experiments/mvp/sft/pilot2/val15/README.md`). Label: *cargo sequence decoded by StowLM under the mask; ballast by the
deterministic V4 finisher; full checker passed.*

| Decode | Shipped (all pass) | Dead end | Finisher timeout | Mask override | Weighted moves, paired vs two-step v4.4 | s/map |
|---|---:|---:|---:|---:|---|---:|
| a: greedy | 8/15 | 5 | 2 | ~22 % | 36.6 vs 23.4 (7 maps; worse on 7/7) | 127 |
| c: greedy + look-ahead + rewinds (≤ 32) | 10/15 | 3 | 2 | ~12 % | 35.3 vs 22.4 (9 maps; worse on 9/9) | 139 |
| d: best-of-2 (T = 0.7) | 3/4 run (11 not run) | — | — | — | T = 0.7 samples ~4× two-step moves | 247 |
| e: + 15 s LNS polish (hybrid, separate) | unchanged | — | — | — | 0 of 10 distinct plans improved | +12 |
| two-step v4.4 (same pipeline, 120 s finisher) | 12/15 | — | — | — | 24.7 mean on its 12 | — |

**Equal-budget correction (D139, 30 Oct 04:22; `experiments/mvp/sft/pilot2/val15/rescored-D139.json`):** the run let
a StowLM candidate whose finisher hit its budget be finalized again (planner last resort / pipeline re-finalize, up to the
per-map limit), which two-step never got. Re-scored from saved records with one finisher attempt: **row a 7/15**
(40100007 drops), **row c 8/15** (41100019, 40100040 drop); paired weighted moves vs two-step: row a 37.2 vs 22.2 (6
maps), row c 34.3 vs 21.4 (7 maps); two-step 12/15 unchanged. Look-ahead's measured gain is +1 map. (These ran at a 90 s
budget; 9.1 uses 120 s for both methods, so 40100007 might pass at 120 s; unmeasured.)

Attribution (D128): rewinds rescued 0 maps (all 38 on the 3 maps that still dead-ended); the 8 → 10 gain is look-ahead
and/or the sequential decode. n = 15: +2 maps is suggestive only. Finisher timeouts are counted separately (budgets
differed: 90 s vs two-step's 120 s). Row c's failures ran under the pre-D119 checker budgets. The D127 backtracking bug didn't affect row c (rule-freeze OFF → per-action counts).

**8.1 vs 8.2 on the 30 small VAL audit maps (30 Oct 07:38; D147; parent-choice audit, same recipe for both: greedy,
mask + securing + look-ahead, V4 finisher 90 s, post-D132/D137 code):**

| Adapter | Passed / 30 | Mean weighted moves (own passes) | Mean on the 20 maps both pass |
|---|---:|---:|---:|
| 8.1 (`stowlm-v1`, 882 train rows, pre-fix LR) | 22 | 35.32 | 36.2 |
| **8.2** (`stowlm-v2`, 1,253 train rows incl. 540 small, fixed LR) | **26** | 32.85 | **33.05** |

Validation numbers, not 9.1; physics failures 0 for both; securing active. **Not significant at n = 30:** passes 6 vs 2 discordant maps (sign p ≈ 0.29); moves on the 20 shared maps fewer on 11 / more on 7 / equal 2 (sign p ≈ 0.48). 

**8.1 / 8.2 / two-step v4.4 on the same 30 small VAL audit maps (30 Oct 09:00; validation only, not 9.1; `experiments/mvp/sft/val-audit30-twostep/`):**
two-step ran live `twostep.py` (sha256 78dec70b…, the v4.4.0 hash) with D79 C2 off, V4 finisher, one attempt, full checker,
no repair, CPU only. Its 90 s and 120 s runs are identical map by map (longest finisher call 23 s and 43 s), so the
audits' 90 s budget didn't hold it back.

| Arm | Passed / 30 | Mean weighted moves (own passes) | Mean on the 20 maps all three pass |
|---|---:|---:|---:|
| 8.1 | 22 | 35.32 | 36.20 |
| 8.2 | 26 | 32.85 | 33.05 |
| **two-step v4.4** | **29** | **26.03** | **26.40** |

| Pair (A vs B) | Passes: only A / only B (sign p) | Mutual maps | Mean A vs B | Fewer moves A / B / equal (sign p) |
|---|---:|---:|---:|---:|
| 8.1 vs 8.2 | 2 / 6 (0.289) | 20 | 36.20 vs 33.05 | 7 / 11 / 2 (0.481) |
| 8.1 vs two-step | 0 / 7 (0.016) | 22 | 35.32 vs 26.32 | 2 / 16 / 4 (0.001) |
| 8.2 vs two-step | 0 / 3 (0.250) | 26 | 32.85 vs 26.15 | 7 / 16 / 3 (0.093) |

Two-step passes every map either adapter passes; its one failure is a planner dead end on a map neither adapter passes.
On the 26 maps they share, 8.2 uses 25 % more weighted moves than two-step, not significant at n = 30 (exact two-sided
sign tests on discordant maps, ties dropped, no multiple-test adjustment). 8.1 is significantly behind two-step on both measures.

**Tiny gold quality (D149, D151; train maps, seeded sample of 90 tiny + 45 small, `experiments/mvp/gold/QUALITY-D149.md`):**
tiny gold is a *time-limited exact solver; 381/713 proven optimal*, not "exact/optimal plans". Stored gold is worse than
two-step v4.4 on 32/90 tiny maps (9.07 vs 6.57 weighted moves; ~36 % ship-weighted, 95 % CI 27–46 %). Proven-`OPTIMAL` gold
is never worse (0/50); `TIME_LIMIT` gold is worse on 29/34 (13.82 vs 6.59). Small `solver.gold_warmstart` is never worse
(0/39); small `solver.scalable` is worse on 3/6. The objective didn't change (135/135 stored costs recount exactly); the
60 s budget ran out while checking the better plan and the solver returned its greedy seed. 8.1 and 8.2 trained on these
rows. The loop's "model beat gold" counts are inflated where gold was time-limited: report them against the best of
{gold, two-step} (D151; `experiments/mvp/exit/deployment-d137/NOTE-D151-model-beat-gold.md`).

**Generations loop from 8.2 (D137 fix; 30 Oct 07:35–11:00; `experiments/mvp/exit/deployment-d137/`, `STOPPED-D151.md`):**
generations 0–2 completed; stopped during generation 3 for 8.3 (D151). 30 small VAL audit maps, greedy, 120 s finisher, same
epoch as 8.2's baseline (26/30; 32.85 mean). Paired counts recomputed from the per-map files.

| Audit | Adapter | Passed / 30 | Only child / only 8.2 | Mean moves on mutual maps (n) | Fewer moves child / 8.2 / equal (sign p) | Status |
|---|---|---:|---:|---:|---:|---|
| Generation 1 | `gen-000/child-0` | 28 | 3 / 1 | 35.56 vs 32.52 (25), +9.3 % | 7 / 10 / 8 (0.63) | measurement only; learning curve: not accepted, `mutual_moves_regressed` |
| Generation 3 | `gen-002/child-0` | 27 | 3 / 2 | 33.75 vs 33.08 (24), +2.0 % | 9 / 9 / 6 (1.00) | audit finished; gate and gold control never ran, so no gate verdict (D154's "not adopted" is what the rule would give: +1 pass, needs +2) |
| Generation 2 child vs generation 0 child (D172; columns read gen-2 / gen-0) | `gen-002/child-0` vs `gen-000/child-0` | 27 vs 28 | 1 / 2 | 32.15 vs 34.96 (26), −8.0 % | 13 / 7 / 6 (0.26) | measurement only; 95 % bootstrap CI −24.9 % to +10.9 % (`experiments/mvp/exit/analysis/gen_compare.out`) |

| Generation (12 maps each) | Winners: model / gold / two-step | Fitness passes: parent / SFT child / SFT + DPO child | Quotable greedy wins (D142 F2) |
|---|---:|---:|---:|
| 0 | 10 / 2 / 0 | 10 (8.2 only) | 1 |
| 1 | 8 / 1 / 2 | 11 / 11 / 7 | 1 |
| 2 | 7 / 2 / 3 | 10 / 12 / 6 | 1 |

No child replaced 8.2 as the final model; ties moved the loop's own training parent (D142 F5). No significant move or pass
change either way. The gold-only control never finished, so no "the loop learned X" claim is available (D128.4). DPO
children passed fewer fitness maps than SFT children in both comparisons (small sample). All three quotable wins beat two-step on
train maps no adapter trained on: 27 vs 31, 9 vs 11, 42 vs 44. **Diagnosis (D172, replacing the tiny-gold reading for small maps; 8.3 tested that and small maps
didn't improve):** the design was underpowered: 100 steps per child at LR 1.5e-5 (8.2: 2,504 steps, peak 5e-5), 35 new rows vs 1,253,
teachers winning 10 of them. Moves aren't in the SFT loss, and DPO hurt (cause not diagnosed). No audit difference is distinguishable
from zero: the 30-map audit can't resolve changes under about ±15 % (CI vs 8.2: gen-0 child −5.7 % to +25.7 %, gen-2 child −14.1 % to
+20.0 %). Plans are very sensitive to weights: 0/30 identical token sequences between adapters, ~10 % shared prefix. Training
noise is unmeasured (no same-recipe repeat).

**8.2 / 8.3 / two-step on the final validation sets (D152; validation only, not 9.1; `experiments/mvp/sft/stowlm-v3/VALIDATION.md`):**
Validation only; no test or showcase input. Same audit recipe for 8.2 and 8.3 (greedy batched decode, mask + securing + look-ahead, V4 finisher, full checker, layered mask replay); two-step through `ablation/twostep_val.py`. Finisher 120 s, except the 8.2 audit30 row: its 90 s parent audit equals its 120 s loop audit on all 30 maps. Exact two-sided sign tests on discordant maps, ties dropped, no multiple-test adjustment. One training run per adapter, so a difference is "consistent with" the relabelling, not caused by it. The D152 rule is applied by logic-v4-decode, not here.

**audit30** (30 maps; 25 passed by all three):

| Arm | Passed / 30 | Mean on own passes | Mean on the 25 maps all three pass |
|---|---:|---:|---:|
| 8.2 | 26 | 32.85 | 32.76 |
| 8.3 | 27 | 34.52 | 35.32 |
| two-step | 29 | 26.03 | 25.88 |

| Pair (A vs B) | Passes: only A / only B (sign p) | Mutual maps | Mean A vs B | Fewer moves A / B / equal (sign p) |
|---|---:|---:|---:|---:|
| 8.2 vs 8.3 | 1 / 2 (1.000) | 25 | 32.76 vs 35.32 | 13 / 9 / 3 (0.523) |
| 8.2 vs two-step | 0 / 3 (0.250) | 26 | 32.85 vs 26.15 | 7 / 16 / 3 (0.093) |
| 8.3 vs two-step | 0 / 2 (0.500) | 27 | 34.52 vs 25.63 | 6 / 17 / 4 (0.035) |

**reserve25** (25 maps; 15 passed by all three):

| Arm | Passed / 25 | Mean on own passes | Mean on the 15 maps all three pass |
|---|---:|---:|---:|
| 8.2 | 18 | 26.94 | 26.93 |
| 8.3 | 17 | 27.65 | 25.40 |
| two-step | 21 | 22.57 | 21.73 |

| Pair (A vs B) | Passes: only A / only B (sign p) | Mutual maps | Mean A vs B | Fewer moves A / B / equal (sign p) |
|---|---:|---:|---:|---:|
| 8.2 vs 8.3 | 3 / 2 (1.000) | 15 | 26.93 vs 25.40 | 7 / 3 / 5 (0.344) |
| 8.2 vs two-step | 0 / 3 (0.250) | 18 | 26.94 vs 21.72 | 4 / 9 / 5 (0.267) |
| 8.3 vs two-step | 1 / 5 (0.219) | 16 | 25.12 vs 21.44 | 2 / 9 / 5 (0.065) |

**tiny48** (48 maps; 47 passed by all three):

| Arm | Passed / 48 | Mean on own passes | Mean on the 47 maps all three pass |
|---|---:|---:|---:|
| 8.2 | 47 | 9.40 | 9.40 |
| 8.3 | 47 | 8.19 | 8.19 |
| two-step | 47 | 6.55 | 6.55 |

| Pair (A vs B) | Passes: only A / only B (sign p) | Mutual maps | Mean A vs B | Fewer moves A / B / equal (sign p) |
|---|---:|---:|---:|---:|
| 8.2 vs 8.3 | 0 / 0 (1.000) | 47 | 9.40 vs 8.19 | 6 / 19 / 22 (0.015) |
| 8.2 vs two-step | 0 / 0 (1.000) | 47 | 9.40 vs 6.55 | 2 / 30 / 15 (0.000) |
| 8.3 vs two-step | 0 / 0 (1.000) | 47 | 8.19 vs 6.55 | 1 / 24 / 22 (0.000) |

**Final adapter choice, 8.3 vs 8.2 vs two-step (30 Oct 14:07; D152 rule, D158 test; validation, not 9.1;
`experiments/mvp/sft/FINAL-CHOICE.md`, per-map tables there):** **8.2 stays.** 8.3 (same 1,253 maps as 8.2, per-map best verified
teacher; D151) met the pass condition (44 vs 44 of 55) but not the moves condition: on 40 mutual maps it used 3.4 % *more*
moves (31.60 vs 30.57; needed ≥ 5 % fewer; fewer on 12, more on 20, 8 equal, sign p = 0.22). Provisional D158 label: **Worse**
(8.2 vs two-step, 55 maps: 44 vs 50 passes, Δ +25.0 %, 30.43 vs 24.34 on 44 mutual maps); 9.1 can only keep or lower it.

| Set | Arm | Passed | Mean on own passes | Mean on maps all three pass (n) |
|---|---|---:|---:|---:|
| audit30 + reserve25 | 8.2 | 44/55 | 30.43 | 30.57 (40) |
| | 8.3 | 44/55 | 31.86 | 31.60 |
| | two-step v4.4 | 50/55 | 24.58 | 24.32 |
| reserve25 alone (fresher: audit30 picked 8.2) | 8.2 | 18/25 | 26.94 | 26.93 (15) |
| | 8.3 | 17/25 | 27.65 | 25.40 |
| | two-step v4.4 | 21/25 | 22.57 | 21.73 |
| tiny val48 (diagnostic, never decides) | 8.2 | 47/48 | 9.40 | 9.40 (47) |
| | 8.3 | 47/48 | 8.19 | 8.19 |
| | two-step v4.4 | 47/48 | 6.55 | 6.55 |

Exact two-sided sign tests on discordant maps, ties dropped, no multiple-test adjustment. 8.2 vs two-step, 55 maps: passes 0 / 6
(p = 0.031), fewer moves 11 / 25 / 8 equal (p = 0.029). 8.3 vs 8.2, tiny: fewer moves 19 / 6 / 22 equal (p = 0.015). 8.3 cut tiny moves
(−12.9 %), consistent with the relabelling, but that didn't carry over to small maps; one run per adapter and no repeat-run noise
estimate, so neither result is "caused by" it. 8.3 lost 4 of 55 small maps to the 120 s finisher limit and 8.2 lost 3. 8.3's higher val loss (0.392 vs
0.346) says nothing about plan quality (D162).

**8.1 SFT (30 Oct 00:14):** `experiments/mvp/sft/stowlm-v1/adapter`, 2 epochs over 882 train rows (600 tiny + 282 small)
+ 60 val, 2.0 GPU-h, mixed val loss 2.29 → 0.40 (still falling). Trained with the pre-fix LR schedule (near-constant
peak LR, D122). Its validation results come from the generations loop's baseline audit (30 small VAL maps,
`experiments/mvp/valsplit/audit_small30.txt`) and, finally, 9.1.
 The 15 s LNS polish
on the pilot's own checked plans takes 8.53 → 8.27 (4/15 improved, ~6 s each). The securing layer doesn't bind on tiny.

## Plan v6 task 3.1: held-out run on the frozen pipeline (2 Nov 2026, D288-D290)

**Label (D284):** held-out maps, frozen pipeline, run after two interrupted attempts; not the pre-registered sealed claim. Pipeline manifest v3 `a11b4202…` (D285), Engine 0.13.0, standard profile (300/100, 1,800 s engine wall, 1,200 s outer deadline), 4 workers, this laptop. Synthetic ships and cargo, simulated port; fresh draws from the same generator. Pass = L0 full checker pass with completion and every confirmed instruction obeyed; confirmation here is exact expected-rule match, not a human planner. Sources: `experiments/mvp/pipeline/sealed/runs/final-3/REPORT.md`, `report.json`, `story-figures.json`; independent recount in `experiments/review/v6/code11.md` (all figures match).

| Cohort | Ships | Pipeline passes (exact 95 %) | Two-step passes (exact 95 %) |
|---|---|---|---|
| tiny15 | 500-800 TEU | 10 of 10 (69.2-100 %) | 0 of 10 |
| tiny15 | 1,700 TEU (outside the design limit) | 5 of 5 (47.8-100 %) | 0 of 5 |
| small30 | 500-800 TEU | 20 of 20 (83.2-100 %) | 0 of 20 |
| small30 | 1,700 TEU (outside the design limit) | 9 of 10 (55.5-99.7 %; one L5 relaxed) | 0 of 10 |
| envelope24 | feeder_500 + feeder_800 | 24 of 24 (85.8-100 %) | 0 of 24 (300 s variant: 1 of 24) |

Two-step returned relaxed plans or no plan (inside the envelope: 10 relaxed, L4 9 and L5 1, and no plan on 14). The two-step planner is modelled from one field expert's description; inside the envelope it passed 0 of 24 (0–14.2 %), and with 300 s 1 of 24 (0.1–21.1 %). That says the baseline doesn't survive our rules, not that two-step planning fails in practice. Instruction arm (one confirmed instruction per envelope map): registered binding passes 13 of 18 (46.5-90.3 %) among voyages whose no-rule plan broke the rule; 18 of 24 counting every voyage (53.3-90.2 %; feeder_800 11 of 12, feeder_500 7 of 12), templated sentences; on the other 6 the first plan already obeyed it. No mutual passes against two-step, so no lift saving is quotable. Per instruction type, close_bay +18.75 [2.0, 35.5] and max_tiers +13.2 [2.0, 24.4] extra lifts exclude zero; we don't attribute lift changes to individual rules: per-type samples are 2–6 maps and same-map spread is about ±20 lifts. Mean planning time per envelope map, planning step only, four maps at a time on this laptop: 563 s (pipeline), 694 s (with one instruction). For the first hour of the final run, two freeze test suites, one triage suite and one draining lattice row ran beside it (D278); wall times are upper bounds. The launcher's 'other fleet work stopped' message was wrong for that hour. Two earlier attempts on the same maps stopped early when our own frozen-code guard tripped. In the first, a test rewrote a pinned fixture; in the second, the engine wrote scratch files inside its pinned folder. No planning code changed. They completed 36 and 91 rows; both are published as they are and not pooled with the final run. Where the same rows ran again in the final run, attempt 1 matched the final run on 36 of the 38 rows it opened (status and lifts; 37 of 38 on verdict alone) and attempt 2 on 91 of 91, reported separately.

### Fresh held-out extension (ext-1, D333)

**Label:** fresh held-out extension, run after the freeze; not the pre-registered sealed claim. 24 new envelope maps (feeder_500 and feeder_800, 12 each) from new seeds, pre-registered (PREREG sha256 ba3dcc46…), run on manifest v4 `acf2f275…` (planning code byte-identical to v3). Reported beside 3.1, never in place of it; never called sealed. Source: `experiments/mvp/pipeline/sealed/measurements/ext-1/FIGURES.json`; independent recount `experiments/review/v6/code15.md`.

| Arm | Passes (exact 95 %) | feeder_500 | feeder_800 |
|---|---|---|---|
| Pipeline | 24 of 24 (85.8–100 %) | 12 of 12 | 12 of 12 |
| Two-step | 1 of 24 (0.1–21.1 %) | 1 of 12 | 0 of 12 |
| One confirmed instruction, every voyage | 20 of 24 (62.6–95.3 %) | 8 of 12 | 12 of 12 |

Binding (a checked plan obeying a rule the first plan broke): 18 of 22 (59.7–94.8 %); on the other 2 the first plan already obeyed it. By type, small samples, quoted only with denominators: close_bay 4 of 6, dg_keep_away 5 of 5, max_tiers 6 of 6, one_port_per_hatch 3 of 5. No lift saving is quotable (one mutual pass in the extension, too few to compare), and no wall times are reported.

Pooled with the first held-out run (secondary figure): 48 of 48 envelope maps passed the full checker (92.6–100 %); binding 31 of 40 (61.5–89.2 %). Never the lead figure, and not one experiment: 3.1 ran on v3, ext-1 on v4, and no partial attempt is pooled.

## Plan v6 StowLM Reader results (locked test and ood-1; D266, D321)

Locked test, 128 sealed sentences written by a separate AI reviewer (not real planners), same mask; scored once:

| Reader | Exact, of 128 | Loosening requests accepted, of 48 | Wrong rules from 76 sentences it should refuse |
|---|---:|---:|---:|
| Muse Glimmer 30B, same mask, no fine-tune | 60 (46.88 %) | 0 | – |
| **StowLM Reader, v3 swa-last-5, adopted** | **118 (92.19 %)** | **1** | **4** |
| Reader v2, not adopted | 120 (93.75 %) | 2 | 6 |
| Muse Spark, cloud (fixed reference, earlier mask, not rerun) | 126 | 0 | – |
| Hand-written parser, frozen before scoring | 90 | 1 | – |

It passed the 90 % gate and missed two stricter ones (one loosening request accepted where none was allowed; 6 points below Spark where 3 were allowed). Sources: `experiments/mvp/reader/v3/results/`, D266.

**ood-1 (D321):** On 100 synthetic instructions written after the freeze by a different model (not real planner text; not sealed; one pre-registered run on the same ships and four kinds of rule), the adopted StowLM Reader read 86 exactly (95 % interval 77.6–92.1 %). The same model and mask without our fine-tune read 45 (35.0–55.3 %), and a hand-written parser 68 (57.9–77.0 %). On the 66 sentences that carry a rule, it read 61 exactly (83.2–97.5 %), against 18 untrained (17.0–39.6 %) and 44 for the parser; its 5 misses were refusals, never a different rule. Its weak side is refusing. Of 34 sentences it should have refused, it turned 6 into a rule (6.8–34.5 %), more than the untrained model's 2 or the parser's 1, though the intervals overlap. One dropped half an order ("Close bays 9 and 11" read as bay 9 only) and one invented a number ("Max 4 tiers in the hold of bay 9" read as 2). That's why every reading is shown to a person and confirmed before anything is planned. It read 4 of the 5 Thai-English sentences. Source: `experiments/mvp/reader/v3/ood-1/REPORT.md`. The author and the blind second labeller are both Claude models, so their agreement can share blind spots.


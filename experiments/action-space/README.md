# Action space and expander — sizing and evidence (plan 3.8)

Code: `src/stowage/actions/` (`space.py` = the `boxslot` action space, `expander.py` = deterministic expander,
`samples.py` = seeded tiny instances + random action sequences). Tests: `tests/actions/`.

## The decision in one paragraph

One action = **one box and where it goes**: `Place(box, slot)` (load from the yard, put back from the quay, or a
direct ship-to-ship shift), plus `Discharge(box, to=None)`, an explicit `Relocate(box, stack)` and `RestowOff(box)`
for overrides and layovers. The expander adds every forced lift deterministically: yard blockers are relocated by a
default rule (clean stack first), ship blockers go to the quay, and hatch covers open and close lazily, which is
optimal for a fixed lift sequence. Overrides are written only where a plan departs from the defaults. So the model
makes both industry decisions (which slot, and which actual yard box) in every action, and the move count is fully
determined by its actions. `from_plan` represents legal solver move plans **without gate moves** (only cover lifts
are re-timed, never upward). Gate-in/out events belong to the simulator and are explicitly rejected by `from_plan`;
the move engine also rejects them in solver `MovePlan`s. The round-trip evidence below exercises this representable
domain; it is not a proof for every possible move sequence.

## Evidence

- **Round trip** (`tests/actions/test_space_properties.py`):
  - 80 seeded random instances, stage A + layover: `expand(from_plan(expand(x))) == expand(x)` byte for byte, and
    every plan is legal under the move engine.
  - 66 "foreign" plans with random relocation targets, early covers and covers parked on other covers: same
    container lifts, cover lifts never higher, objective never higher.
- **Brute force** (`bruteforce.md`): exact Dijkstra/A\* on 1,882 tiny instances, plus 117 congested-yard instances.
  - Default-only `Place` plans reached the **restricted-search optimum** in **100 %** of completed instances; no
    selected optimum needed an override token within that search.
  - Industry-style category actions (a fixed rule picks the box) lost a lift in 0.2 %.
  - Limitations: FULL permits at most **two voluntary actions**, relocations stay in their block, and **stowage
    rules are omitted**. A cheaper legal plan requiring three voluntary actions is outside the comparison. On these
    16-cell ships no selected optimum used the hold. Cover and overstow trade-offs are not measured here;
    re-measure with 4.6 on bigger rungs under the full checker. No unrestricted optimality claim follows.

## Tokens per action (Muse Glimmer 30B tokenizer, `tokens.py`)

Glimmer splits digit runs into groups of 3 from the left.

| text form (4.5 decides) | Place | Relocate / Discharge-with-target |
|---|---|---|
| compact `P<box:03><BBBRRTT>\n` (digit groups cut across fields) | **6** | 8.5 |
| aligned `<box:03>,<BBB>,<RR>,<TT>\n` (one token per field) | **8** | 8.5 |

- With token skipping (D9, deferred), the tier and the separators are forced, so only ~4 tokens per action are
  sampled.
- Indices above 999 need an extra token; indices cover blockers as well as loads, so this can occur below 1,000 loads.
- **Planning figure: 8 tokens per action, plus 5 % override actions.** The overrides are an assumption: tiny
  instances needed 0 %, and congested full-size yards will need some.

## Tokens and time per plan, per ladder rung

The active task 3.5 ladder has **ceilings** of 6 / 36 / 150 / 2,000 loads, not fixed generated counts. Defaults depend
on the vessel region, compatible length slots, cargo mix and full-export mass budget. The documented feeder_500
defaults in [`../instances/README.md`](../instances/README.md) are 6 / 13 / 53 / 189; they are reported generation
workloads, not full-checker-certified plans. `sizing.py` reads the production ceiling literals and the documented
default table without loading physics, running a generator or using a tokenizer.

The retained **planning assumption** is ceil(loads × 8 × 1.05) output tokens, excluding EOS. It is an expectation,
not a worst-case bound: rounded override counts, longer indices and non-Place actions can exceed it. In particular,
the recorded tiny case in `../format/measurements.json` exceeds this budget when a whole 5%-rounded override is added.
The later task 4.5 encoding measures 7 tokens per ordinary `Place` through index 999 plus EOS; forced tokens remain
in the sequence under D9. Its measured workloads below must not be confused with these estimates.

Speeds measured by stow-gpu (`experiments/glimmer-bench/run_long.log`, 2k prompt, no logits processor):

| mode | speed |
|---|---|
| single stream, to 2k tokens | 31 tok/s |
| single stream, to 5k tokens | 29 tok/s |
| single stream, to 10k tokens | 24.6 tok/s |
| batch 8 | 54–61 tok/s aggregate |
| batch 16 | ~81 tok/s aggregate (197 ms/step) |

Mask overhead is not included; 4.4/5.1 will measure it.

| rung | current ceiling (loads) | planning tokens at ceiling | documented feeder_500 loads | planning tokens for that workload |
|---|---:|---:|---:|---:|
| tiny | 6 | 51 | 6 | 51 |
| small | 36 | 303 | 13 | 110 |
| medium | 150 | 1,260 | 53 | 446 |
| full | 2,000 | 16,800 | 189 | 1,588 |

The full ceiling is a generation/adapter limit, **not evidence that 2,000 boxes are generated or directly supported
by the model**. At 150 loads, medium's estimate is 1,260 tokens (~41 s at 31 tok/s, ~16 s/plan at 81 aggregate tok/s),
instead of the obsolete 120-load / ~1,010-token estimate. These rates exclude prompt prefill and mask overhead.

Task 4.5's current canonical v2 dense sizing evidence is four recorded shards, not a new measurement in this fix:

| yard generator | loads | measured output including EOS | planning estimate excluding EOS |
|---|---:|---:|---:|
| random_yard | 400 | 2,801 | 3,360 |
| random_yard | 800 | 5,601 | 6,720 |
| simulated_yard | 400 | 2,801 | 3,360 |
| simulated_yard | 800 | 5,601 | 6,720 |

Source: [`../format/extended_measurements.json`](../format/extended_measurements.json), interpreted in
[`../format/README.md`](../format/README.md). Its prior 6 / 36 / 150 / 280-load ladder measurements are explicitly
historical pre-KCS evidence (43 / 253 / 1,051 / 1,961 tokens including EOS), not current generated defaults. Even
canonical v2 data predates the latest prerequisite contract fixes; neither these historical physical replays nor
their token counts certify current full-checker acceptance or current model quality. No end-to-end latency was
measured by this reconciliation.

At the retained 1,000-load direct-planning limit, the planning figure is 8,400 output tokens (~5.7 min at
24.6 tok/s, ~104 s/plan at 81 aggregate tok/s, before prefill/mask/checker costs). Fifty such plans would therefore
take ~4.74 h single-stream or **~1.44 h of aggregate batch-16 generation time** if the measured aggregate rate holds
at that length; neither is an end-to-end benchmark. Longer contexts and actual batches need measurement.

## Decision proposed to the leader

The existing D18 decision is **direct `boxslot` planning up to ~1,000 loads (~8.4k output tokens)**, with no grouped
form. This does not authorize direct planning for the full rung's 2,000-load ceiling. Inputs above that decision
limit need a leader/user decision before claiming support. D18's GRPO cutoff remains the medium rung, but its
old ≤120-load / ~1k-token rationale is obsolete: medium permits 150 loads, ~1,260 planning tokens, and the historical
measured 150-load case uses 1,051 including EOS before overrides. The rollout throughput and memory cutoff remains
**provisional**, pending measured masked rollouts and leader reconciliation of D18; this fix makes no new training
or cutoff decision. If 4.5 measures more than 9 tokens per action, or full-size eval
quality collapses, the fallback is **section decomposition**: the same action space, prompted one hatch section at a
time on the updated yard. A coarser action is not the fallback, because it gives the box choice to a rule.

## Reproduce

```
uv run python experiments/action-space/tokens.py
uv run python experiments/action-space/sizing.py    # arithmetic over existing evidence, no tokenizer/GPU
uv run pytest -q tests/actions
nice uv run python experiments/action-space/bruteforce.py   # see bruteforce.md for the exact arguments
```

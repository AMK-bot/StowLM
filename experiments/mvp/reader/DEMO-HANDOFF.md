## D222 and task 1.4 follow-up

`BRIEF.md` now reports the original cohort and the separately frozen static-screen-selected cohort. They share one detached two-worker controller. The first cohort's two interrupted attempts remain failures without reruns; its manifest, instructions and completed plans are unchanged.

For shots 5a/5b, use `refusal_card.read_recorded_card(case_id, parser=...)` with `loosening-dg` / `impossible-bay-2`. The latter is first-cohort brief-07: **23 TEU of cells needed, 0 left**. `REFUSALS.md` has all four actual Spark examples, the fresh checked witness for the tier-cap alternative, exact box-level proof, tests and one-shot setup. Cards release a cached explanation only after the live reader agrees with its recorded interpretation. Label the explanation recorded and Spark; the active reader's own provenance supplies its label. No checker tick belongs beside a static impossibility proof. `refusal_flow.review_sentence` is the general integration seam; it never applies an alternative automatically.

# D220 brief entry point (always reads before planning)

Use `experiments.mvp.reader.public_brief.plan_brief(instance, VoyageBrief(standing, voyage), parser=...)` for the standing/voyage pipeline. The standing text is `"One port per hatch."`; voyage is a tuple of hard instruction sentences, for example `("Do not load any containers in bay 01.",)`, with real vessel references. Each standing line and each voyage item visits the parser. Even an empty standing brief visits it. A refused or malformed voyage sentence stays visible in `evidence["sentences"]`, does not bind, and blocks release of any new plan (`status="needs_confirmation"`). The planner must explicitly correct or remove it and submit a new brief. Label any retained display as the previous plan, which has not satisfied the new request. Provider/unavailable-reader failures remain request errors. Import `VoyageBrief` from `public_brief`; plain strings are rejected before parsing. `read_brief` returns an immutable authenticated receipt; `execute_brief(instance, receipt)` derives constraints internally. Replay across processes uses `export_checkpoint` / `load_checkpoint` with a dedicated application signing key of at least 32 bytes (never a provider credential). Keep that signing key outside checkpoints and logs, and trust only checkpoints from the application issuer. Sentence, full input/context, exact readings and parser/adapter provenance are authenticated.

`brief_planner.py` is retained byte-for-byte solely for the running, hash-frozen historical cohorts and hero measurements. Its legacy API is not a demo integration entry point. The release gate fixes do not relabel or rerun those measurements.

Standing `one_port_per_hatch` only enables the port-blocked start; other starts remain allowed. Voyage instructions intersect as hard restrictions. Geometry-proven impossibilities return no plan before search; otherwise the pipeline uses Frontier 0.9, finishing and the full checker without repair. `pipeline.SparkParser` is the active baseline; `pipeline.GlimmerParser(generate=callback, adapter_sha256=...)` is the strict adapter seam, with no silent fallback. Do not describe Spark plans as fine-tuned-reader plans. The fixed 30-map validation measurement and fresh audits are in `BRIEF.md` and `runs/brief/`.

The single-sentence API and command below document the historical measurement only. Use `public_brief.plan_brief(instance, VoyageBrief(standing="", voyage=(sentence,)), parser=...)` for a new hard-sentence demo request.

# One live sentence → checked Frontier plan

Entry point: `experiments.mvp.reader.instruction_planner.plan_sentence(instance, sentence, *, parser, search_s=60., finalize_s=300., seed=0, workers=1)`. Use `experiments.mvp.reader.pipeline.SparkParser` today. Its replaceable interface is `parse(sentence, context) -> (validated Answer, provider_evidence)`; a later Glimmer adapter uses the same interface. No GPU is needed for the Spark path.

## Exact demo data

Use the synthetic validation instance `experiments/mvp/corpus/val/small/feeder_500/simulated_yard/seed-040100002.json` (SHA256 `69840b80a17f2820281490e2bd7cecb945e57342e881e4338cb6fa61162b027b`) and type **“One port per hatch.”** The structured response should be `{"schema_version":"stowlm.reader/2","constraint":{"type":"one_port_per_hatch"}}`. This is archived case `one_port_per_hatch-05`, instance `sim.small.40100002`, sealed sentence `sealed-056`.

The complete instance supplies vessel cells/stacks/physical covers, cargo/PODs, retained cargo, yard and physical checker inputs. The reader derives its context and the software derives cover→port allocation from that instance. The caller supplies neither a cover mapping nor an expected constraint to planning. The historical allocation was `HC-002-P → MYPKG`, `HC-002-S → SGSIN`; this is evidence, not a live input.

Run from the repository root, in the existing offline uv environment. Keep the repository's `src/`, `experiments/mvp/frontier/`, `experiments/mvp/hybrid/`, and normal checker/parameter assets available. `SparkParser()` loads `MODEL_API_KEY` from the parent Projects folder's `.env` into process environment only, unless already set. It uses the shared Spark ledger and the existing restart-safe $1 purpose allocation (`reader-d199-binding-parser`); it does not reset that budget. Credentials, ledger internals, and raw provider errors should not be displayed in the demo. No key belongs in arguments or saved output.

```sh
UV_CACHE_DIR=$PWD/.uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  uv run --offline --no-sync python - <<'PY'
import json
from pathlib import Path
from stowage.io import load_instance
from experiments.mvp.reader.instruction_planner import plan_sentence
from experiments.mvp.reader.pipeline import SparkParser

instance = load_instance(Path('experiments/mvp/corpus/val/small/feeder_500/simulated_yard/seed-040100002.json'))
result, evidence = plan_sentence(
    instance, 'One port per hatch.', parser=SparkParser(),
    search_s=78., finalize_s=300., seed=0, workers=1,
)
print(json.dumps({'status': evidence['status'], 'parsed': evidence['parsed'],
                  'wall_s': evidence['wall_s'], 'passed': evidence['passed']}))
# Successful result.plan is the full-checked MovePlan for display/serialization.
# evidence includes software allocation, parser provenance, and planner diagnostics.
if result is not None:
    print(result.plan.model_dump_json())
PY
```

This command makes one paid parsing request and a new planner run **when the demo operator executes it**. It was not executed for this handoff. Use one solve per process: constructor hooks require process isolation; at most two independent worker processes, with one native worker each. If wrapping in the experiment admission controller, retain its load-limit policy (never below 36).

## Wall time and recorded evidence

The archived run measured **1.138 s parsing + 0.716 s allocation + 294.420 s instruction-aware planner = 296.275 s**, approximately **4 min 56 s** for the live path's measured components, excluding instance loading, initialization and minor orchestration. This is a component sum from the existing run, not a new end-to-end timing of `plan_sentence`. The original planning worker took 324.473 s including its fresh baseline check and other orchestration; the separate audit took 85.890 s. Planning worker + parser + audit totaled 411.501 s (6 min 52 s). Shared-machine load and timed search change latency and the resulting plan. `finalize_s=300` is a per-finishing-attempt budget, not an overall request deadline; the public call reports actual `evidence['wall_s']`. Run it asynchronously in any demo integration.

Historical before/after: **10 → 9 weighted nonproductive moves**, changed container plan, full checker passed, freshly audited binding replan. For a recorded fallback or comparison, load these files explicitly as **recorded** evidence:

- Before: `experiments/mvp/frontier/runs/val-0.9/sim.small.40100002.json` (`plan`, `weighted`).
- After: `experiments/mvp/reader/runs/binding/plans/one_port_per_hatch-05.json` (`plan`, `parsed`, `compilation`, `constrained_moves`).
- Independent audit: `experiments/mvp/reader/runs/binding/audits/one_port_per_hatch-05.json`.
- Intended sentence target: `experiments/mvp/reader/binding-cases.json`, case `one_port_per_hatch-05`, frozen by `runs/binding/preregister.json`.

The live interface has no independent gold label: `passed=True` means execution passed checks for the **parsed constraint**. Evidence explicitly has `claim_scope='parsed_constraint_execution'` and `sentence_compliance_verified=False`. Sentence-level binding credit additionally requires an independently established intended target, exact structured parse agreement, a violating checked unconstrained plan, and a changed freshly checked constrained plan. The benchmark checks this separately; its target never enters the compiler.

## When no valid plan is returned

The return is `(None, evidence)` with `passed=False`: `status='refused'` for a parser refusal, `status='constraint_unavailable'` for a known compilation rejection (including incompatible fixed retained tier/DG cargo), or `status='no_checked_plan'` when the portfolio finds no passing plan. Show the parsed constraint and “No checked plan found”; do not present an old unconstrained plan as satisfying it. There is no repair or unconstrained fallback. A search timeout or failed software hatch allocation does not prove that the sentence has no feasible plan under every possible allocation. Provider/network errors, malformed parser responses, and unexpected software errors propagate as exceptions; handle those as request errors, not success or mathematical infeasibility.

For example, archived hatch case `one_port_per_hatch-04` found no admitted plan despite an optimal static packing witness. It earned no pass or binding credit. No new planner runs or Spark calls were used to write this handoff or fix the two review P2s.

# Recorded cut — task 1.5

Cut the realistic camera-to-yard feature from the shipped pipeline. Do not claim that SAM + Muse Spark read a yard photograph, or count Muse Image/SAM as shipped models on this evidence. The offline clean-render replay remains an experimental artifact.

The proposed chain was tested: known YardState → PIL elevated generated view → Muse Image repaint → preservation gate → SAM/Spark recovery. Muse Image did not retain trustworthy ground truth. Across eight paid repaints, five changed container counts and all eight changed painted identities. Even supplying exact stack/height/ID synthesis manifests did not solve this on development. All four frozen held-out repaints were discarded before recovery scoring:

| Held-out case | True boxes | Visible repainted boxes | Disposition |
|---|---:|---:|---|
| 03 | 16 | 20 | Extra stacks/boxes and altered IDs |
| 04 | 26 | 27 | Extra container and altered IDs |
| 05 | 26 | 26 | Counts preserved; IDs changed |
| 06 | 19 | 23 | Extra stacks/boxes and altered IDs |

The counts were tallied from visible tier faces, row by row, and independently checked by two agents against the clean picture and known state. Per-image audit records and hashes are in `fixtures/case-*/pair_audit.json`. Count-only acceptance would incorrectly trust case 05's changed identities. There are **zero valid held-out photo pairs**, so photo recovery accuracy and the realism drop cannot be reported.

The clean arm measured 4/4 exact SAM counts; 52/74 correct stack heights and 61/87 correct IDs, including all abstentions. Three of four held-out states were complete and correct; case 04 returned malformed Spark JSON and safely produced `needs_answers`. Silent wrong stacks: **0 among 52 accepted stacks**, with 22 other stacks abstained. Mean clean read time: **9.52 seconds**. These are tiny synthetic pilot measurements, not operational accuracy.

The shared `YardRead` API, deterministic validators, Questions, raw evidence and a six-case offline player are implemented. There is no guessed completion, repaired held-out call, hidden failed repaint, network-dependent demo, GPU work or engine modification. Sources and held-out code/prompt were frozen before their scoring passes; see README and fixture manifests.

Spend is **$0.22258775**: SAM/Image **$0.095 of the $5 cap**, Spark **$0.12758775** through the shared ledger, zero vision unknown holds. Failed scene generation and malformed JSON are included. Work stopped within the three-hour time box after the frozen pass established the failure; no further paid search for a cherry-picked photo is warranted.

Leader follow-up: mark 1.5 cut and keep the claims at three shipped Meta models unless another task independently establishes the missing photo feature. No contract changes are required. A future experiment needs images with independently trustworthy geometry and IDs, and real occlusion cases, before integration.

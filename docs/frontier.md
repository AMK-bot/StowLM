# Frontier — where ship planning stands (literature updated 27 Oct 2026)

Use this for the pitch's "related work / why this is new" and before claiming novelty. The 0.4/0.6/0.10 notes
below distinguish full-text readings from abstracts and unverified details. Results are author-reported, not reproduced;
the older industry and DG entries remain press/vendor context and were not audited in this pass.

## Academic

- **Survey:** van Twiller, Sivertsen, Pacino, Jensen (2024), "Literature survey on the container stowage planning
  problem", EJOR 317(3):841–857. Field is small; many formulations are oversimplified, which makes industrial
  relevance hard to judge; calls for representative problem definitions and benchmarks.
  https://arxiv.org/abs/2307.07573
- **Public benchmark:** DTU "Container Stowage Planning – MasterPlanning – Benchmark" (data.dtu.dk, dataset 22293412).
  See [data-sources.md §1](data-sources.md#1-ships-by-size) for the separately verified source/licence notes;
  this is a master-planning benchmark, not a yard relocation benchmark.
- **ITU master planning:** see the paper-reading notes below for AI2STOW, feasibility projection and PPSTOW.
- **Deep RL for slot-level planning:** Leiden (Oct 2025), "A Benchmark Study of Deep RL Algorithms for the Container
  Stowage Planning Problem", [arXiv 2510.02589](https://arxiv.org/abs/2510.02589) — keep separate from the aggregate
  master-planning results below; this paper has not been audited in this reading pass.
- **LLM terminal planning:** LMASS is direct prior work; see the evidence limits below.
- **LLMs that write optimisation models:** OptiMUS (arXiv 2310.06116), ORLM (arXiv 2405.17743; 7B open models fine-tuned
  on synthesised data, in *Operations Research*), MIPLIB-NL (Li et al., arXiv 2602.10450, ICML 2026: 223 real MIPLIB
  2017 problems with reverse-generated NL specs, 10³–10⁶+ variables).
  - Pass@1 from MIPLIB-NL Table 1 (prior benchmark → MIPLIB-NL): ORLM-Llama3-8B 83.71% NLP4LP → **0.58%**;
    SIRL-Qwen2.5-32B 98.30% → 1.43%; OptMATH-32B 92.13% → 5.50%; GPT-5.1 93.82% → 39.05%; Gemini-3-Pro 91.57% → 36.19%;
    Claude Sonnet 4.5 Thinking 92.70% → 30.00%; DeepSeek-V3.2 83.71% → 23.81%.
  - Failures: 48.3% modelling errors (missing/mis-specified constraint families, incomplete index sets, wrong
    variable–data coupling, wrong objective direction), 45.0% execution errors (solver code doesn't run), 6.7% timeouts.
  - Lesson for us: small fine-tuned models that *write* models collapse at industrial scale; even frontier models get
    <40%. So our LLMs never write the optimisation model — they fill a fixed schema or choose among checker-masked
    actions, and the hand-built model/checker owns the maths.
- **LLMs on the IMDG Code:** Thomas et al. (2026), "Evaluating LLM Performance on IMDG Code Compliance" (DGEval,
  arXiv 2608.21036): 1,678 questions on Amendment 42-24, 13 models; best beat human practitioners on multiple choice, but
  all models are weakest on stowage, segregation and regulatory recall. Lesson: segregation must stay deterministic
  in the checker, never left to the LLM.

## Industry

- **Navis (Kaleris) MACS3 + StowMan** — the incumbent pair (vendor figures):
  - **MACS3** = class-approved onboard loading computer (since 1984; ISO 16155; LR, ABS, DNV, BV, ClassNK, KR, CCS).
    Modules: basic stability/strength, BELCO containers, DAGO dangerous goods, SEALASH lashing forces, DastyMAN damage
    stability, crane operations. Ship library 5,000–7,500+ profiles (sources differ); ~65% of container-vessel segment.
  - **StowMan** = shore-side planning tool for shipping lines: multi-port voyage views, planning conditions on board +
    load list, and it pulls MACS3's approved lashing, stability, stress, DG segregation, tanks and trim into planning;
    restows/shift moves, crane split; BAPLIE/EDIFACT import/export; "enabled to integrate stowage optimization".
    Control Center + Distributed Services (client-server) and Vessel Pool (current ship profiles).
    Claimed: 50% of top-10 and 37% of top-30 liners plan ashore with it; ~40 mid-size/small carriers.
  - Mode is **planner-in-the-loop**: tools check and assist, humans plan. ONE's 2019 record (19,100 TEU on the 20,146 TEU
    MOL Tribute, ~95%) was framed as "augmenting human planners". "SAM" semi-automatic assistant with an optimisation
    engine and ML for lashing appeared in an older release — current status unknown.
  - **Evergreen** signed a multiyear StowMan Control Center + Distributed Services + Vessel Pool subscription (Jan
    2022), so a planner at a StowMan carrier would compare us against StowMan.
- **Other AI stowage in production (vendor/press claims):** Ningbo-Zhoushan port AI stowage — ~3,000 units in ~20 min
  (terminal side, 2025); Hyundai Glovis AI stowage for car carriers (Feb 2026), claimed expert-level plans, faster.
- **Lloyd's Register** (2025 report, Jan 2026 suite): BoxMax / Roll Design Assessment notations, LashRightUX lashing
  software, RollRight roll prediction; **LXF consortium** building a common lashing-data standard. Lashing / parametric
  roll is where the class-society frontier is — container losses: 576 (2024) → 1,478 (2025, WSC, skewed by one capsizing).
- **Carrier AI programmes** (e.g. CMA CGM MAIA, 2026) list routing, ETA, energy, booking — stowage isn't named.
  Hyundai Glovis showed AI stowage for car carriers (Feb 2026). No public LLM stowage product from a container line found.

## Frontier benchmark sources (30 Oct 2026)

Full reports: `experiments/mvp/frontier/research/{commercial,academic,repos}.md`. Headlines:

- **No published method covers our objective.** Nothing combines the actual yard, later-port restows, hatch-cover moves and
  full stability, strength, DG and lashing checks at industrial size. Each "best" is for a neighbouring problem.
- **No public code exists** for the leading ship planners (Larsen–Pacino ALNS, Roberti–Pacino, Parreño-Torres, STOW). A
  benchmark has to be re-implemented from the papers.
- **The two-step split is documented practice,** not one expert's view: SMDG's best-practice material describes carrier
  pre-stow by category and terminal adjustment within POD, size type and weight class. What was weak in our baseline is the
  carrier step: it has no real master plan. Published carrier designs use one (Maersk patent EP1909221: LP on class counts
  per storage area across the rotation; Pacino et al. 2011: IP master plan, then CP and local search per slot).
- **Plans are decided across the rotation and issued one port at a time,** rebuilt from the actual departure plan at each
  port. Vendors publish no algorithms; AutoStow is described as weighted penalties per candidate box.
- **Public validation sets:** k-shift (405 + 1,620 instances, proven optima), Larsen & Pacino 27, Junqueira integrated
  yard + ship, Delgado/Parreño slot sets, RCSPPSuite (anonymised real data; evaluation only, D197; results in `experiments/mvp/frontier/public/rcspp/VALIDATION.md`).
- **Newer work to cite:** Sivertsen et al. 2025, "A representative model and benchmark suite" (STOW; paywalled to us), Zhu
  2022 and Huang 2024 (integrated yard and ship at small scale).

**Benchmark built from these (31 Oct):** `experiments/mvp/frontier/`. An emulation, since vendors publish no algorithms. Public
validation: Junqueira HR1 and HR2 published objectives matched on 20 and 19 of 24 small instances; k-shift TR1 all 405 proven optimal
with a new flow model; TR4 mean 1.0 rehandles vs 41.8 published, on a forgiving benchmark and without reproducing insert-and-fix;
Larsen & Pacino all 27 feasible, faithful on S and M only. Results on our maps: `experiments/mvp/frontier/RESULTS.md`.

## Dangerous-goods screening

- **Hazcheck Detect** (Exis Technologies / NCB): API screening of booking data with keywords + rules; NCB says AI over
  200M+ records with human inspectors validating. Users: Maersk (first, 2020), Hapag-Lloyd, PIL; ~30% of global volume
  (older figure). NCB 2020 white paper: ~6.5% of DG containers inspected were misdeclared.
- **WSC (2025 inspections, 93,079 units, 7 countries):** 10.36% had deficiencies; rate rising over a decade.
- **Allianz Safety & Shipping Review 2026:** a containership fire every ~17 days; misdeclared hazardous cargo
  (batteries, chemicals) a leading cause. Wan Hai 503 fire (June 2025): four seafarers died.

## Paper-reading notes for 0.6 (27 Oct 2026)

These are author-reported results, not reproductions. The two ITU papers were read in open full-text HTML;
LMASS full text remains inaccessible in this pass. “Feasible” always means feasible for that paper's model.

### ITU: feasibility projection, arXiv 2502.12756v2

The state is `s=(u,q,ζ)`: utilization by bay/deck/cargo class/origin–destination, observed demand, and fixed
vessel/cargo parameters. One step distributes one origin–destination/cargo class across locations, using **continuous
container quantities**. Cargo embeddings plus positional encoding feed attention; utilization supplies decoder context,
and observed demand updates its dynamic representation. This is master allocation, before individual slots.
([§§2, 4–5](https://arxiv.org/html/2502.12756v2))

A projection changes proposed quantities to satisfy constraints. Weighted scaling reduces over-demand allocation;
clipping enforces coordinate limits; violation projection iterates `x ← x − η Aᵀ max(Ax−b,0)`.
A finite iteration budget is not a general feasibility proof. A penalty in training alone is different from preventing
an invalid action. ([§5.3, Appendix B](https://arxiv.org/html/2502.12756v2))

Table 1, 30 test instances: SAC with scaling/clipping reports **1494.22 profit, 13.12 s, 100% feasible**;
non-anticipative stochastic MIP **1053.02, 8434.58 s, 100%**. Perfect-information MIP's **1713.73** is an
unrealistic information advantage. Revenue minus hatch-overstowage and excess-crane-work costs is the objective;
hatch overstowage counts blocking containers, not an explicit unload/reload action trace. These are neither
minimum-move optima nor StowLM checker guarantees. ([Table 1; Appendix A.5](https://arxiv.org/html/2502.12756v2))

### ITU: AI2STOW, arXiv 2504.04469v1

AI2STOW adds paired blocks: one POD per bay/block across decks. Its mask preserves same-POD locations and selects
empty bay/block pairs using symmetric random scores and sufficient capacity. Masking precedes convex projection;
it is a quantity mask, not an LLM token mask. ([Algorithm 1–3](https://arxiv.org/html/2504.04469v1))

On a **20,000 TEU** simulated vessel, policies trained on four ports are tested on 30 instances per horizon.
Table 4 reports profit / seconds / feasible percentage:

| Inference mechanism | 4 ports | 6 ports |
|---|---|---|
| Mask + convex-program projection | 25637.52 / 76.37 / 100 | 43496.30 / 182.63 / 100 |
| Mask + violation projection | 25352.06 / 10.59 / 86.67 | 42515.64 / 38.79 / 0 |
| Mask + violation projection + clipping | 25346.14 / 10.33 / 100 | 41979.11 / 38.09 / 100 |

These are continuous master allocations, multiple-rollout inference and revenue-minus-cost objectives; hatch-overstowage
is counted in containers, excess crane work against a target. Stack/yard action traces and physical hatch-cover lifts
are not the objective units. ([§4, §6, Table 4](https://arxiv.org/html/2504.04469v1))

**Code is public:** the paper links [OptimalPursuit/navigating_uncertainty_in_mpp](https://github.com/OptimalPursuit/navigating_uncertainty_in_mpp).
The repository exposes environment, models, RL algorithms, results and stochastic-MIP code under an
[MIT licence](https://github.com/OptimalPursuit/navigating_uncertainty_in_mpp/blob/master/LICENSE).
The README still names the earlier paper; a matching AI2STOW release/commit has not been reproduced here.

### PPSTOW and LMASS: limits of available evidence

**PPSTOW** means Proximal Policy Optimization for Master Stowage Planning. The [ITU record](https://researcher.itu.dk/en/publications/ppstow-an-end-to-end-deep-reinforcement-learning-model-for-master/)
is a peer-reviewed EURO 2024 conference abstract, dated 30 June 2024. It reports near-optimal solutions on simulated,
realistic-sized instances, but provides no numerical table, encoding, move-count definition or masking/projection
algorithm in that record. Do not silently transfer AI2STOW's details or numbers to PPSTOW.

**LMASS**, Li et al., *Artificial Intelligence for Transportation* **6**, 100062,
[DOI 10.1016/j.ait.2026.100062](https://doi.org/10.1016/j.ait.2026.100062): the accessible publisher abstract/introduction
describes an LLM semantic interpretation pipeline, an Organizing Agent coordinating specialized agents, and executable
multi-stage OR workflows. It reports **>99% stowage rate, 100% hard-constraint satisfaction, 8.31 minutes**, using
Shanghai Yangshan terminal data. These are reported task-specific outcomes, not proof of universal feasibility.
Publisher §1 (Introduction, HTML; no page numbers exposed) also describes generating mathematical models and
heuristic algorithms. Thus the title's “text to slot” does not establish direct slot-action decoding, and
“solver-only orchestration” is too narrow a characterization of the accessible evidence.
The full publisher page returned HTTP 403 again on 27 Oct 2026; exact-title/PDF/manuscript and author/LMASS searches
found no open author manuscript. The [OpenAlex record](https://api.openalex.org/works/doi:10.1016/j.ait.2026.100062)
reported closed access, no OA URL and no repository full text; that is an access-search result, not proof that no
lawful copy exists. No login or expert contact was attempted.
**Open:** exact LLM, agent/solver boundary, state representation, masking/projection, move/restow objective and units,
constraint coverage, sample sizes, hardware, and code/data release/licence. Consequently do not claim it ignores
stability, cannot optimize moves, or has unpublished code/data as an established fact. A full-text reading of LMASS,
as requested by 0.6, is **still open**. To close it, obtain a lawfully accessible full paper, then record its section
and page evidence for the state/input schema, agent-generated artifacts and solver execution, action-validity
mechanism (including an explicit absence if confirmed), objective terms and restow/move units, and the reported
evaluation settings. A publicly indexed abstract or section snippet cannot close that requirement.
([Publisher abstract/introduction](https://www.sciencedirect.com/science/article/abs/pii/S3050860626000177))

#### LMASS lawful full-text search — 28 Oct 2026

**Outcome: no lawful, openly accessible full text found; 0.6 remains open.** This is a dated search result,
not proof that no open copy exists. The exact title searched was *From text to slot: a multi-agent framework
for terminal-oriented container stowage planning via large language models*, by Yufei Li, Peixiang Wang,
Wei Qin, Qihang Xu, Mingye Mo, Qunlong Chen, Runzhi Tan, Heng Huang and Ying Zou; DOI as above.

| Source tried | Result on 28 Oct 2026 |
|---|---|
| [Publisher article](https://www.sciencedirect.com/science/article/pii/S3050860626000177) and [abstract page](https://www.sciencedirect.com/science/article/abs/pii/S3050860626000177) | Both direct opens returned HTTP 403. Public search-index previews expose the abstract, introduction and section snippets, not the full paper. No restriction was bypassed. |
| [OpenAlex DOI lookup](https://api.openalex.org/works/doi:10.1016/j.ait.2026.100062) | Record W7161284001: `is_oa=false`, `oa_status=closed`, `oa_url=null`, `any_repository_has_fulltext=false`, `best_oa_location=null`; sole location has no PDF URL or licence. |
| [Crossref DOI metadata](https://api.crossref.org/works/10.1016/j.ait.2026.100062) | Confirms title/authors. Licence entries are marked `content-version: tdm`; article links are for text mining. These entries do not establish an open-access full-text licence. |
| [ResearchGate publication](https://www.researchgate.net/publication/404928655_From_text_to_slot_a_multi-agent_framework_for_terminal-oriented_container_stowage_planning_via_large_language_models) and [Peixiang Wang profile](https://www.researchgate.net/profile/Peixiang-Wang-7) | Publication explicitly says “No full-text available” and offers an author request. Profile lists the journal article; no identifiable open preprint/conference counterpart found. No request or login attempted. |
| [arXiv exact-title API query](https://export.arxiv.org/api/query?search_query=ti:%22From%20text%20to%20slot%22&start=0&max_results=20) | Zero results. Additional `all:LMASS` and `all:stowage AND all:"language models"` queries returned unrelated papers, not LMASS. |
| [CORE DOI query](https://api.core.ac.uk/v3/search/works?q=doi%3A%2210.1016%2Fj.ait.2026.100062%22) | HTTP 200, `totalHits: 0`. A separate exact-title query returned HTTP 500; that failure is not evidence of absence. |
| SSRN | Indexed searches `site:ssrn.com "LMASS"` and `site:ssrn.com "container stowage" "Li"` found no matching manuscript. Direct SSRN search was inaccessible; its internal catalogue was not successfully searched. |
| Author/institutional repositories and theses | Exact title with `pdf`, `repository`, `preprint`, `thesis`; author-pair searches; `"LMASS" "集装箱"`; and searches restricted to SJTU, Zenodo, Figshare, OSF, Handle and institutional/thesis domains found no relevant downloadable copy. [Wei Qin's SJTU faculty page](https://me.sjtu.edu.cn/en/FullTimeTeacher/qinwei.html) was indexed, but direct opens timed out; its publications were not fully inspected. |
| Conference/workshop versions | No matching version found. The author-cluster hit in [IEEM 2024 contents](https://www.proceedings.com/content/078/078657webtoc.pdf) is *Scheduling Problem of Empty Container Trucks Considering the Uncertainty of Bulk Cargo Weight at the Terminal*, a different paper; it is not treated as LMASS evidence. |
| [Unpaywall DOI API](https://api.unpaywall.org/v2/10.1016/j.ait.2026.100062) | HTTP 422, email required. No email supplied; this service was not successfully searched. |

**Licence/download status:** no open full-text licence established and no paper downloaded, so there is no
paper artifact or MANIFEST under `data/raw/lmass/`. Only public, unauthenticated sources were searched;
no author contact, purchase, login, shadow library or paywall circumvention was used.

**Transfer status after this search:**

- **Action masking:** unverified. Do not infer either token/action masking or its absence from the title or preview.
- **State encoding:** unverified beyond the introduction's high-level yard-container, vessel-slot and natural-language
  inputs; no implementable schema or dynamic state update is established.
- **Move/restow counting:** unverified. The available evidence does not establish objective units or a physical
  removal/return accounting rule that StowLM can adopt.
- **Reported numbers:** retain the abstract's **>99% stowage rate, 100% hard-constraint satisfaction, 8.31 minutes**
  as author claims, not reproduced results or checker guarantees. The publisher's *Dataset and experiment setup*
  snippet names four task sizes (**364, 606, 1,223, 2,005 containers**) and **LangChain v0.3**; a snippet does not
  supply the full evaluation protocol or show how the headline figures aggregate across tasks.
  ([Publisher preview](https://www.sciencedirect.com/science/article/abs/pii/S3050860626000177))

**StowLM implication (our inference):** LMASS remains relevant prior work for translating language requirements
into executable optimization workflows. No new paper-grounded mask, encoding or move-count implementation can be
transferred from this search. Keep the checker and explicit move accounting as StowLM design choices, and keep
the full-text reading requirement open until a lawful complete manuscript is obtained and read.

### What transfers to 3.8 / 4.4 / 5.1

**Our design inference:** encode occupancy, destination/category, remaining demand and residual resources; update after
every action. Use masking before sampling, then check the executed integer sequence, including repaired actions.
([ITU §5](https://arxiv.org/html/2502.12756v2))

**Our accounting recommendation:** count yard relocations, restow removals/returns and hatch-cover lifts separately.
Do not relabel profit or excess-crane-work as total moves. A feasible partial action need not admit a complete plan.
([AI2STOW §5–6](https://arxiv.org/html/2504.04469v1))

## Where our project sits: novelty check for 0.4 / 0.10

**Do not claim “first LLM to minimize container moves.”** LMASS already connects language and stowage workflows.
In adjacent rearrangement problems, [CEoH (Bömer et al., arXiv 2503.03350)](https://arxiv.org/abs/2503.03350)
uses LLM-generated state-scoring heuristics in tree search for unit-load pre-marshalling; its setting is a one-tier,
one-direction warehouse rather than vertical ship/yard stacks. [A-CEoH (arXiv 2601.19622)](https://arxiv.org/abs/2601.19622)
extends this direction to A* search heuristics. An LLM can therefore help reduce rearrangement moves without emitting
each physical action itself.

Nor is “LLM plus deterministic feasibility” sufficient novelty:
[CoupleEvo (arXiv 2605.06341)](https://arxiv.org/abs/2605.06341) evolves LLM-generated large-neighborhood-search destroy
functions and uses deterministic repair for multi-robot, multi-bay warehouse pre-marshalling. Large neighborhood search
removes a substantial part of a candidate solution, then rebuilds it to seek an improvement. Its objective is makespan
(elapsed completion time), distinct from simply counting moves; [code is public](https://github.com/tb-git-kit-research/CoupleEvo).

**Working distinction, not an established priority claim:** StowLM aims to train an LLM to choose discrete actions
under a feasibility mask for the combined current-yard-to-ship task, minimize an explicitly checked multi-port move
count, and publish lower-bound/solver gaps and checker/fallback rates. The reviewed papers do not establish that exact
combination, but LMASS's unresolved full-text details prevent a confident exclusivity claim. Demonstrating these
behaviors is project work still to do; schema-constrained decoding, checker-based evaluation and low move counts are
not proven merely by proposing them.

The parked booking/DG-screen join is no longer the leading pitch. Treat StowLM as a planner-assistance layer whose
plans must pass the checker; do not describe it as a replacement for class-approved loading software. The industry
comparisons above remain vendor/press claims and were not re-audited in this literature pass.

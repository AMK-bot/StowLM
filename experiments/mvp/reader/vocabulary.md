# Reader vocabulary and execution contract

This experiment implements D199 route 3. The three existing production classes
`CloseBay`, `MaxTiers`, `DgKeepAway`, and `Refusal` are imported unchanged. The only
new constraint is `OnePortPerHatch`, encoded as `{"type":"one_port_per_hatch"}`.
The envelope is versioned `stowlm.reader/2`; `contract.json` contains the schema,
frozen system prompt, prompt digest, and a synthetic ship context for sealed review.

`CloseBay` excludes loading/restowing into a named bay, with a 40-foot even bay
covering its two odd neighbours. `MaxTiers` excludes tiers above a per-stack cap
in the explicit hold/deck zone; zero closes that zone. `DgKeepAway` excludes
placements of the named IMDG class/division too close to a bay/accommodation.
One numbered 20-foot bay step means 01 to 03, not one ISO bay number increment.

`OnePortPerHatch` means at most one discharge port per **physical hatch cover**,
across both its hold and deck cells. Port and starboard covers are separate
physical covers. Empty covers are allowed. A long slot must satisfy every cover
touched by its physical footprint. Retained onboard cargo participates in the
restriction and is not authorized to move. Incompatible retained cargo means
infeasible, rather than silently weakening the instruction. A request limited
to half a cover or only one zone is unsupported by this global type.

No type opens a bay, increases a tier limit, reduces an existing DG separation,
permits mixed ports, or overrides a checker rule. Language requesting these
actions must return `Refusal(code="loosening")`, even when another clause tightens
something. Contextual validation rejects nonexistent named bays, impossible tier
counts, missing accommodation, and invalid IMDG divisions. It never substitutes
the nearest real bay. An explicit restriction can be infeasible; parsing it does
not certify that a valid plan exists. The hard physical maximum bounds tier
values, while `min_bays` retains the existing supported range 1..20.

Context exposes only synthetic/public vessel values: valid odd/even bay names,
physical hold/deck tier counts, optional bay-specific counts, accommodation
presence, physical cover IDs, and rotation PODs. `context_from_instance` derives
these values without using a solver or target plan. Training instances must be
selected through explicit train manifest records; sealed evaluation uses its
fixed contract context.

## Experiment-only adapters, with no source changes

The old three types already compile through
`llm.instructions.apply.apply(instance, constraint)` to forbidden `(box, cell)`
pairs. The existing `InstructionEngine` wraps a feasibility engine and removes
those placements from its legal-index arrays, legal actions, and `push` path.
It preserves all underlying legality checks and shares its immutable forbidden
table across copies.

For the new type, an experiment adapter can enumerate strict cover-to-POD
assignments (or choose a deterministic one using only manifest and geometry),
then compile each assignment to per-box forbidden cells. An assignment may
associate each cover with zero or one POD; every retained onboard placement
first fixes its cover's POD. Reject conflicting retained cargo or any layout
that assigns multiple PODs to a cover. All unassigned/different-POD cover cells
are forbidden for that box. Intersect these permissions with previous
constraints and the original feasible set; do not replace either. This is a
conservative implementation of the global type: it may lose feasible plans but
cannot admit a mixed-port cover. Exhaustive cover assignments recover the full
global rule if desired. The current block-rule's soft sharing when ports exceed
stations must **not** be used as a strict instruction implementation.

Frontier 0.9's `alns.Problem` creates the feasibility engine used by `Rebuilder`,
`Sequencer`, and `_mask_ok`. An experiment subclass can replace `problem.engine`
with the same `InstructionEngine` and remove forbidden pairs from its **copied**
`T.static` table. The table restriction is essential because `MasterModel`
constructs candidates directly from `T.static`. Filter `slots_by_length` as
needed, reject any start in `Problem.score` that violates the compiled predicate,
and pass this constrained `problem` into `search(..., problem=problem)`. The
stock `plan_and_check` constructs an unconstrained `Problem`, so invoking it
unchanged is **not** an instruction adapter. An experiment driver must instead
construct constrained starts and use the public search/finisher seams, or copy
the orchestration outside `src/`. The finisher's output must be checked again
against the same instruction, followed by the full production checker.

These are concrete integration seams, not a claim that this data/harness task
has run a constrained frontier replan. No production source is changed here.

## Scoring and binding claims

All arms receive the identical short `schema.system_prompt()` and
`schema.messages(sentence, context)`. Strict structured exact match ignores only
the free-form `refusal.reason`; refusal codes, fields, types and values count.
Malformed answers count incorrect and are not repaired. A loosening sentence
is falsely accepted whenever an arm emits a valid constraint, whether or not
that constraint happens to change a plan. Report raw valid JSON separately from
context-accepted constraints if relevant.

D166 ruled that merely finding an instruction already satisfied by the old
plan is not a replan. D170 demonstrated a binding case by pre-registering a
sentence that the original plan violated, then parsing it, producing changed
placements under the instruction mask, independently checking instruction
honour, and passing the full checker. The same evidence is required before
claiming this reader contributes to a real binding frontier replan. R1/R2/R3
on sealed sentences establish reader performance, not a plan-cost improvement.

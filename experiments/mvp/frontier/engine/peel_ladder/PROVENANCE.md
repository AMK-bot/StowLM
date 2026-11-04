Frozen C5 assignment peeling
===========================

The eight controller/LP files assignment_method.py, assignment_proposal.py,
assignment_masks.py, elastic.py, assignment_runtime.py, assignment_records.py,
finishing.py and tiers.py come from lab/c5/c5-peel. Every donor SHA256 was
verified against assignment-frozen-hashes.json before copying. Only a one-line
provenance comment was prepended. copied-manifest.json records both original
and copied hashes for those files and the 16 required local dependencies.
Their measured algorithm bodies are unchanged. The original frozen hash
manifest is retained here; no engine_snapshot or mutable engine.assignment
implementation is imported by this runtime.

The required dependencies are the donor alns.py and masks.py, its C5 securing
method/stow/gap/rows/chainlab, C4 page/moment/sight/balancing/dense modules,
OOG repair and ballast physics/optimizer, and C1 E9 helper. Relative vendor
layout is preserved. runtime.py is an engine adapter; loader.py isolates and
restores their legacy absolute import names, sys.path and SciPy entry points.
The native _e9_batch method and rules_special._parameter are snapshotted before
private imports and restored on normal, exceptional and failed-import exits,
including when the private donor installer is explicitly called. This preserves
the root E9 ablation and prevents repeated wrappers across invocations.
Both LP/MILP wrappers force one numerical thread. Native checker functions and
input instances are never patched. Tiers refer to the byte-identical root
engine/tiers.toml; its donor hash is verified at runtime.

Public API: run(instance, everything_found, seed=0, budgets=None) returns D242
records, with defaults proposal_s=240, finish_s=120 per level, max_candidates=2.
The already-failed ordinary search is supplied through normal_call, so it is
not repeated. Candidates and physical references come only from this invocation.
Tiny/small size metadata is inactive even above 60 loads; medium/large/full is
active; missing metadata uses more than 60 loads. A held full pass rejects the
call. rule-freeze-v1 and native frozen-interface overrides fail closed.

The unchanged copied controller searches L1 through L5, allows partial cargo
only at L3, retains the best safe partial separately, and stops at the first
completed level. Unknown/T0 hard breaches and incomplete native checker reports
are never eligible. Every relaxed record has passed=False, relaxed=True;
original native verdicts remain as full_checker_passed and checker_report.
Restricted LP/integer exhaustion is never map infeasibility.

Copied tests cover tier policy, preemptive elastic matrices, assignment masks,
partial OOG repairs, and controller cargo/level ordering. Two historical archive
count tests were omitted because their old diagnostic archives are not runtime
inputs or algorithm tests. The test-only pre-tagging gap reference is copied
from lab/c5/c5-securing/gap.py, solely for the donor's matrix-preservation test.
Engine tests additionally cover size routing, no-pass guards, exact hashes,
namespace restoration on error, and L3 partial followed by L4 completion.

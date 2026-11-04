# Pre-generation seed-audit clarification

No map has been generated. Seeds, cells, sizing, selection and derivation are
unchanged. The first full scan checked 5,892,987 accessible paths and 15,493
metadata files, with no access errors. It found four lexical candidate hits
for 306110301, all the fractional digits of a physical diagnostic number
2509850.306110301 in copies of prior TRAIN requalification metadata.

A fractional part of a JSON decimal is not a root seed. JSON metadata is now
parsed **only when lexical seed candidates exist**: integer-valued numbers,
strings and object keys retain conservative matching; other floats do not.
Malformed JSON conservatively retains all lexical candidates. Paths and
non-JSON metadata retain the original matcher. This does not change scope or
remove real integer/string seed evidence. No raw map file is selected/parsed.

Preserve the original full report as SEED-AUDIT-PRE-RAW.json and its exact
auditor source as seed_audit_lexical_v1.py. resolve_seed_audit.py verifies both,
confirms that every candidate-containing file still matches its full-scan hash,
and resolves every candidate using the clarified JSON rule. Since the fixed
56 seeds are unchanged and the new rule only removes decimal false positives,
all original path and zero-hit file evidence remains valid without rescanning
it. The resolved report explicitly records this method and original hashes.
The post-generation audit uses the clarified auditor over the full scope again.

Review additionally required retaining duplicate JSON object keys, so an earlier
seed value cannot disappear under a later key. Candidate parsing preserves all
object pairs. An initial registration completed while that fix was reviewed;
it was archived as requests-pre-key-preservation.json before any map or status
file existed, then the resolved evidence and registration were refreshed.
No seed, generation control, map output or selection rule changed.

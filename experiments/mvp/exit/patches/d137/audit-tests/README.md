# D137 baseline label patch

`../audit-label.diff` changes the staged D132 audit implementation and adds 15
CPU regression cases. It does not change pass/move thresholds or the identity
of legacy 8.1 registrations. New 8.2 audit rules and terminal records say
`generation-0 8.2 baseline`, `keep the 8.2 adapter`, and `keep_sft82`.

Runner hooks:

- Add `--baseline-label` (default `8.1`; restart script uses `8.2`).
- Pass `baseline_label=getattr(args,'baseline_label','8.1')` at every
  `frozen_audit_paths(...)` and `audit_decision(...)` call.
- Include `baseline_label` in the initial baseline binding for nonlegacy
  labels, so it cannot be changed after measurement. Leaving it out for 8.1
  preserves existing baseline-cache compatibility.
- `persist_stop` and `reconcile_failed_gate` accept optional `baseline_label`.
  They otherwise derive it from the audit rule or durable registration. Wrong
  explicit/registered labels fail before publishing a terminal. They never
  infer the fallback adapter from the failed child.

Validation: `UV_CACHE_DIR=$PWD/.uv-cache uv run pytest
experiments/mvp/exit/patches/d137/audit-tests -q`: **15 passed**, 5.34 s tool wall.
The old audit suite with only the staged audit module substituted yielded
22 passes and one unrelated D132 cache-key fixture failure; that fixture still
mocks broad source pins while the new runner keys audit rows by outcome epoch.
It failed on malformed `{}` input before any GPU call. No source files edited.

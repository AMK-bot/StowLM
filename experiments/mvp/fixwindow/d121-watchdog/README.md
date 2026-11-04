# D121 watchdog proposals — unapplied

Ready for the **post-fix-window owner boundary**, before gold adopts the new
checker. No live source or frozen branch was modified.

See [INVENTORY.md](INVENTORY.md) for exact scope, other-caller risks, commands,
nested timer behavior and test limits. [PATCHES.json](PATCHES.json) pins each
source and candidate; `VERIFY.json` records final patch hashes/applicability.

Prepared patches: gold driver, smoke runner, look-ahead CPU cap, and loop GPU
smoke bookkeeping. Native/physical acceptance stays unchanged. A regular
`llm.exit.selection.verify_candidate` subprocess/watchdog policy remains an
owner follow-up; no new deadline threshold is added here.

Final helper receipt: **45 passed, 3 intentional skips, 0 failures/errors,
0.415 s**. Separate cold_analysis receipt: **4/4 synthetic checker/integration
guards passed**. All commands completed; no owned background process remains.
No actual signals, model/GPU calls, corpus bodies or numerical builders ran.

Final read-only `git apply --check` passed for all four patches. Recheck after
the producer-owner boundary; do not apply from this preparation handoff.

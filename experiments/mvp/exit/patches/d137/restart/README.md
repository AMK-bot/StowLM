D137 explicit host restart — NOT LAUNCHED

After the leader installs and reviews the combined D132/D137 batch and publishes
experiments/mvp/exit/patches/d137/installed-manifest.json:

zsh experiments/mvp/exit/patches/d137/RESTART-HOST.sh --check-only
zsh experiments/mvp/exit/patches/d137/RESTART-HOST.sh

Only a Claude/Metal-capable host may run the second command. Codex did not launch
it. --check-only validates the installation and reads old schedule metadata; it
never imports MLX, writes queue state or starts a process. A missing installation
receipt refuses before queue creation. The launcher never installs any patch.

Required receipt schema:
{"schema":"exit-d137-installed-v1","targets":{"src/llm/exit/runner.py":"<sha256>",...}}
The nonempty target map must include runner.py, data.py, control.py, selection.py
and dpo.py. Every listed file must match exactly. Root owns the real receipt;
this folder deliberately contains no fabricated installed marker.

Parent: experiments/mvp/sft/stowlm-v2/adapter. Its sibling DONE.json must identify
that exact adapter; the owner's legacy marker text "8.1 SFT complete" is accepted
without relabelling the actual baseline, which is explicitly 8.2. The controller
still waits for fix-t8-sft's GPU ledger row to disappear and the shared GPU lock
to become free. It retains review, numeric ledger, RAM, current-code smoke,
securing-active and cache-limit gates. A stale smoke from 8.1 cannot admit 8.2.

Fresh output: experiments/mvp/exit/deployment-d137. Initial defaults: 12 maps,
k=6, P=2, B=8, 100 SFT steps, max 4 verification workers dynamically capped by
existing capacity policy; gold-map-fraction=.5, teacher planning=120 s, finisher
budget=120 s, LNS=15 s, DPO enabled, fixed audit set/audit-every=2 retained.
The first actual generation launch freezes launch-clock.json with an audit due
150 minutes later; controller restarts do not slide or reset this audit clock.
There is no stale 05:30 clock or not-before delay.

The new schedule starts from the union of all prior deployment*/schedule.json
used input hashes, seeds and instance IDs, plus their generation reservations.
It resets generation numbering and validation selection only in the fresh output.
At staging time three old schedules unioned to 73 hashes, 73 seeds and 73 IDs.
No corpus instances, especially held-out inputs, were opened by this operation.
Prior schedule content hashes and imported exclusion inventories are pinned.
Both are rechecked before every smoke/runner launch; change means wait/refuse,
never silent reuse. A live resumed schedule is never overwritten.

Normal runner epoch boundaries, re-verification, elitism and gate stop state
remain authoritative. Cancelled/terminal D137 directories are never revived.
All processes use native thread limits 1 and MLX cache limit 2 GiB. Controller
and watcher PIDs, exact command, installation manifest and lineage inventory
are recorded under the fresh output when the host explicitly queues it.

Verification:
UV_CACHE_DIR=$PWD/.uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 uv run pytest experiments/mvp/exit/patches/d137/restart/tests -q
20 CPU tests passed. Both Python --help commands and zsh -n passed. No GPU,
heavy job, paid call or external service was used.

# C1+C2 v3 accepted subset released

All **2,240 requests are processed**: 595 accepted instances and 1,645 rejected
request audits. Complete artifact verification passed (exit 0, 6.7197595 s).
The accepted subset is released for solvers and task 6.1 through manifest
`shards` lists. `rejected_shards` are evidence, never solver or gold-plan input.
No full-checker-approved plan is claimed.

**Accepted coverage remains incomplete:** tiny has zero accepted instances;
medium has 132 feeder_1700 instances, all frozen test, and no train/val examples.
Small accepts only feeder_500. Accepted splits total 264 train / 20 val / 311
test; rejected splits are 938 / 52 / 655. Root `full` includes the realistic
profiles: 223 accepted = 112 ordinary full + 59 realistic_500 + 52 realistic_1000.
Mass-limited random realistic targets are 348/575 boxes; simulated targets range
342–356 / 565–588. All accepted DG bookings total 3,052 across 438 instances.

Qualified cache warm-up passed in 34.303888 s. Both release test commands exited
0: scoped 194.400 s, whole suite 1205.256 s. Main generation completed in
5780.958 s (96.35 min); see `COMPUTE.md` for all estimates and actual timings.

Frozen split keys and admitted physics are unchanged. The supplemental policy
bank pin was captured after main start and matched at final verification; it
does not certify earlier unobserved bank history. File mtime is supporting
evidence only. `verification.json`, `RESULTS.md` and `policy-bank-pin.json` retain
the evidence and limitation. Historical prototype epochs under `_benchmarks/`
remain benchmark evidence, not released solver instances.

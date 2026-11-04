# D132 legal-set qualification

**PASS:** all 214 permitted traces, 25,454 token prefixes and 618 engine probes.
The 23 changes are additions at heat-relevant prefixes; there are no deletions.
One TRAIN terminal prefix gains immediate escape. Final TOML source pins match.
No production install or full-plan cure is claimed.

- [Results, controls, exact commands and compute receipts](REPORT.md)
- `comparison.json`: actual before/after legal arrays and change classification.
- `source-qualification.json`: assembly/source/asset and final TOML evidence pins.
- `prepared/manifest.json`: exact TRAIN/VAL/fixture scope and input/script hashes.
- `baseline/`, `candidate/`: every fresh per-prefix and engine event.
- `combined-fast-tests.xml`: 20 synthetic/policy tests passed in 0.613 s.

`guard.py prepare` checks metadata policy before reading an input, and creates
the fixed operation scripts. Run each arm separately via the strict `owned.py`
wrapper after fresh `admission.py` and successful `ledger.py reserve` calls;
do not chain reservation and worker launch. Then run `guard.py compare` and
`qualify_sources.py`. Refused admission queues zero cores and launches nothing.
`STOP.json` prevents new workers and stops an active arm between traces.

Do not substitute TEST inputs. Do not regenerate seeded walks independently
after legal sets grow: replay the same committed prefixes/operations. A changed
source revision requires new qualification artifacts. The 200 minimal fixtures
exercise mask state mechanics with documented unknown-data securing fallback;
all 14 real TRAIN/VAL traces require active securing and had zero fail-open.

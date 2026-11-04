# Test v3 — SEALED (D187 / D174)

45 fresh synthetic maps: **15 tiny +30 small** across feeder_500, feeder_800 and
feeder_1700. Each ship has five tiny maps and ten small maps; small is balanced
five random-yard/five simulated-yard per ship. No medium or full-ship map.

**Sealed:** no loading planner, solving, plan quality, diagnosis or tuning has
been run or is authorized on these maps. Only the frozen generator's ordinary
validity checks ran: necessary screens, D58 fixed-background witness checks,
clean provenance and serialization round-trip. D58's internal background ballast
witness is generator acceptance, not a loading plan for these target boxes.
No loading-plan feasibility or quality is claimed.

Tiny seeds: **187000000–187000014**. Small: **187100000–187100029**.
Identity guards cover current and archived train/val/test/showcase metadata,
including requested-but-unemitted and rejected showcase seeds; raw map hashes
are also disjoint. Existing raw maps were not opened for these guards.

Current code/parameters/hull inputs were copied into a hash-pinned immutable
epoch; existing numerical acceleration artifacts retain the normal generator
qualification checks. The source pin and each audit bind actual inputs/runtime.
Rule-freeze OFF; C2 global screen OFF; generation uses legacy ballast environment.
Small ship slack/mass settings: 500=.25/.55, 800=.25/.45, 1700=.35/.45.

Assumption: the explicit D187 **15+30** count takes precedence over v2.1's actual
15+15. Small repeats each old stratum with the odd family assignment reversed.
The shared ship/family strata measure fresh familiar-family draws.

`manifest.json` lists identities, source/schema versions and checks. `SHA256SUMS`
binds raw maps, audits and release metadata. Files become read-only after sealing.
Do not unseal or run a planner without a separate leader order.

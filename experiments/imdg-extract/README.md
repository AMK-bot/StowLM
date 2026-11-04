# IMDG 42-24 data handoff

Runtime data is in `src/stowage/rules/imdg/data/`; `tables.py` loads immutable,
cached dictionary indexes without a PDF dependency. Catalogue identifiers match
without a catalogue rename. `dgl.py` owns the curated Dangerous Goods List and
its US HMT cross-check. Individual extraction notes document page verification.

Example (with `PYTHONPATH=src uv run python`):

```python
from stowage.rules.imdg.tables import general_segregation, containership_segregation

cell = general_segregation('3', '5.1')
assert cell.level == 2
requirements = containership_segregation(
    cell.level, ship_type='closed_holds', orientation='fore_aft',
    container_pair='closed_closed', deck='under',
)
# requirements.alternatives is an OR: one container space OR a bulkhead,
# with the source's barrier resistance and intervening-bulkhead clearance.
print(requirements.text, requirements.source)
```

Consumers must evaluate the applicable horizontal and vertical requirements,
DGL precedence and special provisions, subsidiary hazards, chemical reactivity,
class-1 compatibility, and mixed-deck sensitive vertical lines. `X` and `*` are
consultation instructions, never zero separation. A box's UN number alone may
have several packing-group/description variants; select the actual declaration.

This task supplies exact data, not a stand-alone compliance checker. No
approximation was introduced. Later corrigenda to the supplied adopted 42-24
resolution were not checked; the edition and source snapshot remain explicit.
No heavy compute jobs were used (single-process scripts/tests, no GPU).

`tables.segregation_codes()` also returns all 24 SG/SGG meanings assigned in
the sample, with source pointers. These retain external references (e.g. SG72
points to 7.2.6.3); they do not infer chemical group membership or implement
exception conditions. `build_segregation_codes.py` reproduces that transcription.


Generator-profile follow-up: 53 DGL rows / 41 UN numbers; all UN/PG variants
requested by the default feeder-export profile are present. UN0336 retains
1.4G/category02 and remains blocked by checker 2.4's unsupported class-1 stowage
rejection. UN2915 retains the genuine SW20/SW21 codes; these only modify
categories for particular materials, so they do not resolve radiation limits.
The source-traced `checker_guards.json` marks its 7.1.4.5 evaluation unresolved.
`entry.source_stowage_codes` quotes the DGL exactly; `entry.stowage_codes` adds
`IMDG:7.1.4.5` for checker integration, and `entry.unresolved_provisions` exposes
the guard definition. It is an internal identifier, not an IMDG SW code.
The existing checker rejects missing external `verified_provisions` for this
guard, even if the box declaration omits it and SW20/SW21 were verified.
External verification must cover all applicable 7.1.4.5 shipment and voyage
conditions; the planner cannot grant it. SP172 hazards separately require
`subsidiary_hazards_verified`. No checker, generator, profile or model edits
were needed. Tests exercise the actual checker for these fail-closed paths.

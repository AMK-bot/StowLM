# Independent source and witness audit

The unchanged full checker accepts the two positive controls on 1 November
2026: the 88-box D192 map at 71 lifts and the 162-box D192 map at 116 lifts.
Both have legal replay, every load-list box aboard, exact load counts, and zero
violations. Rule freeze v1 is **false**. `validity.json` binds raw inputs,
selected evidence, final plans and native checker/vessel sources; `validity/`
retains both fresh reports and plans. These checks establish feasibility of
those controls only. They are not new D185 passes.

`audit.py` reads only the fixed `medium12` and `quick_medium` input paths. Its
independent aggregate witness verifier uses the raw JSON, never the relaxation
builder. It checks the exact class partition and attributes, representative
slot domains, physical cell capacities, aggregate prefix occupancy, bay-level
mass and physical height, cargo moment coefficients and reported deck counts.
Accumulations use rational arithmetic on decoded binary floating values. The
2e-6 residual allowance qualifies approximate primal witnesses only. It cannot
turn numerical infeasibility into a proof. The separately available exact
Farkas verifier proves inconsistency only of its explicitly supplied rows;
their physical outwardness remains a separate obligation.

The packing map is outward for empty decision cargo: every real slot has a
bay/deck/ordinal signature; forgetting its row and stack enlarges choices.
Each class keeps exact mass and height. An actual stack's total mass is at
most the largest of its three composition-dependent mass limits. Summing
these maxima is therefore optimistic. Summed physical heights and occupied
cells are necessary. Supported occupied cells imply aggregate prefix
occupancy. Every lower supporting box has ordinary height at least the
global minimum, so `base + ordinal*minimum_height + own_height/2` lowers
stability's cargo CG. This is a moment coefficient for stability's provider;
it is not an exact CG for sight or securing.

Source-audit hazards that must stay explicit:

- Stability and securing/sight use different liquid, cargo CG and free-surface
  providers. A certified limit on stability GM or draught does not by itself
  constrain securing's GM/draught. In particular, stability ordinary cargo
  heights omit OOG expansion while the sight provider uses its full envelope.
- Sight depends transformed roof corners, trim and heel. An upright roof cap
  without an outward pose envelope can reject a legal map.
- The second-check form factor uses draught, corrected KG, half-draught hull
  breadth, waterplane coefficient and coaming depth. Its positive-area limit
  also depends the finite-angle, free-trim, worst-side righting curve and
  liquid correction. `GM*angle` is not a certified area upper envelope.
- D57 checks departure, midpoint and arrival, with consumable drawdown. Shared
  adjustable ballast does not make the providers or the phases identical.
- Restricted page-pool infeasibility proves only exhaustion of that pool.
  The full checker remains mandatory for every proposed finished plan.

The inherited `early_bound.py` is a fixed-stow diagnostic. Its positive
spring-chain argument can reject a named page at every ballast vector. It
does not rule out another page or the map. Its floating guards are not an
interval proof. No reference was copied into this audit; the native checker
and required lab notes were read as source evidence.

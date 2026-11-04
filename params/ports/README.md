# Synthetic port archetypes

These four profiles describe invented berth groups, not real terminals or companies.
Public port totals provide scale and context only; they do not calibrate our per-call
workloads. Operational values remain assumptions except the explicitly sourced
Laem Chabang-like height and DG handling rules from expert Q3/Q9 (28 Oct 2026).
Task 2.6 can read these files with Python 3.12 `tomllib`; task 3.4 can use the
arrival, dwell, inventory and placement parameters to generate yard states.

| File | Role |
| --- | --- |
| `laem_chabang_like.toml` | Thai gateway serving synthetic regional feeders |
| `singapore_like.toml` | Synthetic gateway-style gate-in only; APPROXIMATION: no transshipment inflow |
| `small_feeder.toml` | Small regional port with a shallow yard stacking envelope |
| `us_west_coast.toml` | US West Coast gateway receiving the stage D rotation |

Three additional lightweight profiles declare only the next-call water environment
for D50 arrival checks. They use `profile_kind = "next_call_environment"`, with an
explicit UN/LOCODE and a unit-bearing, provenance-labelled water density. They
provide no crane count, reach or height, equipment, traffic or yard parameters
and cannot supply a port simulation. Their D74 raw timing evidence is retained
without turning it into an operational crane specification.

`list_ports()` includes all seven IDs and `load_port()` returns the existing
`PortProfile` type and preserves typed `profile_kind = "next_call_environment"`.
Environment-only profiles have an empty yard and no equipment.
Their yard maximum, quay crane specification and governing crane height H are
unknown (`None`). They contain no positive capability placeholders.
`Instance` rejects these profiles as its current operational port, before any
checker can treat density evidence as an operating limit. `load_simulator_params()`
also rejects these three entries explicitly. Their density and provenance remain
available for next-call voyage lookup. With rule freeze v1 off their berth draught
limit stays unknown; with it on they additionally expose the labelled synthetic
depth-derived limit below, without acquiring operational crane or yard limits.

| File | UN/LOCODE | Assumed water density |
| --- | --- | --- |
| `cat_lai_like.toml` | VNSGN | 1,000 kg/m³ |
| `hong_kong_like.toml` | HKHKG | 1,025 kg/m³ |
| `port_klang_like.toml` | MYPKG | 1,025 kg/m³ |

**ASSUMPTION:** No measured dock data are available. Cat Lai on the Saigon River
uses fresh water as the conservative river/estuary scenario for draught; brackish
water could reach about 1,015 kg/m³. The two seaport scenarios follow D34's
1,025 kg/m³ assumption. These values are synthetic voyage inputs, not surveyed
conditions or real-terminal operating claims. Replace the values and provenance
together when scenario-specific measured density becomes available.

Every numeric parameter is a table with `value`, `unit`, and exactly one of
`assumption` or `source`. A public source contains `url`, `says` (what the publication
actually supports), and `read_date`. Expert sources instead contain the repository
`document`, `question`, `says`, and `read_date`; this avoids inventing a public URL
for the expert's written answer. Numeric lists, if introduced, belong inside
`value` too. An assumed value inspired by a source stays an assumption: a machine
brochure does not establish a terminal's operating limit. `schema_version` uses the
same wrapper. Strings and booleans are descriptive metadata.

Durations use seconds (`s`), including gate-in offsets, dwell and arrival jitter.
Counts identify their unit explicitly: boxes are physical containers, while TEU is
a capacity/statistical unit. `mean_teu_per_box` is only an aggregate conversion,
not permission to treat every container as the same size. `calls_per_week` is the
requested weekly service frequency; divide by 604800 seconds to obtain calls/s.
Fractions and coefficients of variation are dimensionless. A coefficient of
variation (CV) is standard deviation divided by mean: larger values mean a more
variable workload.

## Consumer semantics

- **Layout:** `block_count = berth_count * blocks_per_berth`; each block has
  `bays_per_block` longitudinal positions, each with `rows_per_bay` stack locations
  and `max_stack_tiers` storage tiers. Bays are 20-foot ground positions; a 40-foot
  box occupies two adjacent positions, with compatibility/access checked by the
  yard model. Dimensions count storage positions, not metres or truck lanes.
  Homogeneous blocks have the listed equipment envelope. Equipment count is for
  the entire berth group; quay crane count is per berth.
- **Height (D62 amendment/A8):** `max_crane_height_tiers` is the quay crane's H,
  governing on-deck ship storage and ship-side crossing. The current-call quay
  `max_on_deck_tier_count` equals H for every activated operational parameter profile here;
  the old fields remain available to existing consumers. Legacy serialized
  profiles with missing or null H retain `max_crane_height_tiers = None` and
  preserve independent yard/quay limits until the L2 consumer migration. Loading
  them never derives H from the quay limit or rewrites either legacy limit.
  Effective yard storage and its crossing threshold are independently
  `min(block.max_tiers, yard.max_stack_tiers, equipment.stack_height_tiers)`.
  H is never part of this yard minimum; the quay limit does not cap yard storage.
  `1-over-N` means a crane can pass over an N-high stack. The legacy profiles store
  N tiers with nominal `clearance_height_tiers = N + 1`. Laem Chabang-like uses
  plain descriptive notation `synthetic RTG; yard height 5`: a five-high yard
  stack blocks yard crossing with the switch on. Its retained nominal clearance
  quantity6 is informational, never a measured operational hook height, crossing
  threshold or an additional storage tier. Reach stacker use of notation describes our
  synthetic clearance envelope. Machine load charts, reach and box height still
  need checking by a consumer handling detailed equipment feasibility.
  `max_on_deck_tier_count` counts tiers above the deck/hatch, not BAPLIE tier codes
  such as 82. The effective ship constraint
  is the minimum of applicable port, vessel, visibility and lashing limits.
- **Activation (A1/A8):** `load_port` and `load_simulator_params` resolve the same
  catalogue rule-freeze-v1 switch. Default off uses Laem's `legacy_height_limits`:
  H=None, yard/equipment storage5, informational clearance6, quay7.
  On uses H=8 and quay8; yard/equipment storage remains5 and informational
  clearance remains6. A8 withdraws the earlier staged yard5→8 change.
  Both settings use plain RTG notation. DG rule references are emitted only when activated.
  Other ports retain their existing numeric limits. The cache includes the
  effective catalogue SHA and activation value, including test overrides.
- **Geometry:** every operational profile explicitly sets `berthing_side = "port"`
  and `yard.truck_lane_side = "low"` (the row-1 side, applied to every block).
  These are labelled synthetic defaults; the files contain no surveyed berth or
  lane geometry. Environment-only profiles also label the synthetic berthing default.
- **DG handling:** Laem Chabang-like declares `port.dg.refused_class` with
  `classes = ["7"]` and `port.dg.direct_delivery` with `classes = ["1"]`,
  from expert Q9 (28 Oct 2026). Refused cargo may not be handled at the call;
  direct-delivery cargo uses ship side and cannot enter the yard. These references
  describe the data contract; enforcement belongs to the subsequent checker,
  feasibility and solver layers.
- **Later-call lookup (A3):** use
  `stowage.ports.resolve_port_call_profile(call, current_port=...)`. An explicit
  archetype must match the call's LOCODE. Without one, the matching current
  profile is retained, then unique LOCODE lookup preserves legacy voyage-density
  resolution. Unknown LOCODEs return None; ambiguous matches raise ValueError.
  `crane_height_at_call` returns the minimum of the call's explicit cap and the
  resolved H/legacy quay cap when both are known; a call cannot raise the port's
  limit. `crane_limits_known_at_call` exposes whether a known value exists.
  Density-only profiles always return unknown crane limits, including when a
  legacy call carries a positive placeholder; crane rules must skip that call.
  A declared explicit cap on an otherwise unknown call remains available.
- **Calls and volumes:** create the stated number of calls per service per week,
  equally spaced within that service, with service phases spread across the week.
  Add independent uniform jitter in plus/minus `arrival_jitter_s`; retain call
  identity and sort the resulting arrivals. Services repeat before and after the
  phase origin: reduce each phase modulo its own interval, or enumerate negative
  cycle indices too. Include pre-horizon/post-horizon nominal arrivals whose jitter
  can move them inside the horizon, then retain jittered times in `[0, horizon)`.
  Boundary jitter can change a finite horizon's observed count; validate the
  underlying weekly frequency with jitter disabled. Berth waiting is a simulator concern.
  Sample integer load and discharge counts separately from negative binomial
  distributions with their stated means and `call_volume_cv`. For mean m and CV c,
  variance is `(c*m)^2`, size is `m*m/(variance-m)` and success probability is
  `m/variance`; the supplied values have variance greater than mean. This is an
  APPROXIMATION: variable counts around a mean, not a fitted demand model. See
  [NumPy's negative binomial definition](https://numpy.org/doc/stable/reference/random/generated/numpy.random.Generator.negative_binomial.html).
  The simulator must respect vessel capacity and record conditioning/resampling;
  an unbounded count distribution never overrides a hard constraint.
- **Cargo flows:** `transshipment_fraction` is the synthetic fraction of discharged
  boxes retained for a later compatible vessel; the rest are local imports.
  Reserve the corresponding share of loading demand for transfer inventory; other
  load boxes are local exports. Match actual transfer boxes to compatible calls
  after their sampled minimum dwell. This fraction is a box-flow assumption, not
  automatically the same as a published whole-port TEU percentage. Unequal inbound
  and outbound volumes require explicit outstanding inventory/demand queues; do
  not create or delete transfer boxes to fill a call. Warm-up inventory can supply
  early calls and must be recorded as synthetic initial cargo. Missing transfers
  stay unfilled or roll to a later call, with the outcome reported.
- **Gate-in:** local export arrivals are relative to each booked call's cut-off,
  which precedes planned arrival by `cutoff_before_arrival_s`. Select a bin by
  `share`, then sample uniformly in seconds before cut-off between
  `end_before_cutoff_s` (inclusive) and `start_before_cutoff_s` (exclusive).
  Bins partition the full `window_before_cutoff_s`; shares sum to one. Transfer
  boxes arrive by discharge, not through the gate. These are conditional arrival
  times given a booking count, not a separate Poisson demand stream.
- **Dwell:** sample local import exit times at discharge. Gamma dwell uses the
  stated mean m and CV c: shape `1/c^2`, scale `m*c^2` seconds. Transfer dwell is
  a minimum readiness time before selection of the next compatible connection;
  actual dwell can be longer. See [NumPy's gamma definition](https://numpy.org/doc/stable/reference/random/generated/numpy.random.Generator.gamma.html).
  Export dwell follows the gate-in bins and call's actual loading time.
- **Placement:** cluster keys are ordered preferences: vessel call, POD (port of
  discharge), size/type, weight class. Normally select a legal location matching
  the longest available prefix, preferring a stack where later retrieval is below
  earlier retrieval; break ties by lowest occupancy then stable slot address.
  With `cluster_misplacement_probability`, ignore clustering and use all legal
  locations as the candidate set. Independently, with
  `retrieval_order_error_probability`, ignore the retrieval-order preference and
  draw uniformly within that candidate set; otherwise rank candidates by retrieval
  order, then occupancy and stable slot address. Neither event moves already stacked boxes, guarantees
  an inversion, nor overrides physical/DG/reefer constraints. This is the explicit
  meaning of `independent_cluster_and_order_errors`; report realised rehandles
  separately from these input probabilities.
- **Capacity and startup:** create an empty yard for task 2.6. For task 3.4,
  `initial_occupancy_fraction` targets occupied TEU storage positions at warm-up,
  not a claim that every stack has that height. Assign synthetic initial boxes
  future calls/exits and use the same legal placement policy; report achieved
  occupancy. Warm up for at least the longest gate window and relevant dwell
  horizon before collecting measurements, and retain residual inventory.
  `hold_in_external_queue` means a box waits outside the modelled yard when no
  legal slot exists. Count/report those waits and any missed cut-offs; never put
  a box beyond the tier limit. This is an APPROXIMATION of capacity spillover,
  not additional free yard storage.

The distributions, independent errors, homogeneous blocks, and synthetic initial
inventory are labelled approximations of port activity. They do not replace any
checker physics or hard rules. Berth/crane timing, routes, cargo compatibility,
DG areas, reefer plugs and vessel allocation remain responsibilities of the
simulator, cargo generator and checker; these files do not assert permission to
ignore them. Random seeds and run horizons belong to individual simulation runs.

## Downstream implementation limits

Task 3.4's current `sample_ship_calls` does not yet satisfy the weekly schedule
and transfer-inventory semantics above. Its nonnegative service-cycle loop omits
pre-origin cycles when a phase exceeds the interval. It records inbound transfers
and transfer bookings as unserved flow demand, without retained transfer boxes,
readiness times or compatible-call queues. Until those contracts are implemented,
results must disclose unsupported transfer counts and cannot substantiate transfer
hub occupancy, placement or connection performance. These limitations do not
change the requested fractions or permit treating transfers as local traffic.

## Public sources

Read on **2026-10-27**, without login. Only facts/context are referenced; no raw
datasets or downloaded files are redistributed.

| Source | What it supports; limitations |
| --- | --- |
| [Thai MOT / Port Authority of Thailand, Laem Chabang monthly statistics](https://datagov.mot.go.th/en/dataset/s6-5-2) | Catalogue of monthly ship calls, cargo and TEU, Jan 2018–Aug 2026; Open Data Common. Context for the Thai gateway; no per-call workload or yard geometry inferred. |
| [MPA monthly container throughput on data.gov.sg](https://data.gov.sg/datasets/d_da030f7028200d19ffcbe4a2d71af39c/view) | Public monthly throughput series through Sep 2026, latest figures preliminary; Singapore Open Data Licence. Future calibration source, not a gate-in or dwell dataset. |
| [MPA, Strong growth momentum for Maritime Singapore](https://www.mpa.gov.sg/media-centre/details/strong-growth-momentum-for-maritime-singapore) | Calendar 2024 throughput 41.12 million TEU, around 90% transshipment; whole-port context only. |
| [Port of Los Angeles, calendar 2024 container statistics](https://portoflosangeles.org/business/statistics/container-statistics/historical-teu-statistics-2024) | Total 10,297,352.25 TEU including loaded/empty import/export categories; whole-port context, not a real terminal model. |
| [Liebherr RTG product specifications](https://www.liebherr.com/en-us/maritime-cranes/products/port-equipment/rubber-tyre-gantry-cranes-4433172) | Product range includes 1-over-3 through 1-over-6; does not establish deployment or a local operational limit. |
| [Kalmar reach stackers](https://new.kalmarglobal.com/equipment/reachstackers/) | Published product range supports discussion of reach stacker equipment; operating envelope depends on model, load and reach. Synthetic small-port settings are assumptions. |

## Assumptions awaiting X1

Height mapping for schema 1.9 (parameter-file schema remains version 1):

| Profile | H | Yard stored / nominal clearance | Source or mapping |
| --- | --- | --- | --- |
| Laem Chabang-like, switch off | None | 5 / 6 | Legacy synthetic quay7 retained until coordinated activation; yard5 and informational clearance6 remain independent. |
| Laem Chabang-like, switch on | 8 | 5 / 6 | yard height 5, synthetic RTG; quay crane H = 8, expert Q3 (28 Oct 2026). A8 keeps the pre-L1 yard height; real yard height remains pending Q25. Clearance6 is informational, not an operational crossing allowance. |
| Singapore-like | 10 | 5 / 6 | Synthetic H copied from the existing quay limit 10; lower yard/equipment caps retained. |
| Small feeder | 4 | 3 / 4 | Synthetic H copied from the existing quay limit 4; lower yard/equipment caps retained. |
| US West Coast-like | 10 | 5 / 6 | Synthetic H copied from the existing quay limit 10; lower yard/equipment caps retained. |
| Cat Lai-like, Hong Kong-like, Port Klang-like | Unknown (`None`) | No yard | Density-only profiles declare no crane capability. H, quay specification and yard maximum are None; they cannot be used as a current operational port. |

| Expert question | Assumptions to replace or confirm |
| --- | --- |
| **Q3 / E3, height** | Laem Chabang-like quay H=8 is sourced. Its yard5 is a synthetic RTG assumption pending Q25; other ports' H and all nominal equipment clearance quantities remain synthetic. Equipment models, measured hook heights and reach/load charts remain unverified. |
| **Q5 / E4, placement** | Cluster-key priority, retrieval preference, both noise probabilities, block geometry/assignment and initial occupancy; also gate-in shape and dwell distributions as unmeasured yard behaviour. |
| **Q8, volumes** | Calls per service, load/discharge box means and variability, TEU/box conversion and synthetic transfer fractions. Public totals do not answer these questions. |
| **Q9, other port rules** | Refused class 7 and direct-delivery class 1 are sourced for Laem Chabang-like. Detailed DG/reefer/OOG zoning and restrictions at other ports remain unverified. |

Other assumptions are labelled in each metric: synthetic layout/counts, cut-off
offsets, dwell and traffic models. Small feeder is deliberately generic, and the
West Coast feeder service is a synthetic regional connection for testing, not a
claim about a named carrier. No expert answer is required to parse or use the
profiles; X1 should update values and provenance together.

Validate with `uv run pytest tests/params -q -p no:cacheprovider`.

## D74 crane timing and D75 berth draught

Each file declares `quay_crane.gross_moves_per_hour` in moves/h/crane and
`pre_operation_allowance_h` / `post_operation_allowance_h` in hours. All are
**APPROXIMATION: synthetic, UNVERIFIED, pending expert Q23/Q30**. The D74
parameter amendment selects main-port GMPH 25; small feeder and Cat Lai-like use
20 (background range 20–35). Both allowances are 1.0 h; the declared background
ranges remain 0.25–1.5 h before operations and
0.25–1.0 h afterwards. Faster crane work gives less ballast-pumping time.

The D74 interval is `3600 / (GMPH * min(port cranes, ship max_cranes))` seconds:
Laem Chabang-like gives 72 s for the 500/800 and 72 s for the 1700; Singapore-like
gives 48 s for the 1700; small feeder gives 180 s. `QuayCraneSpec.count` remains
the canonical serialized crane count; `count_per_berth` is a read-only alias.
The model's three timing fields are optional and strictly positive/finite; an
unknown value is omitted from JSON, rather than supplied with an invented default.

**APPROXIMATION:** gravity draining is not modelled (conservative). These amended
timing choices follow D74 and `experiments/review/w2/logic-d74.md`; they are
synthetic allowances for pumping throughout the call, not measured terminal
productivity or expert-confirmed operational windows.

| Profile | Raw berth depth (m) | Anchor and limits of its application |
| --- | ---: | --- |
| Laem Chabang-like | 16 | [Official Hutchison facilities table](https://hutchisonports.co.th/facilities/): C1/C2 and D depth at mean sea level. This operating-terminal anchor is used for a synthetic representative berth; the separate 18 m Phase 3 design is not treated as current availability. |
| Singapore-like | 18 | [Official PSA Singapore 2026.04.R1 factsheet, page 2](https://www.globalpsa.com/wp-content/uploads/2026/04/PSA-SINGAPORE.pdf): Pasir Panjang maximum depth at Chart Datum. Tuas is separately listed at 23 m; neither figure applies to every berth. |
| Small feeder | 8 | Synthetic shallow berth; deliberately constrains a deeply loaded feeder_1700. |
| Cat Lai-like | 10 | Synthetic river/estuary berth, no surveyed terminal/channel figure supplied. |
| Hong Kong-like, Port Klang-like, US West Coast-like | 16 | Synthetic representative berths, no surveyed terminal/channel figure supplied. |

**APPROXIMATION:** raw `berth_depth_m` becomes the existing canonical
`PortProfile.max_draught_m` when rule freeze v1 is on. No tide or UKC increment
is credited; no separate under-keel-clearance reserve is modelled. The two public
depths provide context for these synthetic cases, not navigational permission.
The read-only `berth_depth_m` model property reports that same effective cap,
without creating a second serialized limit. The existing P5b checker therefore
receives the cap directly, including its low-side draught allowance at list.

With the switch off, new timing inputs are omitted and `max_draught_m` remains
None. File hashes still change honestly when parameter evidence changes. The
three next-call environment profiles retain `profile_kind=next_call_environment`,
`quay_crane=None` and unknown yard/H limits under both settings. Their raw timing
tables do not invent a crane count or authorize current operational use.

# Task 0.6: envelope cohorts, registered before generation

The envelope was chosen after the former holdout was read. That holdout cannot
support the final claim. This fresh sealed cohort is reserved for task 3.1.

Eight cells, in this order: ship (feeder_500, feeder_800), physical fill (60,70
percent), generator (random_yard, simulated_yard). The latter is the clustered
yard simulator. Four dev maps per cell (32 total); three sealed maps per cell
(24 total). Each map has its own root seed, with no parent shared across maps.
Seed = 306100000 + split offset (dev=0, sealed=10000) + 100*cell_index + rep,
with zero-based cell_index and rep. All roots are audited against prior set,
request, cohort, split, manifest and path metadata before generation and after.
Forbidden old sealed raw trees are never opened; their external metadata is
covered. The audit states this limitation rather than claiming full inspection.
AMENDMENT-1.md clarifies that fractional JSON decimal digits are not seeds;
the original full scan and exact source are preserved with resolved evidence.

Copy C6's independent unmodified src/params/corpus-builder runtime and numerical
caches. D192 settings: medium, laem_chabang_like, noise .5, max_attempts=100,
booking_slack=0, booking_mass_fraction=None, n_boxes=None, dense_20ft=False,
tiny_dg_enabled=True; mass multiplier=1.0 (C6 high arm). Producer C1/D58 and
schema/provenance gates remain unchanged, C2 off, rule freeze off, V4 unset.
No planning or plan checker is run. A generated derivative is an experimental
input, not a producer certificate or proof of feasible loading.

Before any draw, fix this derivation/trim rule: C6 proportional-deficit stratum
interleaving, with SHA256 ordering namespace C6, followed by the longest prefix
whose physical occupancy does not exceed floor(target_fill*region_cell_count)
and whose box count does not exceed 195. Twenty-foot boxes consume one cell;
40/45-foot boxes consume two. Keep parent vessel, yard, inventory, voyage and
onboard background, and keep nonselected targets ashore. Only identity,
load_list, family lineage/utilisation and provenance change. No mass change.
The 195 cap takes precedence over proximity to the fill target; any dev cap
trimming and pretrim count are recorded. Sealed generator records only overall
envelope/schema success, never cargo statistics in public metadata. No redraw,
manual repair or seed substitution is permitted after a failed request.

Every output must be stage A, empty ship on arrival, no discharges, no forecasts
or later planned load calls, selected cargo POL equal to the current port.
Future discharge calls remain in the rotation. Physical fill is relative to the
medium decision region, as in C6; nominal ship identity remains 500/800 TEU.

Sealed means: after the generator writes and validates it, nobody opens, parses,
summarises, scores, plans, visualises, or inspects cargo/box counts/lifts of a
sealed input before task 3.1. Only byte hashes, file counts, declared seed/cell
metadata and the generator's own schema-valid/exit status may be checked.
No parent/audit/derivation payloads or cargo summaries for sealed maps are
retained outside the generator's private temporary directory; it is removed
by the generator. Task 3.1 opens the cohort once, after pipeline/test freeze.
Development maps are fully open for building, hero selection and dry runs.

Twenty-four sealed maps imply approximately 150 seconds per paired map for a
one-hour final run (projection, not a measured runtime guarantee); task 1.12
must freeze arm budgets using dev only. No sealed timing pilot is allowed.

Two CPU workers maximum, BLAS/OMP threads one. Each job waits in a loop for
fleet core-state mask popcount>=8, load1<=45 and reading age<120 seconds.
No nice/ps/sysctl or unowned process control. All writes stay in this directory.
The lab's positive provenance list cannot be edited within this fence; a local
dev-only manifest-pinned adapter invokes its actual loader/scorers without
authorizing any sealed path.

Cycle 3 integration, 0.12

`exact/` copies all eight algorithm modules from `lab/c3/c2-hatch/`: plain
packing, first-aboard joint model, heat/DG unary strengthening, outward geometry,
lazy necessary-state cuts, reviewed securing cuts/oracle, and `certified.py`.
Every sibling import is engine-local. Its three test files are copied too;
fixed good/bad assignment subsets are test-only fixtures. `certified.py` at this
directory level exposes the public bound and a replay/mask-gated birth-order
witness repair. No historical assignment or finished plan supplies a start.

The exact bound permits arbitrary legal current MovePlans, including staging,
direct shifts and repeated ship restows. It prices only necessary costs; CP
OPTIMAL is an optimum of the relaxation, not a checker pass. A matching fresh
full-checker upper proves fewest lifts among checker-passing plans, counting
later ports by D15's fixed-stow projection with no later loads. The new record
names the certificate, family, activation mode, integer cutoff target, native
source hashes and assumptions. It retains `cutoff_infeasible` separately from
whole-model infeasibility. Exact cuts are reviewed for rule-freeze-v1 disabled,
empty decision arrival, no discharges/forecasts/future loads, finite nonnegative
objective weights, positive integer restow-lifts-per-box, disjoint immutable
fixed geometry and ship-scale coordinates. Unsupported cases retain older bounds.

DG rows in the exact model are rebuilt by the copied necessary no-ship stowage
screen, retaining supported upper geometry; the copied synthetic SW2 regression
explicitly covers the empty-probe false negative. Older and medium helpers keep
the broader footprint-only widening. Neither changes native search tables.

Fixed integration bugs: incomplete/error/static `checker.*` diagnostics could
otherwise become full-assignment no-goods; they now stop as unknown. Absolute
geometry error margins and bounded CP floor variables are no longer extrapolated
to exotic coordinate scales. Integer-cutoff status and scope are explicit.

`physical_capacity.py`, `port_capacity.py` and their tests copy
`lab/c3/c2-layout/{bound,bound_ports,test_bound,test_bound_ports}.py`.
`capacity.py` combines their certified physical-stack/E6/Hall bounds with the
old aggregate. Eligibility-dependent demands use optimistic DG tables first.
A partial Hall enumeration yields unknown and preserves the previous bound;
its best enumerated mask is an incumbent, never a valid lower bound. Published
old DG-filtered values are not assumed by the adapted regression. Constructor
capacity screening is separate and never certifies this bound.

No numerical jobs were run by this integration agent while the leader reserved
the CPU. Python syntax is checked; root owns full pytest and fresh benchmark
verification. All claims of measured stage contribution belong in root NOTES
only after those runs. Test fixtures identify approved development/TRAIN data.

Physical→Hall E6 reuse is a process-local `E6WitnessCache`, populated only by
native witness calculation. It binds the same immutable instance/table objects,
oracle, rule mode and E6 parameters; replacement invalidates the entry. It is
never reconstructed from serialized base-result fields. Optimistic DG widening
is idempotent, so already-widened tables retain that shared identity. Records
contain only hit/miss counts and Boolean component hits, never cached objects.
`capacity.lower_bound(..., reuse_e6=False)` ablates reuse. Regressions compare
cached/fresh Hall values, reject cross-map/table reuse, ignore forged serialized
witness fields, and preserve Unknown when enumeration does not finish.
A counting-only exact fallback cannot replace an equal named old certificate.
If a counting fallback improves the old value it is labelled
`counting_only_fallback`, never `certified`; component status and scope remain
visible. The legacy combination likewise names a counting-only winner honestly.

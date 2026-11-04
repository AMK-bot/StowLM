# Cold bound and graph model

`bound.py:lower_bound` reads only the supplied instance and source code. It
imports the unchanged C4 `c2-layout/bound_tiers.py` necessary physical-cell
relaxation, its audited outward physics envelopes, and C3
`c2-layout/bound_ports.py` Hall enumeration. No earlier plan, assignment,
ballast or bound-result file is read. All input lists use `catalog.load_set`.

The graph has discharge calls on one side and hatch covers on the other:
putting hold cargo under a cover selects an edge, pays a current opening and
closure for the used cover, and pays a projected opening and closure at each
incident discharge call. Deck cargo lasting beyond such a call forces a
restow, counted once even when its footprint spans covers. Physical cells and
their support predecessors form a second graph which couples this fixed-cost
covering problem to packing.

The reused C4 relaxation retains actual eligible physical-cell footprints,
cell capacity, no-gap support, stack mass/height capacity, hold length-class
separation, safe empty-bottom E6 witness implications, necessary displacement,
weather vertical moment and globally minimum-motion securing. It gives exact
ordinary minimum support-height floors, never nominal stack heights. It drops
E1/E2, DG/OOG pair interactions, exact securing, sight and finite-angle area,
and makes in-stack overstows free. The CP feasible incumbent is **not** a
certificate. Only the solver's lower bound, maximized with independently valid
analytic cover cuts and the Hall enumeration, enters the result.

## New securing spreading cut

At a deck root end frame the checker's transverse corner shear is
`(sum(transverse inertial force)+sum(wind force))/2`. Its combined two-axis
norm cannot be smaller than this transverse component, and its hard fitting
limit is `.85*210000 N`. Thus one root carries at most `357000 N`; one occupied
physical column has at most two roots and carries at most `714000 N` across
those roots. No-gap support makes all roots bottom roots. A long box may join
two columns, so arbitrary per-column attribution is unsafe. Instead, join all
columns linked by any eligible long-box footprint and sum over each connected
component. Every end-chain remains inside one component.

The new rows activate a physical column iff at least one allocation occupies
it. Sum downward-rounded minimum-motion force in each support component and
cap it by `ceil(714000*(1+1e-7))` times its active column count. Group
coefficients are minima over original members. Existing C4 force coefficients
already include an outward numerical guard and an optional minimum-end-share
factor at most one, making this total-force inequality weaker. Background
support is disjoint, guarded by the reused C4 routine. Omitting its forces
only relaxes the rows. These cuts can force deck cargo to spread; they do not
claim that a reference-GM securing polytope is globally necessary. In
particular, neither the constructor's `.95` margin nor its approximate
heights enter this certificate.

The original relaxation is restricted to empty closed arrival, all load-list
boxes aboard, no discharge now, forecasts or future loads, nonnegative
weights, and disjoint immutable background support. It rejects unsupported
cases. “Optimal” would mean fewest weighted lifts among complete plans passing
the unchanged full checker, counting later calls by D15's fixed-stow
projection without later loads. A relaxation optimum alone does not establish
an operational optimum; equality with a checked plan does.

## Circle graphs: explanation and limitation

Avriel, Penn and Shpirer connect multiport stack shifting to circle-graph
colouring. Each cargo interval has its loading and discharge events as chord
ends; crossing intervals cannot share a stack without shifting in the
classical model. Here all decision cargo is loaded at one call, no later
loads exist, and geometry, E1/E2, bookings and securing dominate which stacks
are usable. The unrestricted interval graph is therefore too weak to certify
the observed restows: sorting by discharge call alone ignores those hard
constraints. No circle-colouring bound is claimed. The cover bipartite graph
does retain a useful fixed-cost lower bound.

## Papers and proof provenance

- P. Hall (1935), *On Representatives of Subsets*, Journal of the London
  Mathematical Society 10, 26–30,
  https://doi.org/10.1112/jlms/s1-10.37.26. Port-subset cargo cannot exceed the
  neighbouring cover capacity; the reused two-port enumerator checks all
  three nonempty subsets and all cover masks.
- M. Avriel, M. Penn and N. Shpirer (2000), *Container ship stowage problem:
  Complexity and connection to the coloring of circle graphs*, Discrete
  Applied Mathematics 103, 271–279,
  https://doi.org/10.1016/S0166-218X(99)00245-0. Used for the graph explanation,
  with the limitation above; no approximation guarantee imported.
- C4 `c2-layout/physics_bound_notes.md` supplies the existing necessary-row
  proof and outward arithmetic. The active-root inequality above follows
  directly from unchanged `stowage.checker.lashing._chains` and the fitting
  shear term, rather than an external physical fit.

## Reproduction and validation

Use one numerical thread, the root-assigned CPU token, `nice -n 5`, and the
same scoped vessel cache as the experiment. Example (paths from repo root):

```
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab \
PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c6/c6-lifts/bound.py \
  --list experiments/mvp/frontier/lab/c6/c6-lifts/dev6.txt --budget-s 15 \
  --out experiments/mvp/frontier/lab/c6/c6-lifts/results/bounds-dev6.json
```

Tests cover support-graph components including shifted long boxes, force
capacity forcing occupation of a second component, long-box force counted
once, and the 37/14-cell Hall hand certificate. Parent will append measured
rows and checked-plan gaps to NOTES.md after frozen measurement. No claimed
improvement from these new rows precedes measurement.

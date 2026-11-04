# Static-domain Hall screens

`hall.py:analyze(instance, tables=None, assignment=None)` is an independent,
necessary-condition screen. It does not construct a stow, calculate lifts, or
replace the native mask, finisher, or checker. No new source is copied; it
imports the unchanged native static tables and OR-Tools integer max flow.

The graph has one vertex for each remaining load-list box and one for each
physical cell. An edge means that the cell occurs in at least one native
statically compatible footprint for that box, at any tier. A twenty-foot box
demands one unit and a forty/forty-five-foot box two. A cell supplies one unit.
Flow can split the two units of a long box across incompatible footprints;
this enlarges the solution set and makes rejection safe. Unsupported upper
slots are retained. `legal_actions` and the current open-slot mask are never
used to construct these domains.

Two further flows replace cells by physical stacks. The source demand is box
mass, or box height multiplied by its one/two physical-cell units. A box can
send that demand to any stack occurring in any of its static footprints.
Mass capacity is the maximum of that stack's mixed, twenty-foot, and long-box
ratings. Height capacity is its entire height envelope. Both flows allow
arbitrary splitting across stacks. Demands round down and capacities round
up, with an additional numerical guard; units are whole kilograms and whole
millimetres. These are optimistic necessary conditions, not exact packing.

For any deficient flow the code extracts a set of boxes X and all its graph
neighbors N(X), then independently checks the integer inequality
`sum(demand[X]) > sum(capacity[N(X)])`. The returned certificate contains the
box IDs, resource IDs, demand, capacity, and deficit. This is a capacitated
form of Hall's condition, obtained through a minimum cut. A no-deficit result
is explicitly `unclassified`: it establishes no legal completion.

At the root, only load-list boxes are demanded. Every onboard and background
box is omitted and every decision cell is available. Therefore a root
deficit rejects the original instance under the native static-table rule
contract even in this optimistic relaxation. We deliberately avoid demanding
transit boxes through current-port loading domains: an untouched transit box
need not satisfy the current port's handling reach.

An optional `{box_id: slot}` assignment reserves its physical cells and
deducts its mass/height from supporting stacks. A deficit then rejects only
extensions that preserve those placements. It says nothing about the same
map after rearranging that prefix. Unknown boxes, unknown slots, wrong-size
footprints, and overlapping committed placements raise `ValueError`; these
are malformed prefixes, not original-map infeasibility certificates.

Static domains retain the native size acceptance, reefer plugs, hard
under/on-deck requests, individual envelope limits, crane reach, and native
optimistic DG placement rules. All support/stack ordering, composition,
pairwise DG, neighboring OOG geometry, securing, physics, yard sequencing,
port blocking, and further constraints omitted from that static table are
relaxed. In particular, no later-port chain requirement is called a necessary
legal condition here: restows can make a port-blocked arrangement legal.

## Sources

- P. Hall (1935), *On Representatives of Subsets*,
  [DOI](https://doi.org/10.1112/jlms/s1-10.37.26).
- L. R. Ford Jr. and D. R. Fulkerson (1956), *Maximal Flow Through a Network*,
  [DOI](https://doi.org/10.4153/CJM-1956-045-5).

The implementation uses OR-Tools' installed integer maximum-flow routine;
no code was downloaded or executed from these papers.

## Verification and measurements

Five tiny synthetic checks pass: a pooled deficit with no empty individual
domain, the extracted Hall witness, a feasible long-box split, a deficient
long-box split, and the empty graph/rounding behavior. Run:

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONPATH=src .venv/bin/python \
experiments/mvp/frontier/lab/c1/fx-chains/hall.py --self-test
```

An integration smoke check also ran on `model.fixtures.build_instance()`
with unchanged native tables, then reserved one statically eligible long
placement and verified that the result changes its scope to prefix-only and
removes that load from remaining demand. This fixture is explicitly not a
checker-approved instance; its zero static domain is not a medium-map result.
The Hall analysis itself took less than 0.001 s on this tiny fixture.

The subagent did not launch a medium sweep while the team's two CPU slots
were occupied. The parent can reuse already-built tables without creating
another process:

```python
result = hall.analyze(instance, tables=problem.engine.T)
prefix_result = hall.analyze(instance, tables=problem.engine.T,
                             assignment=committed_assignment)
```

Existing `MEDIUM-WALL.md` reports witnesses for all twelve earlier fractional
static capacity relaxations. Thus another root capacity failure was not
assumed. Fresh results must be reported separately; prior prefix failures
are not evidence against original-map feasibility. This module has no
full-checker pass claim and no measured lift comparison. Parent experiment
results and `NOTES.md` own the quick/medium12 construction comparison.

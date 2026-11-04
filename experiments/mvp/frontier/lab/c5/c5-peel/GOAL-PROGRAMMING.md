# Goal programming and the assignment ladder

The assignment ladder uses deviations to guide construction, then asks the unchanged checker what the resulting plan actually breaks. It never treats a small LP slack as a checker pass. T0 remains enforced at every level.

## Primary sources read

Abraham Charnes and William W. Cooper, **“Goal programming and multiple objective optimizations: Part 1”**, *European Journal of Operational Research* 1(1), 39–54 (1977), DOI [10.1016/S0377-2217(77)81007-2](https://doi.org/10.1016/S0377-2217(77)81007-2). [Publisher record and abstract](https://www.sciencedirect.com/science/article/pii/S0377221777810072); [primary paper in Cooper’s Carnegie Mellon archive](https://iiif.library.cmu.edu/file/Cooper_box00005_fld00001_bdl0001_doc0001/Cooper_box00005_fld00001_bdl0001_doc0001.pdf).

Charnes and Cooper describe desired values or intervals and functions that penalise departures from those goals. Their discussion admits both numeric distances and ordered priorities. This gives a useful distinction: a target can remain visible in the objective even when it is no longer a hard feasibility condition. The 1961 Charnes–Cooper book, *Management Models and Industrial Applications of Linear Programming*, is historical background; the paper above is the primary work consulted here, not a claim that the entire book was read.

John W. Chinneck and Erik W. Dravnieks, **“Locating Minimal Infeasible Constraint Sets in Linear Programs”**, *ORSA Journal on Computing* 3(2), 157–168 (1991), DOI [10.1287/ijoc.3.2.157](https://doi.org/10.1287/ijoc.3.2.157). [Publisher record](https://pubsonline.informs.org/doi/10.1287/ijoc.3.2.157); [author-hosted primary paper](https://sce.carleton.ca/faculty/chinneck/docs/ChinneckDravnieks.pdf), especially sections 3–4, printed page 160.

Their elastic formulation adds nonnegative variables that let inequalities stretch; equalities need deviations in both directions. Minimising their sum discourages stretching. Their **elastic filter** repeatedly makes stretched rows hard again and resolves, obtaining a smaller set containing an irreducible infeasible subsystem. A separate filtering step can isolate an IIS. An IIS is irreducible: removing any member makes that subsystem feasible. This does not mean it is the smallest conflicting subsystem, nor does one IIS identify the fewest rows that repair the whole model. The author’s [MINOS(IIS) description](https://www.sce.carleton.ca/faculty/chinneck/minosiis.html) explicitly distinguishes those tasks.

The publisher and author/archive material was consulted through web search on 2026-11-01. Direct PDF opens returned access/fetch errors; the web tool supplied indexed primary-paper passages. No downloaded executable or external source code was run.

## Translation into this experiment

These are proposed modelling choices for this repository, not performance claims from either paper.

For an upper-bound row `a*x <= b`, use `a*x - s <= b`, with `s >= 0`, only when its rule is permitted to peel. For a lower bound, use `a*x + s >= b`. T0 and higher-priority tiers keep `s = 0`. Retain the original row label, rule ID, units, limit and slack value so that the construction objective has an inspectable meaning.

First preserve cargo: minimise boxes ashore, then their TEU and mass. Preserve earlier objective values when moving to later aims. Next prefer fewer broken checker rules, smaller checker breaches, and fewer lifts. An LP sum of row slacks is a construction surrogate: one checker rule may correspond to many model rows, and vice versa. A generic weighted sum does not establish that the requested discrete, cargo-first ranking has been solved exactly.

Physics rows are approximations and the ballast search is bounded. Rounding a fractional assignment or changing ballast can change the checker’s breach. Therefore record LP slack diagnostics separately from the unchanged checker’s measured/limit evidence, and rank returned plans using the latter. The current assignment method borrows the elastic formulation idea; it is not a completed implementation of Chinneck–Dravnieks IIS filtering and supplies no minimum-repair or global-optimality certificate.

The stopped pages experiment contains five measured medium rows out of the fixed 36. It is preserved as **“pages ladder, stopped at 5 of 36”**, not combined with the replacement assignment ladder. Historical as-is tier classification remains a retrospective table, not a cold solving result.

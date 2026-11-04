# Frontier benchmark: a stronger baseline than two-step

> **Now (plan v6, 1 Nov 2026):** Frontier is no longer a baseline. Frontier Engine 0.13.0 (`engine/`) is the reference engine in StowLM's engine slot, the planning software inside the pipeline (see "The engine slot" in the repository README). The rest of this README records the benchmark as it was built.

**Why this exists.** The two-step baseline came from one expert's description. This folder replaces it as the bar for
StowLM with the best emulation of current practice we can build from public sources, checked against published numbers.

**What it is not.** Vendors don't publish their algorithms, so this is not Navis, StowMan or a human planner. No public
code exists for the leading academic planners either (`research/repos.md`). Everything here is re-implemented from papers
and public product descriptions, and labelled where it departs from them.

## Evidence

| File | What it holds |
|---|---|
| `research/commercial.md` | Products, single-port and multi-port workflows, rule set, what can't be known |
| `research/academic.md` | Best published method per problem class, specifications, validation targets |
| `research/repos.md` | Public code and benchmarks, formats, licences |
| `public/rcspp/VALIDATION.md` | A full-rule multi-port planner and an audited checker against RCSPPSuite's 73 published plans |
| `public/multiport/VALIDATION.md` | Junqueira HR1/HR2 and k-shift re-implementations against published numbers |
| `public/larsen_pacino/VALIDATION.md` | Larsen & Pacino ALNS re-implementation against their Table 2 |
| `RESULTS.md` | The benchmark on our maps through the full checker, against two-step and StowLM |

## The planner on our maps

The same gate as every method in this project: the V4 ballast finisher, then the full checker, no repair.

1. **Starts.** Four independent ways to get a first plan:
   - the two-step plan (public-rule carrier and terminal workflow, kept as a start);
   - the same plan under port blocking, one discharge port per block (`blocked.py`, reusing the D185 rule and D174 compaction);
   - yard-first randomised construction (Junqueira HR2 idea): cheapest next lift, then a clean load order;
   - a master plan (`master.py`): constraint model of cargo groups to slots over the whole rotation, the carrier step of
     Pacino et al. 2011 and the Maersk patent. Its objective is written in our lift units.
2. **Search** (`alns.py`). Simulated annealing over complete assignments with adaptive operator choice, in the spirit of
   Larsen & Pacino 2021. Every candidate is scored by the real move replay. Operators: swaps, swaps across discharge
   ports, swaps within a yard stack (changes who is dug out first), column swaps, moving a top box.
3. **Finish.** Two-step and block-rule are finished first, as the fallback and as the stand-in tanks for the gate (the ship's
   default tanks if neither finishes). Then the cheapest plans that clear the gate go through the finisher and checker until one
   passes or none is cheaper than the fallback. So this planner can't do worse than a start that finished in the same run, and it
   takes far more wall time: 305–357 s per small map against about 35 s.

**Large calls** (`columns.py`, `plan_big` in `planner.py`). Not finished. The slot model doesn't scale to 198 boxes, so the
master plan works on stack columns: group counts per column with mass, height and capacity limits, later port lower and
heavier lower inside a column. Constrained cargo goes first (`blocks.place_specials`). See `RESULTS.md` for where it stands.

**Versions.** The final rows are 0.9.0, in `runs/*-0.9`, and each carries a hash of the planner's source; all 193 rows (148 on seed 0, 45 on seed 1) share
one. The hash doesn't cover `src/` or `params/`. `planner.py` stays unedited so the hash remains true, which also leaves one known issue in place:
`pool[_key(s)] = s` can let a dearer load order of the same stow replace a cheaper one. No row is affected.

- **0.9.0** adds the block-rule start, finishes a start even if the other's finish failed, and stops trying once nothing is
  cheaper than the fallback.
- **0.8.2** changes large calls and any map whose two-step finish fails in that run: that plan stays a search start, the sight
  gate uses the ship's default tanks instead of being skipped, and a large plan is ordered again after missing boxes are added.
- **0.8 and 0.8.1** are the blind first-run rows in `runs/test-0.8`, `val-0.8` and `unseen15-0.8`. They have no source hash.
- **Plan origin.** Rows don't store it: `search-N` in `tries` means "came through the search pool", including starts search didn't
  improve. `tables.origin` classifies by content.
- **`master.py`** was edited at 00:15 on 31 Oct, after 0.8.1, for a longer cut loop that was later removed. Its end status now
  separates running out of cuts from running out of time.

## What the planner had to learn from the checker

Three rules aren't in the master model's first version; each came from reading why the finisher failed on validation
maps. Validation numbers are therefore development numbers, and test maps are the clean measure.

- **SOLAS V/22 sight:** too much on deck closes the 5-degree clear gap between blind sectors. Deck height steps down
  until the gate in `sight.py` passes.
- **Lashing:** hold stacks may not mix 20- and 40-foot boxes.
- **E6:** heavy boxes stay in the hold when it can take them.

## What departs from the published methods

- The master plan works on slots (small calls) or columns (large calls), not aggregated areas.
- The search is exchange-based with a real-objective score, not Larsen & Pacino's label-based repair. Their method is
  re-implemented faithfully and separately in `public/larsen_pacino/`.
- Crane intensity and crane split aren't modelled: our objective is moves, and our crane model is single-sequence.
- The objective is ours (D11), not any vendor's. Vendors' weights aren't public.

## How to run

```bash
PYTHONPATH=src:experiments/mvp/frontier .venv/bin/python experiments/mvp/frontier/run_maps.py <list.txt> <out_dir> --search-s 120 --finalize-s 300 --workers 3
```

```bash
.venv/bin/python experiments/mvp/frontier/tables.py <out_dir> val
```

Nothing here touches `src/`. Lists hold repo-relative instance paths.

## Data notes

- k-shift data is GPL and Junqueira's data has no licence: both stay outside the repo; only derived numbers are here.
- RCSPPSuite is anonymised real vessel and cargo data with no licence. The user allowed it for evaluation only (D197): never to train or tune a model. It stays in git-ignored `data/raw/`, and only
  derived numbers are here. `public/rcspp/` is a separate planner and checker: nothing on our maps imports it.

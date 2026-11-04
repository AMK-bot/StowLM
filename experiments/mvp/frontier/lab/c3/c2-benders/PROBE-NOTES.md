# Native ballast probes

Only this C3 experiment folder is written. No checker, engine, rule, tolerance,
or interpolation qualification changes. `physics.py` imports the ordinary
checker and D57 condition derivation; it copies no physics. The direct
forward-draft root follows the production method-1 loading/equilibrium call
sequence, then all proposals receive ordinary three-phase stability and
securing. Physics success alone is never a full-checker pass.

`heel_forward_probe.py` was copied from this folder's `apt_probe.py` and adapted
to propose HEEL/DB-M increases. Both use the unchanged native direct equilibrium
for their scalar FWD-DEEP compensation root. The root targets forward draft
0.0003 m below the actual vessel/port maximum; this is a search target, not an
extra rule allowance. They retain fixed consumables and rederive all voyage
states. All candidates and completed reports are saved.

## Probe decisions and budget

The HEEL direction was initially misread in a saved finite-difference trace.
The measured trace actually increased HEEL by 0.0075; that sign was corrected
before the numerical HEEL probe launch. The completed corrected probe tested
mass-neutral WING-F-to-HEEL transfers and pure HEEL additions. No numerical
negative-HEEL trial was counted.

Pure HEEL +0.045 reached positive arrival total-area margin but failed forward
draft and securing. Mass-neutral WING-F transfers worsened total area. APT
reductions, with exact FWD-DEEP draft compensation, also worsened total area;
the large positive-APT secant was not locally reversible. These are sampled
negative results, not physical infeasibility proofs.

The parent authorized a single serial process for the final predeclared eight
HEEL/FWD-DEEP probes, while another team process handles cargo refinement.
It tests HEEL increases 0.06, 0.08 and 0.10, each with DB-M increases 0 and
0.0008, then the latter two HEEL increases with DB-M increase 0.0015. The
sequence has a 600-second admission cap and stops at the first unchanged full
checker pass. This budget is needed to distinguish the remaining total-area,
securing and forward-draft tradeoff near the first D185 acceptance; one
completed three-state native physics evaluation currently costs about 20 s.
No run is intended to exceed 20 minutes. `nice -n 5` was attempted, but this
machine denied setpriority; all numerical thread settings remain one.

Each launch prints its owned PID. Only those recorded process sessions are
controlled. The final probe is PID 54986 / session 65831; earlier HEEL and APT
processes exited normally. Their exact source/input/frozen-recipe hashes and
requested variant lists are in each results directory's `manifest.json`.

## Completed probe result

All eight compensated HEEL/DB-M probes completed and PID 54986 exited zero.
None passed the six requested physics parts, so no full-checker call was
triggered. Every candidate retains the same complete 106-lift cargo stow;
these eight ballast recipes are not eight newly constructed stows.

At HEEL +0.08 and DB-M +0.0008, all requested rules except arrival total GZ
area pass. That final area margin is -0.0001198191722 m rad. At HEEL +0.08
and DB-M +0.0015 the deficit falls to -0.00005158589395 m rad, but departure
rod ratio rises to 1.00111084644. No tolerance or rule was changed to accept
either recipe. The saved negative outcomes remain valuable starts for a
small cargo-CG/securing repair. No physical infeasibility is established.

from pathlib import Path
import json,hashlib,datetime
h=Path(__file__).resolve().parent;r=h.parents[4]
sha=lambda name:hashlib.sha256((h/name).read_bytes()).hexdigest()
files=['candidate-teacher-loop.diff','candidate-teacher-loop-tests.diff','installed-manifest.json','RESTART-HOST.sh']
manifest={name:sha(name) for name in files}
(h/'release-hashes.json').write_text(json.dumps(manifest,indent=2)+'\n')
text='''D137 candidate pool is staged and qualified; NOT installed or launched.

Apply candidate-teacher-loop.diff and candidate-teacher-loop-tests.diff after D132.
Both git apply --check commands pass against the currently installed repository,
as well as an independent copy of the exact D132 ExIt baseline. Private application
was verified against all 27 expected source/test hashes. Production src/ and tests/
were never changed by this executive. installed-manifest.json contains EXPECTED
installed bytes, not an assertion that deployment has happened.

The patch changes nine source modules: data, selection, preferences, reverify,
control, runner, audit, sizing (new), and final_recipe. Twelve test files change.

Behavior:
- About half the train maps carry historically checked, content-bound stored gold;
  half have no stored gold. Every teacher is checked again under the current epoch.
  Gold-backed maps are labelled new_to_loop. Unseen_by_any_adapter is used only
  when a complete inherited adapter inventory proves it; otherwise new_to_loop.
- Every map runs two-step4.4 with120s planning plus FINALIZE_S (default120s) V4
  finishing, a full checker/watchdog and production+securing+lookahead replay.
  CPU teachers start before GPU decoding; each map records attempt/pass/failure,
  measured moves and phase timings. Training refuses if teacher coverage is zero,
  or no gold teacher passes while the requested mix includes gold.
- All complete plans are checked; the fewest-moves verified plan wins. LNS15s
  starts from the best verified plan and any improvement is checked again.
  Model-alone and polished-model outcomes retain distinct source labels.
- DPO teaches the verified winner against fully checked, costlier own-model plans
  first, then genuine own-model dead ends. Complete negative traces are rebuilt
  from checked plans and refreshed under the training epoch. Unavailable checks
  and unfinished pruned samples never supply a preference rejection.
- A gold-only control really trains: it matches the compared child schedule, or
  explicitly borrows the fittest trained child's schedule when the elite survives.
  Gold admission and mask dataset building use bounded ordered process pools.
  Attribution requires a trained, matched control; method/gate records disclose
  unmatched DPO updates. Parent elitism and numerical D128 gates stay in force.
- D139: final-recipe candidates cannot receive a second finisher attempt after
  failing their first. Retry refusal, known finisher timeout and checker
  unavailability stay distinct. The cache recipe version changes accordingly.
- P=3 preference children have separate frozen-reference caches.

Measured CPU qualification:
827 tests passed, 0 failures/errors/skips, 97.651s. This is the complete private
ExIt suite plus restart tests, not an MLX/model smoke. See qualification.xml/json.
Command (native threads1, no GPU):
UV_CACHE_DIR=$PWD/.uv-cache PYTHONPATH=$PWD/experiments/mvp/exit/patches/d137/validation/src uv run pytest experiments/mvp/exit/patches/d137/validation/tests/llm/exit experiments/mvp/exit/patches/d137/restart/tests -q --junitxml=experiments/mvp/exit/patches/d137/qualification.xml
Native variables used: OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=VECLIB_MAXIMUM_THREADS=1,
STOWAGE_IN_PORT_WORKERS=1, TOKENIZERS_PARALLELISM=false.

Teacher CPU pilot: 3/3 TRAIN maps passed, wall45.22/56.77/62.68s, moves7/23/22.
Planning2.726/.612/.986s; V4finish18.007/29.952/36.340s;
checker21.687/22.620/21.287s; mask2.456/3.207/3.508s.
Securing evaluations4/124/114; zero unavailable/fail-open. Estimate1core/4GB/noGPU/
1200s; actual166.591s total and0.698GiB PARENT peak (checker children excluded).
These are three gold-backed calibration maps from the pre-D132 epoch, not a
representative success-rate estimate. Recalibrate after installation.
The separate real scheduling exercise produced6 gold +6 no-gold maps and excluded
all73 prior loop maps, with0 held-out body reads;37.07s CPU. Its PRIVATE schedule
must never be used for deployment.

Host restart after leader installation:
zsh experiments/mvp/exit/patches/d137/RESTART-HOST.sh --check-only
zsh experiments/mvp/exit/patches/d137/RESTART-HOST.sh

Parent experiments/mvp/sft/stowlm-v2/adapter; exact siblingDONE.json binding.
Fresh output deployment-d137; imports old used hashes/seeds/IDs, not their old
terminal/generation/audit states. Baseline/fallback says8.2. Completion, numeric
ledger, shared GPU lock, review, current-code/current-adapter smoke, active
securing, lookahead and2GiB MLX cache limits remain required. The frozen clock
audit is first launch+150min, not an already elapsed05:30 deadline. Controller
creates STARTED only inside the admitted runner. Run only from a Metal-capable
Claude host. The expected-byte guard currently REFUSES launch before installation.
Any post-install smoke/model/training checks remain unmeasured, not claimed.

Initial sizing:12maps, k6, P2, B8,100SFTsteps, up to4verification workers (or fewer
on admission). Flags expose each. APPROXIMATION: map-linear phase projection,
overlapping GPU decode and pooled CPU work via their maximum; no double-counting
teacher work. It adapts map count from generation0, includes due audit/control
costs, and reports when even the minimum fails the75min target. Startup baseline
is separately timed. Full successful DPO time and the new complete generation
are UNMEASURED;75min is a target, not a measured promise. No gain is claimed.
Gold replay remains67%; current-epoch reverification remains mandatory.

Integration note: D132 is already installed. D137 changes the source identity;
restart the standalone replay prefetcher on its new epoch shelf after installation,
then require the new adapter/code smoke. Do not relabel old verification caches.
No live source edits, commits, staging, cloud calls, paid calls or GPU work occurred.
'''
(h/'README.md').write_text(text)
(h/'REPORT.md').write_text('D137 ready as patches. 827CPUtests pass; code/test patch applicability and 27target hashes verified.\nTeacher pilot3/3PASS,45.22/56.77/62.68s; fullgeneration/DPOduration unmeasured.\n8.2 RESTART-HOST.sh is gated on exact installed bytes and parent completion; never launched here.\nSee README.md for commands, assumptions, timings, source targets and limitations.\n')
inbox=r/'experiments/mvp/batch-d132/INBOX.md'
block='\n\n## mvp-exit D137 + D139 — READY, patch-only handoff ('+datetime.datetime.now(datetime.timezone.utc).isoformat()+')\n\n'
block+='D132 is installed; these are follow-on patches, not edits to production. No GPU launched.\n\n'
for name,digest in manifest.items():block+=f'- `experiments/mvp/exit/patches/d137/{name}` SHA256 `{digest}`\n'
block+='\n827 CPU tests PASS (97.651s), both patches `git apply --check` PASS against current installed source/tests; private application27target hashes match. Real12map scheduling:6gold/6no-gold, excludes73prior maps. Teacher pilot3/3PASS at45.22/56.77/62.68s (pre-D132 calibration, remeasure).\n\n'
block+='Nine source targets: data.py,selection.py,preferences.py,reverify.py,control.py,runner.py,audit.py,sizing.py(new),final_recipe.py. D139 single-finisher fix and P3 per-child DPO caches included. Keeps D132 dataset pooling + outcome-epoch ACK + D136 checkpoint suffix. Trained gold control now pools both admission and dataset replay.\n\n'
block+='Apply source+tests only when leader authorizes. Expected-byte manifest refuses launch until installed. Host command: `zsh experiments/mvp/exit/patches/d137/RESTART-HOST.sh` (8.2 exactparent marker, fresh deployment-d137,12maps/k6/P2/B8/100steps,max4workers,lookahead/cache2/securing/freshsmoke/allpreviousgates). No automatic launch here. Full successful DPO/generation timing unmeasured; sizing reports target misses honestly.\n\n'
block+='After installation, new epoch re-stales source-bound replay caches; restart standalone replay prefetch on a fresh shelf. Detail/commands: `experiments/mvp/exit/patches/d137/README.md`; measured proof: `measure/REPORT.md`; qualification: `qualification.json`/`.xml`. No leader-only docs edited.\n'
with inbox.open('a') as f:f.write(block)
print(json.dumps(manifest,indent=2))

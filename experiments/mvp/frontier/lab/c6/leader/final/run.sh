#!/bin/zsh
# Leader only. Final benchmark of Engine 0.13.0 from a frozen copy: 18 sealed maps with a fresh 0.9.0 arm at the same load, plus quick tiny and small.
cd .
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
L=experiments/mvp/frontier/lab; LB=$L/c1/leader; H=$L/c6/leader/final; PY=.venv/bin/python
SC=$L/c5/leader/score_ladder.py
E=/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/650fd584-ebd7-4e07-a64a-d5d31504125f/scratchpad/bench-0.13.0/experiments/mvp/frontier/engine/lab_method.py
for i in 0 1 2 3; do
  STOWAGE_VESSEL_CACHE_DIR=/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/650fd584-ebd7-4e07-a64a-d5d31504125f/scratchpad/bench-0.13.0/cache-e$i $PY $SC --set $H/e$i.txt --method $E:propose --finish $E:finish --budget-s 900 --finalize-s 600 --tries 3 --seed 0 --workers 2 --out $H/e$i > $H/e$i.log 2>&1 &
done
for s in quick_tiny quick_small; do
  STOWAGE_VESSEL_CACHE_DIR=/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/650fd584-ebd7-4e07-a64a-d5d31504125f/scratchpad/bench-0.13.0/cache-$s $PY $SC --set $s --method $E:propose --finish $E:finish --budget-s 900 --finalize-s 600 --tries 3 --seed 0 --workers 2 --out $H/$s > $H/$s.log 2>&1 &
done
for i in 0 1 2; do
  $PY $SC --set $H/f$i.txt --method $LB/method_e9_big.py:propose --finish $LB/method_e9_big.py:finish --budget-s 0 --finalize-s 600 --tries 1 --seed 0 --workers 2 --out $H/f$i > $H/f$i.log 2>&1 &
done
wait
date

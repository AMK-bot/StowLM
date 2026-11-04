#!/bin/zsh
cd .
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
E=/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/650fd584-ebd7-4e07-a64a-d5d31504125f/scratchpad/bench-0.12.0/experiments/mvp/frontier/engine/lab_method.py
LB=experiments/mvp/frontier/lab/c1/leader
BB=$LB/bench-0.12.0
PY=.venv/bin/python
SC=experiments/mvp/frontier/lab/score.py
STOWAGE_VESSEL_CACHE_DIR=/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/650fd584-ebd7-4e07-a64a-d5d31504125f/scratchpad/bench-0.12.0/cache-e0 $PY $SC --set $BB/shard0.txt --method $E:propose --finish $E:finish --budget-s 900 --finalize-s 600 --tries 3 --seed 0 --workers 2 --out $BB/e0 > $BB/e0.log 2>&1 &
STOWAGE_VESSEL_CACHE_DIR=/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/650fd584-ebd7-4e07-a64a-d5d31504125f/scratchpad/bench-0.12.0/cache-e1 $PY $SC --set $BB/shard1.txt --method $E:propose --finish $E:finish --budget-s 900 --finalize-s 600 --tries 3 --seed 0 --workers 2 --out $BB/e1 > $BB/e1.log 2>&1 &
$PY $SC --set $BB/all85.txt --method $LB/method_e9_val.py:propose --finish $LB/method_e9_val.py:finish --budget-s 0 --finalize-s 300 --tries 3 --seed 0 --workers 2 --out $BB/f0 > $BB/f0.log 2>&1 &
STOWAGE_VESSEL_CACHE_DIR=/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/650fd584-ebd7-4e07-a64a-d5d31504125f/scratchpad/bench-0.12.0/cache-m0 $PY $SC --set $BB/med0.txt --method $E:propose --finish $E:finish --budget-s 900 --finalize-s 600 --tries 3 --seed 0 --workers 2 --out $BB/m0 > $BB/m0.log 2>&1 &
STOWAGE_VESSEL_CACHE_DIR=/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/650fd584-ebd7-4e07-a64a-d5d31504125f/scratchpad/bench-0.12.0/cache-m1 $PY $SC --set $BB/med1.txt --method $E:propose --finish $E:finish --budget-s 900 --finalize-s 600 --tries 3 --seed 0 --workers 2 --out $BB/m1 > $BB/m1.log 2>&1 &
$PY $SC --set $BB/big.txt --method $LB/method_e9_big.py:propose --finish $LB/method_e9_big.py:finish --budget-s 0 --finalize-s 600 --tries 1 --seed 0 --workers 1 --out $BB/b0 > $BB/b0.log 2>&1 &
wait
date

#!/bin/zsh
# Leader only. Comparison arms on 18 of the sealed holdout maps, 2 per ship and fill, picked by rule before any arm ran.
cd .
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
L=experiments/mvp/frontier/lab; LB=$L/c1/leader; H=$L/c6/leader; PY=.venv/bin/python
SC=$L/c5/leader/score_ladder.py
E=/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/650fd584-ebd7-4e07-a64a-d5d31504125f/scratchpad/bench-0.12.0/experiments/mvp/frontier/engine/lab_method.py
nice -n 7 $PY $SC --set $H/holdout18.txt --method $LB/method_e9_big.py:propose --finish $LB/method_e9_big.py:finish --budget-s 0 --finalize-s 600 --tries 1 --seed 0 --workers 2 --out $H/arm-fresh-big > $H/arm-fresh-big.log 2>&1 &
STOWAGE_VESSEL_CACHE_DIR=/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/650fd584-ebd7-4e07-a64a-d5d31504125f/scratchpad/bench-0.12.0/cache-h0 nice -n 7 $PY $SC --set $H/holdout18.txt --method $E:propose --finish $E:finish --budget-s 900 --finalize-s 600 --tries 3 --seed 0 --workers 2 --out $H/arm-engine-012 > $H/arm-engine-012.log 2>&1 &
wait
date

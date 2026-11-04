#!/bin/zsh
# Leader only. Same frozen method and budgets as run_holdout2.sh, split into six shards because the machine is free.
cd .
L=experiments/mvp/frontier/lab; C=$L/c6/c5-securing; H=$L/c6/leader
. $C/env.sh
for i in 0 1 2 3 4 5; do
  STOWAGE_VESSEL_CACHE_DIR=$PWD/$C/vessel-cache-h$i nice -n 5 $PY $L/c5/leader/score_ladder.py --set $H/h2s$i.txt --method $C/method.py:propose --finish $C/method.py:finish --budget-s 300 --finalize-s 180 --tries 2 --workers 2 --seed 0 --out $H/holdout-securing-2-s$i > $H/holdout-securing-2-s$i.log 2>&1 &
done
wait
date

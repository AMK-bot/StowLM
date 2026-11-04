#!/bin/zsh
# Leader only. c5-securing's second frozen method on the same 72 sealed holdout maps, same budgets as its frozen run.
cd .
L=experiments/mvp/frontier/lab; C=$L/c6/c5-securing
. $C/env.sh
exec nice -n 5 $PY $L/c5/leader/score_ladder.py --set $L/c6/c6-maps/holdout.txt --method $C/method.py:propose --finish $C/method.py:finish --budget-s 300 --finalize-s 180 --tries 2 --workers 2 --seed 0 --out $L/c6/leader/holdout-securing-2

#!/bin/zsh
# Leader only. c5-securing's frozen method on the 72 sealed holdout maps: one clean read, nothing tuned on it.
cd .
L=experiments/mvp/frontier/lab; C=$L/c5/c5-securing
. $C/env.sh
exec nice -n 5 $PY $L/c5/leader/score_ladder.py --set $L/c6/c6-maps/holdout.txt --method $C/method.py:propose --finish $C/method.py:finish --budget-s 240 --finalize-s 120 --tries 2 --workers 2 --seed 0 --out $L/c6/leader/holdout-securing

#!/bin/zsh
# Leader's untuned check: c5-securing's frozen method on the 18 feeder_1700 ladder maps it never saw.
cd .
L=experiments/mvp/frontier/lab; C=$L/c5/c5-securing
. $C/env.sh
exec nice -n 5 $PY $L/c5/leader/score_ladder.py --set $L/c5/leader/f1700-18.txt --method $C/method.py:propose --finish $C/method.py:finish --budget-s 240 --finalize-s 120 --tries 2 --workers 2 --seed 0 --out $L/c5/leader/securing-f1700

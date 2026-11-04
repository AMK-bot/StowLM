#!/bin/sh
# Final runs, one frozen source. Run from the repo root. Plans are derived from unlicensed data, so they go to a git-ignored folder.
# Streams: stream a runs small and medium ships with the master; stream b replays the published plans and runs the two largest ships greedy only; stream c runs those two with the master.
# report_rcspp.py takes the rows of later files over earlier ones, so pass stream b's before stream c's.
SUITE=data/raw/rcsppsuite
HERE=experiments/mvp/frontier/public/rcspp
OUT=$HERE/results
PLANS=data/raw/rcsppsuite-plans
PY=".venv/bin/python -I"
names() { ls $SUITE/Instances | sed 's/.xml//' | grep "^$1_" | tr '\n' ' '; }
mkdir -p "$PLANS"
case "$1" in
  a)
    nice -n 5 $PY $HERE/run_rcspp.py $SUITE $OUT/final_s.jsonl $(names s) --budget 200 --passes 2 --workers 4 --plans "$PLANS"
    nice -n 5 $PY $HERE/run_rcspp.py $SUITE $OUT/final_m.jsonl $(names m) --budget 400 --passes 2 --workers 4 --plans "$PLANS"
    ;;
  b)
    nice -n 8 $PY $HERE/replay.py $SUITE $OUT/replay.jsonl
    nice -n 8 $PY $HERE/run_rcspp.py $SUITE $OUT/final_l.jsonl $(names l) --passes 0 --workers 1 --plans "$PLANS"
    nice -n 8 $PY $HERE/run_rcspp.py $SUITE $OUT/final_xl.jsonl $(names xl) --passes 0 --workers 1 --plans "$PLANS"
    ;;
  c)
    $PY $HERE/run_rcspp.py $SUITE $OUT/sensitivity/l_master.jsonl $(names l) --budget 1200 --passes 1 --workers 5 --plans "$PLANS/l-master"
    $PY $HERE/run_rcspp.py $SUITE $OUT/sensitivity/xl_master.jsonl $(names xl) --budget 1200 --passes 1 --workers 5 --plans "$PLANS/xl-master"
    ;;
esac

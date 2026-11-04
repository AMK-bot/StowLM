#!/bin/zsh
# Resumable: skips configs that already have run.json.
for C in "6k 52" "9k 16" "9k 52" "14k 16" "14k 52" "14k 52 1024" "17k 16 1024" "17k 52 1024"; do
  set -- ${=C}; NAME=long-$1-l$2$([[ ${3:-0} != 0 ]] && echo -b$3)
  [[ -f experiments/sft-pilot/adapters/$NAME/run.json ]] && continue
  ./experiments/sft-pilot/run_long.sh $@ > experiments/sft-pilot/$NAME.log 2>&1
done
echo ALL DONE
./experiments/sft-pilot/run_lr.sh

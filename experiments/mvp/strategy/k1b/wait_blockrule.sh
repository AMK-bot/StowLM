#!/bin/zsh
# Writes BLOCKRULE-PAIR.md once BLOCKRULE.md has a finished small-val row for every K1b map; gives up after 12 h.
cd "$(dirname "$0")/../../../.."
for i in {1..720}; do
  PYTHONPATH=experiments/mvp/strategy/k1b .venv/bin/python experiments/mvp/strategy/k1b/pair_blockrule.py && exit 0
  /bin/sleep 60
done

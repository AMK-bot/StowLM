#!/bin/zsh
# Detached chain for the leader's final order: 9.1 once, then 9.2, then release the ledger row. Stops at the first failure.
cd "$(dirname "$0")/../../../.."
A=experiments/mvp/sft/stowlm-v2/adapter
L=experiments/mvp/runs
FINALIZE_S=120 zsh experiments/mvp/sft/eval91/run_91.sh $A $L/test-91 || { echo "9.1 failed $(date)"; exit 1; }
date > $L/test-91/DONE-91
zsh experiments/mvp/sft/eval92/run_92.sh $A || { echo "9.2 failed $(date)"; exit 2; }
sed -i '' '/^| stowlm-91-92\/final |/d' experiments/fleet/ledger.md
print -r -- "
- stowlm-91-92/final (fix-t8-sft, 30 Oct): 9.1 on test v2.1 + 9.2 showcases, exclusive GPU; finished $(date '+%Y-%m-%d %H:%M %Z')." >> experiments/fleet/history.md
date > $L/stowlm-showcase/DONE-92

#!/bin/zsh
# D143: armed restart. Waits for 8.2's DONE.json, releases fix-t8-sft's own 8.2 GPU ledger row (the loop's GPU admission
# refuses while any fix-t8-sft/ row exists, and the leader's executive may not be resumed by then), then runs
# RESTART-HOST.sh: parent audits of 8.1 and 8.2, the parent choice, and the loop queue. A capacity refusal raises rather
# than waits, so the step retries; parent_audit.py resumes from any finished audit. Run detached; one copy only.
set -uo pipefail
cd "$(dirname "$0")/../../../../../.."
DONE=experiments/mvp/sft/stowlm-v2/DONE.json
say() { print -r -- "$(date '+%H:%M:%S') $*"; }
say "armed: waiting for $DONE"
until [[ -f $DONE ]]; do sleep 30; done
say "8.2 complete: $(tr -d '\n' < $DONE | cut -c1-160)"
sed -i '' '/^| fix-t8-sft\/8.2-sft |/d' experiments/fleet/ledger.md
print -r -- "- fix-t8-sft/8.2-sft (D137/D141 parent), released by arm_restart.sh $(date '+%Y-%m-%d %H:%M %Z'): 2 epochs, $(tr -d '\n' < $DONE | cut -c1-120)" >> experiments/fleet/history.md
say "ledger row released"
for attempt in {1..120}; do
  say "RESTART-HOST attempt $attempt"
  if zsh experiments/mvp/exit/patches/d137/revision2/RESTART-HOST.sh; then
    say "restart queued"; exit 0
  fi
  say "attempt $attempt refused or failed; retrying in 120 s"
  sleep 120
done
say "gave up after 120 attempts"; exit 1

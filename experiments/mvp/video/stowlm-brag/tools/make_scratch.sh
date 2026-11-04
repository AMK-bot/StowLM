#!/usr/bin/env bash
# Scratch timing voice with macOS `say`, only for shots with no recorded vo/shot-NN.wav.
# Files are named scratch-NN.* and never go into a release render (build.sh marks SCRATCH on screen).
set -euo pipefail
source "$(dirname "$0")/env.sh"
OUT="$VIDEO/demo-cut/scratch"; VO="$VIDEO/record-kit/vo"
mkdir -p "$OUT"
py - "$VIDEO/stowlm-brag/shots.json" <<'P' | while IFS=$'\t' read -r nn text; do
import json, sys
for s in json.load(open(sys.argv[1]))["shots"]:
    print(f'{s["id"]:02}\t{s["voice"]}')
P
  [[ -f "$VO/shot-$nn.wav" ]] && continue
  if [[ ! -f "$OUT/scratch-$nn.wav" || "$OUT/scratch-$nn.txt" -ot "$VIDEO/stowlm-brag/shots.json" ]]; then
    say -r 150 -o "$OUT/scratch-$nn.aiff" "$text"
    afconvert -f WAVE -d LEI16@48000 -c 1 "$OUT/scratch-$nn.aiff" "$OUT/scratch-$nn.wav"
    printf '%s\n' "$text" > "$OUT/scratch-$nn.txt"
  fi
done
echo "scratch voice ready in $OUT"

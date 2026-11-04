#!/usr/bin/env bash
# 10 s silent x-ray explainer for the write-up: 182 of 200 -> 2 of 200, both caveats on screen (D267 wording).
#   ./build.sh | ./build.sh check | ./build.sh render
set -euo pipefail
source "$(dirname "$0")/../stowlm-brag/tools/env.sh"
HERE="$VIDEO/xray-explainer"; OUT="$HERE/out"; TOOLS="$VIDEO/stowlm-brag/tools"
mode="${1:-compose}"
(cd "$TOOLS" && py build_extras.py xray --out "$OUT")
py "$TOOLS/check_honesty.py" explainer "$OUT/composition/index.html"
(cd "$OUT/composition" && "$HF" lint .)
[[ "$mode" == compose ]] && exit 0
(cd "$OUT/composition" && "$HF" check . --timeout 120000)
[[ "$mode" == check ]] && exit 0
need_ffmpeg
guard "$OUT" "$OUT/xray.mp4" "$OUT/xray.jpg"
(cd "$OUT/composition" && "$HF" render . --quality looks --output "$OUT/xray.mp4")
guard "$OUT" "$OUT/xray.mp4" "$OUT/xray.jpg"
ffmpeg -y -loglevel error -ss 9 -i "$OUT/xray.mp4" -frames:v 1 -q:v 2 "$OUT/xray.jpg"
echo "done: $OUT/xray.mp4"

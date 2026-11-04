#!/usr/bin/env bash
# Launch cut for the X post: about 27 s, no voice, music + captions + end card.
#   ./build.sh | ./build.sh check | ./build.sh render
set -euo pipefail
source "$(dirname "$0")/../stowlm-brag/tools/env.sh"
HERE="$VIDEO/launch-cut"; OUT="$HERE/out"; TOOLS="$VIDEO/stowlm-brag/tools"
mode="${1:-compose}"
py "$TOOLS/build_comp.py" launch --out "$OUT"
py "$TOOLS/check_honesty.py" launch "$OUT/composition/index.html" "$HERE/share-copy.txt"
(cd "$OUT/composition" && "$HF" lint .)
[[ "$mode" == compose ]] && exit 0
(cd "$OUT/composition" && "$HF" check . --timeout 120000)
[[ "$mode" == check ]] && exit 0
need_ffmpeg
guard "$OUT" "$OUT/launch-draft.mp4" "$OUT/launch-draft.jpg" "$OUT/launch.poster.mp4"
(cd "$OUT/composition" && "$HF" render . --quality draft --output "$OUT/launch-draft.mp4")
pt=$(py -c "import json;s=[x for x in json.load(open('$OUT/timing.json'))['segments'] if x['shot']==7][0];print(s['start_s']+s['dur_s']/2)")  # one complete caption
guard "$OUT" "$OUT/launch-draft.mp4" "$OUT/launch-draft.jpg" "$OUT/launch.poster.mp4"
ffmpeg -y -loglevel error -ss "$pt" -i "$OUT/launch-draft.mp4" -frames:v 1 -q:v 2 "$OUT/launch-draft.jpg"
guard "$OUT" "$OUT/launch-draft.mp4" "$OUT/launch-draft.jpg" "$OUT/launch.poster.mp4"
ffmpeg -y -loglevel error -i "$OUT/launch-draft.mp4" -i "$OUT/launch-draft.jpg" \
  -filter_complex "[0:v][1:v]overlay=0:0:enable='eq(n,0)'[v]" -map "[v]" -map "0:a?" \
  -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p -c:a copy -movflags +faststart "$OUT/launch.poster.mp4"
guard "$OUT" "$OUT/launch-draft.mp4" "$OUT/launch-draft.jpg" "$OUT/launch.poster.mp4"
mv "$OUT/launch.poster.mp4" "$OUT/launch-draft.mp4"
echo "done: $OUT/launch-draft.mp4 ($(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/launch-draft.mp4") s)"

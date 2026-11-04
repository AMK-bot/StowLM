#!/usr/bin/env bash
# 9:16 launch cut (1080x1920) for Reels/Shorts/TikTok-style feeds and the Community Vote.
#   ./build.sh | ./build.sh check | ./build.sh render
set -euo pipefail
source "$(dirname "$0")/../stowlm-brag/tools/env.sh"
HERE="$VIDEO/social"; OUT="$HERE/out"; TOOLS="$VIDEO/stowlm-brag/tools"
mode="${1:-compose}"
(cd "$TOOLS" && py build_extras.py social --out "$OUT")
py "$TOOLS/check_honesty.py" launch "$OUT/composition/index.html" "$VIDEO/launch-cut/share-copy.txt"
(cd "$OUT/composition" && "$HF" lint .)
[[ "$mode" == compose ]] && exit 0
(cd "$OUT/composition" && "$HF" check . --timeout 120000)
[[ "$mode" == check ]] && exit 0
need_ffmpeg
guard "$OUT" "$OUT/social-draft.mp4" "$OUT/social-draft.jpg" "$OUT/social.poster.mp4"
(cd "$OUT/composition" && "$HF" render . --quality draft --output "$OUT/social-draft.mp4")
pt=$(py -c "import json;s=[x for x in json.load(open('$OUT/timing.json'))['segments'] if x['shot']==7][0];print(s['start_s']+s['dur_s']/2)")  # one complete caption
guard "$OUT" "$OUT/social-draft.mp4" "$OUT/social-draft.jpg" "$OUT/social.poster.mp4"
ffmpeg -y -loglevel error -ss "$pt" -i "$OUT/social-draft.mp4" -frames:v 1 -q:v 2 "$OUT/social-draft.jpg"
guard "$OUT" "$OUT/social-draft.mp4" "$OUT/social-draft.jpg" "$OUT/social.poster.mp4"
ffmpeg -y -loglevel error -i "$OUT/social-draft.mp4" -i "$OUT/social-draft.jpg" \
  -filter_complex "[0:v][1:v]overlay=0:0:enable='eq(n,0)'[v]" -map "[v]" -map "0:a?" \
  -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p -c:a copy -movflags +faststart "$OUT/social.poster.mp4"
guard "$OUT" "$OUT/social-draft.mp4" "$OUT/social-draft.jpg" "$OUT/social.poster.mp4"
mv "$OUT/social.poster.mp4" "$OUT/social-draft.mp4"
echo "done: $OUT/social-draft.mp4"

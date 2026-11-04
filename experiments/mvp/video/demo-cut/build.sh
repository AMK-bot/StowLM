#!/usr/bin/env bash
# Demo cut (3:00 cap) from the 12 recorded shots.
#   ./build.sh          compose + honesty check + hyperframes lint (no browser)
#   ./build.sh check    + hyperframes check (headless Chrome)
#   ./build.sh render   + render, poster frame baked as frame 0, subtitles, credits check
# Voice per shot: record-kit/vo/shot-NN.wav if present, else scratch (macOS say) and SCRATCH on screen.
# Output: out/demo-draft.mp4 while any scratch is used, out/demo-final.mp4 once every shot has your voice.
set -euo pipefail
source "$(dirname "$0")/../stowlm-brag/tools/env.sh"
HERE="$VIDEO/demo-cut"; OUT="$HERE/out"; TOOLS="$VIDEO/stowlm-brag/tools"
mode="${1:-compose}"

"$TOOLS/make_scratch.sh"
py "$TOOLS/build_comp.py" demo --out "$OUT"
py "$TOOLS/check_honesty.py" demo "$OUT/composition/index.html" "$OUT/subtitles.srt" "$HERE/share-copy.txt"
(cd "$OUT/composition" && "$HF" lint .)
[[ "$mode" == compose ]] && exit 0
(cd "$OUT/composition" && "$HF" check . --timeout 120000)
[[ "$mode" == check ]] && exit 0

need_ffmpeg
scratch=$(py -c "import json;print(int(json.load(open('$OUT/timing.json'))['scratch_voice']))")
name=demo-final; quality=delivery
[[ "$scratch" == 1 ]] && { name=demo-draft; quality=draft; }
guard "$OUT" "$OUT/$name.mp4" "$OUT/$name.jpg" "$OUT/$name.poster.mp4" "$OUT/$name.srt"
(cd "$OUT/composition" && "$HF" render . --quality "$quality" --output "$OUT/$name.mp4")
# poster: shot 11's held card after its last caption ends, so no caption is cut mid-line
pt=$(py - "$OUT/timing.json" "$OUT/subtitles.srt" <<'P'
import json, re, sys
s = [x for x in json.load(open(sys.argv[1]))["shots"] if x["shot"] == 11][0]
a, b = s["start_s"], s["start_s"] + s["slot_s"]
ends = [int(h)*3600 + int(m)*60 + int(sec) + int(ms)/1000
        for h, m, sec, ms in re.findall(r"--> (\d\d):(\d\d):(\d\d),(\d\d\d)", open(sys.argv[2]).read())]
last = max([e for e in ends if a < e <= b], default=a + 4)
print(round(min(last + 0.9, b - 0.4), 2))
P
)
guard "$OUT" "$OUT/$name.mp4" "$OUT/$name.jpg" "$OUT/$name.poster.mp4" "$OUT/$name.srt"
ffmpeg -y -loglevel error -ss "$pt" -i "$OUT/$name.mp4" -frames:v 1 -q:v 2 "$OUT/$name.jpg"
guard "$OUT" "$OUT/$name.mp4" "$OUT/$name.jpg" "$OUT/$name.poster.mp4" "$OUT/$name.srt"
ffmpeg -y -loglevel error -i "$OUT/$name.mp4" -i "$OUT/$name.jpg" \
  -filter_complex "[0:v][1:v]overlay=0:0:enable='eq(n,0)'[v]" -map "[v]" -map "0:a?" \
  -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p -c:a copy -movflags +faststart "$OUT/$name.poster.mp4"
guard "$OUT" "$OUT/$name.mp4" "$OUT/$name.jpg" "$OUT/$name.poster.mp4" "$OUT/$name.srt"
mv "$OUT/$name.poster.mp4" "$OUT/$name.mp4"
guard "$OUT" "$OUT/$name.mp4" "$OUT/$name.jpg" "$OUT/$name.poster.mp4" "$OUT/$name.srt"
cp "$OUT/subtitles.srt" "$OUT/$name.srt"
(cd "$TOOLS" && py youtube.py --out "$OUT")
py "$TOOLS/check_honesty.py" demo "$OUT/youtube-description.txt"
dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/$name.mp4")
py - "$OUT/timing.json" "$dur" <<'P'
import json, sys
t = json.load(open(sys.argv[1])); d = float(sys.argv[2])
ok = abs(d - t["total_s"]) < 0.5 and d <= t["cap_s"]
print(f"credits check: rendered {d:.2f} s, composed {t['total_s']} s, cap {t['cap_s']} s -> {'OK' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
P
echo "done: $OUT/$name.mp4 ($OUT/$name.jpg, $OUT/$name.srt)"

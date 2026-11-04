#!/usr/bin/env bash
# README hero: an 8 s muted loop (shot 7 plan change, shot 8 drag) and a poster frame.
#   ./build.sh | ./build.sh check | ./build.sh render
# Render outputs: out/hero.mp4 (1280 wide, H.264, no audio, loops cleanly), out/hero.jpg (poster),
# out/hero.gif (960 wide, 12 fps, palette-optimised; GitHub autoplays GIFs, not mp4 in README markdown).
set -euo pipefail
source "$(dirname "$0")/../stowlm-brag/tools/env.sh"
HERE="$VIDEO/readme-hero"; OUT="$HERE/out"; TOOLS="$VIDEO/stowlm-brag/tools"
mode="${1:-compose}"
(cd "$TOOLS" && py build_extras.py readme-hero --out "$OUT")
py "$TOOLS/check_honesty.py" explainer "$OUT/composition/index.html"
(cd "$OUT/composition" && "$HF" lint .)
[[ "$mode" == compose ]] && exit 0
(cd "$OUT/composition" && "$HF" check . --timeout 120000)
[[ "$mode" == check ]] && exit 0
need_ffmpeg
guard "$OUT" "$OUT/hero-full.mp4" "$OUT/hero.mp4" "$OUT/hero.jpg" "$OUT/hero.gif"
(cd "$OUT/composition" && "$HF" render . --quality looks --output "$OUT/hero-full.mp4")
guard "$OUT" "$OUT/hero-full.mp4" "$OUT/hero.mp4" "$OUT/hero.jpg" "$OUT/hero.gif"
ffmpeg -y -loglevel error -i "$OUT/hero-full.mp4" -an -vf "scale=1280:-2" -c:v libx264 -crf 20 -pix_fmt yuv420p -movflags +faststart "$OUT/hero.mp4"
guard "$OUT" "$OUT/hero-full.mp4" "$OUT/hero.mp4" "$OUT/hero.jpg" "$OUT/hero.gif"
ffmpeg -y -loglevel error -ss 2.5 -i "$OUT/hero-full.mp4" -frames:v 1 -q:v 2 "$OUT/hero.jpg"
guard "$OUT" "$OUT/hero-full.mp4" "$OUT/hero.mp4" "$OUT/hero.jpg" "$OUT/hero.gif"
ffmpeg -y -loglevel error -i "$OUT/hero-full.mp4" -vf "fps=12,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4" "$OUT/hero.gif"
ls -la "$OUT"/hero.*

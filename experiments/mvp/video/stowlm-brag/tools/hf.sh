#!/usr/bin/env bash
# Pinned HyperFrames with all its state and caches kept inside experiments/mvp/video/.tools/state/ (code12 P2).
# HyperFrames writes ~/.hyperframes/{running,config.json,catalog,models}, ~/.local/state and ~/.cache via os.homedir(),
# which no env variable covers, so HOME is redirected for this subprocess only. Its documented cache variables
# (HYPERFRAMES_EXTRACT_CACHE_DIR, HYPERFRAMES_FONT_CACHE_DIR, HYPERFRAMES_MEDIA_HOME) and XDG_* are set too.
set -euo pipefail
VIDEO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE="$VIDEO/.tools/state"
mkdir -p "$STATE/home" "$STATE/cache/extract" "$STATE/cache/fonts" "$STATE/media" "$STATE/xdg-cache" "$STATE/xdg-state" "$STATE/xdg-config"
export HOME="$STATE/home"
export XDG_CACHE_HOME="$STATE/xdg-cache" XDG_STATE_HOME="$STATE/xdg-state" XDG_CONFIG_HOME="$STATE/xdg-config"
export HYPERFRAMES_EXTRACT_CACHE_DIR="$STATE/cache/extract"
export HYPERFRAMES_FONT_CACHE_DIR="$STATE/cache/fonts"
export HYPERFRAMES_MEDIA_HOME="$STATE/media"
exec "$VIDEO/.tools/node_modules/.bin/hyperframes" "$@"

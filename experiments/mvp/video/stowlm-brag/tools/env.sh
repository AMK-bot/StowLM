# Sourced by the build scripts. Pinned, local-only HyperFrames (D298).
KIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)"
VIDEO="$KIT_ROOT/experiments/mvp/video"
HF="$VIDEO/stowlm-brag/tools/hf.sh"  # pinned binary, state confined to .tools/state (code12 P2)
export PUPPETEER_SKIP_DOWNLOAD=1
export PATH="/opt/homebrew/bin:$PATH"
export HYPERFRAMES_BROWSER_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
export PUPPETEER_EXECUTABLE_PATH="$HYPERFRAMES_BROWSER_PATH"
export HYPERFRAMES_NO_TELEMETRY=1 DO_NOT_TRACK=1 HYPERFRAMES_NO_UPDATE_CHECK=1
export HYPERFRAMES_NO_AUTO_INSTALL=1 HYPERFRAMES_NO_OPTIONAL_INSTALL=1
export UV_CACHE_DIR="$KIT_ROOT/.uv-cache"
py() { uv run --offline --no-project --python 3.12 python "$@"; }
need_ffmpeg() {
  command -v ffmpeg >/dev/null || { echo "ffmpeg not found: rendering needs it (leader decision pending: brew install ffmpeg)" >&2; exit 3; }
}
# Write fence for shell-side outputs (ffmpeg, cp, mv, hyperframes --output): fails on a symlink or an escape.
guard() { py "$VIDEO/stowlm-brag/tools/safe_path.py" "$@"; }

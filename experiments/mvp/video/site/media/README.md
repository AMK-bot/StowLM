# media/

Everything the page loads, all relative paths, no network.

| File here | Comes from | Built by / notes |
|---|---|---|
| `launch.mp4` | `experiments/mvp/video/launch-cut/out/launch-draft.mp4` | `experiments/mvp/video/launch-cut/build.sh render`. Plays in the hero and in the "Watch the launch cut" card until the full demo video is hosted |
| `launch-poster.jpg` | `experiments/mvp/video/launch-cut/out/launch-draft.jpg` | Same run (the poster frame `build.sh` extracts); 1920×1080. Also the `og:image` / `twitter:image` social card |
| `pipeline.svg` | hand-written (D319; redrawn 3 Nov, D369) | Architecture diagram inside a "StowLM user software" app window; light only; system fonts, no external assets. Each software box says how it was developed. Figures from `docs/headline.md` (reader 118 of 128 sealed v3; mask 182 → 2 with both caveats). PNG copy for the README: `readme-hero/out/pipeline.png` (2560×1916, rendered by headless Chrome at 2×) |
| `xray.mp4` | `xray-explainer/out/xray.mp4` | 10 s, silent; `xray-explainer/build.sh render` |
| `xray-poster.jpg` | `xray-explainer/out/xray.jpg` | Frame at 9 s: both counts and both caveats on screen |
| `charts/pipeline-passes.svg` | `experiments/mvp/charts/pipeline-passes.svg` | hashes below |
| `charts/instruction-binding.svg` | `experiments/mvp/charts/instruction-binding.svg` | |
| `charts/reader-exact-match.svg` | `experiments/mvp/charts/reader-exact-match.svg` | |
| `charts/decode-mask-overrides.svg` | `experiments/mvp/charts/decode-mask-overrides.svg` | |
| `hero-bay-plan.pdf` | `experiments/mvp/pipeline/package/fixtures/hero-run/package/bay-plan.html` (v4 hero package, pipeline acf2f275…; html sha256 `af7c88b54aff4fe52a72fa9decedf6e0beb5fe4f96fe79913101f2ddd672a995`) | Printed by `experiments/mvp/pipeline/package/print_check.py` (`check_print`, read-only; its temp Chrome profile redirected to the session scratchpad so nothing was written under `package/`). 6 A3 pages, 3,016,287 bytes, sha256 `53fc2dafbdb04ba1860ef4907bd81fc839f3869b07f655cb1ea6d4d245889d66`; 6 bay pages, 143 cell labels, no overflow, no console errors, no remote requests, `ok: true` |
| `favicon.svg` | hand-written | 32×32 icon; stops the browser's root `/favicon.ico` request on a project Pages site |

Charts are copied byte for byte; sha256 matches `experiments/mvp/charts/README.md`:

- `pipeline-passes.svg` `1563eed1592bf1e9e6387dd9d211272cbb0e4e7a513cbd7e0830de9a6515915e`
- `instruction-binding.svg` `8512f92e3067e4a4f23825a6aeccbd04db70015a55e611c76745baa193ae2b95`
- `reader-exact-match.svg` `fc06bae0bbff71556a49e1227090b94fd4fbaab8ed7d120a71bc5f9e13d2206c`
- `decode-mask-overrides.svg` `c38fa06747c85429963fe346386c73cb2298bb9121ffb4b1f18ebb14f705c7f5`

None has external references (no href, url(), @import, script, image, foreignObject or @font-face). If the charts are regenerated, copy them again and update these hashes.

The film credits must keep "Music: Sascha Ende, ende.app (CC BY 4.0)".

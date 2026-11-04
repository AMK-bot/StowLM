"""Extra compositions from shots.json, beside the demo and launch cuts (D300).

  build_extras.py social      --out DIR   9:16 launch cut (1080x1920), captions inside platform safe areas
  build_extras.py readme-hero --out DIR   8 s muted loop for the README hero, 1920x1080
  build_extras.py xray        --out DIR   10 s x-ray before/after explainer (182 -> 2), silent

Each writes DIR/composition/index.html (+ assets/) and DIR/timing.json. CSS animation only; no GSAP, no CDN.
"""
import argparse
import json
from pathlib import Path

from build_comp import ROOT, audio_seconds, chip_class, contained_out, copy, duck_lane, esc, launch_segments, load, shot_proxy
from safe_path import write_text

BASE = """*{margin:0;padding:0;box-sizing:border-box}
html,body{width:%(w)dpx;height:%(h)dpx;overflow:hidden;background:#050a16}
#root{position:relative;width:100%%;height:100%%;font-family:Inter,sans-serif;color:#f4f6fb;background:#050a16}
@keyframes pop{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:none}}
@keyframes fade{from{opacity:0}to{opacity:1}}
@keyframes rise{from{opacity:0;transform:translateY(24px)}to{opacity:1;transform:none}}
"""


def page(title, w, h, total, css, body):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width={w}, height={h}" />
<title>{esc(title)}</title>
<style>{BASE % {"w": w, "h": h}}{css}</style>
</head>
<body>
<div id="root" data-composition-id="main" data-no-timeline data-start="0" data-duration="{total:.3f}" data-width="{w}" data-height="{h}">
{body}
</div>
</body>
</html>
"""


def write(out: Path, title, w, h, total, css, els, info):
    comp = out / "composition"
    write_text(comp / "index.html", page(title, w, h, total, css, "\n".join(els)))
    info["total_s"] = round(total, 3)
    write_text(out / "timing.json", json.dumps(info, indent=2) + "\n")
    print(f"{info['cut']}: {info['total_s']} s, {w}x{h}")


# 9:16. Safe area: keep text out of the top 250 px and bottom 420 px (platform chrome), 70 px side margins.
SOCIAL_CSS = """
.cropbox{position:absolute;left:0;width:1080px;overflow:hidden;z-index:1}
.cropvid{position:absolute;z-index:1}
.brand{position:absolute;left:70px;top:260px;font-size:44px;font-weight:800;letter-spacing:-.01em;color:#4be0a0;z-index:5}
.foot{position:absolute;left:300px;right:70px;top:272px;text-align:right;font-size:28px;color:#c9d2e3;z-index:5}
.big{position:absolute;left:60px;right:60px;top:330px;height:240px;display:flex;align-items:center;justify-content:center;text-align:center;z-index:6;animation:rise .35s ease-out both}
.big span{font-size:64px;line-height:1.12;font-weight:900;color:#fff}
.chips{position:absolute;left:60px;right:60px;top:1370px;display:flex;flex-direction:column;align-items:center;gap:10px;z-index:5}
.chip{padding:8px 20px;text-align:center;line-height:1.25;border-radius:18px;background:#0b1630;border:2px solid rgba(160,180,215,.45);font-size:28px;font-weight:600;animation:pop .45s ease-out both}
.chip.held,.chip.proj{border-color:#ffb547;color:#ffd9a0}
.card{position:absolute;left:0;top:0;width:1080px;height:1920px;background:radial-gradient(ellipse at 50% 40%,#0f2347 0%,#050a16 70%);display:flex;flex-direction:column;justify-content:center;gap:24px;padding:260px 64px 420px;text-align:center;z-index:8}
.card h1{font-size:120px;font-weight:800;animation:rise .6s ease-out both}
.card p{font-size:32px;line-height:1.3;color:#c9d2e3;animation:fade .6s ease-out both}
.card p.url{font-size:40px;color:#4be0a0;font-weight:800}
.card p.claim{font-size:42px;color:#ffd9a0;font-weight:700}
.card p.small{font-size:28px}
"""

# The video area: y 590..1350. Each segment crops a region of the 1920x1080 take to the full 1080 width.
AREA_TOP, AREA_H = 590, 760


def build_social(cfg, out: Path):
    assets = out / "composition" / "assets"
    shots_dir = ROOT / cfg["shots_dir"]
    els, t, segs, windows = [], 0.0, [], []
    for k, g in enumerate(launch_segments(cfg, assets, shots_dir)):
        d = g["dur"]
        x0, y0, x1, y1 = g.get("crop") or [0, 0, 1920, 1080]
        sc = 1080 / (x1 - x0)
        h = min(AREA_H, round((y1 - y0) * sc))
        top = AREA_TOP + (AREA_H - h) // 2
        # untimed crop window; the timed video inside it is placed so the crop region fills the window
        els.append(f'<div id="s-box-{k}" class="cropbox" style="top:{top}px;height:{h}px">'
                   f'<video id="s-seg-{k}" class="cropvid clip" muted playsinline src="assets/shots/{g["pn"]}" '
                   f'style="left:{-round(x0 * sc)}px;top:{-round(y0 * sc)}px;width:{round(1920 * sc)}px;height:{round(1080 * sc)}px" '
                   f'data-start="{t:.3f}" data-duration="{d:.3f}" data-media-start="{g["ms"]}" data-playback-rate="{g["rate"]}" '
                   f'data-track-index="0"></video></div>')
        els.append(f'<div id="s-big-{k}" class="big clip" data-start="{t + 0.05:.3f}" data-duration="{d - 0.05:.3f}" data-track-index="6"><span>{esc(g["caption"])}</span></div>')
        chips = "".join(f'<span class="{chip_class(c)}">{esc(c)}</span>' for c in g["chips"])
        els.append(f'<div id="s-chip-{k}" class="chips clip" data-start="{t + 0.1:.3f}" data-duration="{d - 0.1:.3f}" data-track-index="5">{chips}</div>')
        if g.get("radio"):
            rc = ROOT / cfg["radio_clip"]["path"]
            copy(rc, assets / "radio" / rc.name)
            rl = audio_seconds(rc)
            els.append(f'<audio id="s-radio-{k}" data-start="{t:.3f}" data-duration="{rl:.3f}" data-volume="1" '
                       f'data-track-index="4" src="assets/radio/{rc.name}"></audio>')
            windows.append((t, t + rl))
        segs.append({"shot": g["shot"], "start_s": round(t, 3), "dur_s": d, "crop": [x0, y0, x1, y1]})
        t += d
    body_end = t
    els.insert(0, f'<div id="s-brand" class="brand clip" data-start="0" data-duration="{body_end:.3f}" data-track-index="5">StowLM</div>')
    els.insert(1, f'<div id="s-foot" class="foot clip" data-start="0" data-duration="{body_end:.3f}" data-track-index="5">{esc(cfg["whole_film_label"])}</div>')
    ec = cfg["launch"]["end_card"]
    ps = [f"<h1>{esc(x)}</h1>" if c == "h1" else f'<p class="{c}">{esc(x)}</p>' for c, x in ec["lines"]]
    d_end = float(ec["dur"])
    els.append(f'<div id="s-end" class="card clip" data-start="{t:.3f}" data-duration="{d_end:.3f}" data-track-index="8">{"".join(ps)}</div>')
    t += d_end
    m = cfg["music"]
    copy(ROOT / m["dir"] / m["launch"], assets / "music" / m["launch"])
    lane = duck_lane(windows, 0.0, t, 0.8, 0.3)
    els.append(f'<audio id="s-music" data-start="0" data-duration="{t:.3f}" data-volume="1" data-fade-in="0.4" data-fade-out="2" '
               f'data-track-index="2" data-automation="{esc(json.dumps(lane))}" src="assets/music/{m["launch"]}"></audio>')
    write(out, "StowLM social vertical", 1080, 1920, t, SOCIAL_CSS, els, {"cut": "social", "segments": segs})


HERO_CSS = """
.band{position:absolute;left:0;top:0;width:1920px;height:140px;background:#050a16;border-bottom:3px solid rgba(75,224,160,.55);z-index:3}
.shot{position:absolute;left:0;top:140px;width:1920px;height:940px;object-fit:contain;z-index:1}
.title{position:absolute;left:60px;top:22px;display:flex;align-items:baseline;gap:28px;z-index:6}
.title b{font-size:72px;font-weight:800;line-height:1}
.title span{font-size:30px;color:#c9d2e3}
.chips{position:absolute;top:16px;right:40px;display:flex;flex-direction:column;align-items:flex-end;gap:8px;z-index:5}
.chip{padding:8px 16px;border-radius:999px;background:#0b1630;border:1.5px solid rgba(160,180,215,.45);font-size:28px;font-weight:600}
"""
HERO = [(7, 2.0, 4.0), (8, 1.0, 4.0)]  # (shot, media_start, dur): the plan changes, then a drag gets judged


def build_hero(cfg, out: Path):
    assets = out / "composition" / "assets"
    shots = {s["id"]: s for s in cfg["shots"]}
    els, t = [], 0.0
    for n, ms, d in HERO:
        s = shots[n]
        pn = shot_proxy(ROOT / cfg["shots_dir"] / s["file"], assets / "shots")
        els.append(f'<video id="h-shot-{n:02}" class="shot clip" muted playsinline src="assets/shots/{pn}" '
                   f'data-start="{t:.3f}" data-duration="{d:.3f}" data-media-start="{ms}" data-track-index="0"></video>')
        chips = [s["chips"][0], cfg["whole_film_label"]]  # the take's own label, plus the synthetic label (final-logic S21)
        els.append(f'<div id="h-chip-{n:02}" class="chips clip" data-start="{t:.3f}" data-duration="{d:.3f}" data-track-index="5">'
                   + "".join(f'<span class="{chip_class(c)}">{esc(c)}</span>' for c in chips) + '</div>')
        t += d
    els.insert(0, f'<div id="h-band" class="band clip" data-start="0" data-duration="{t:.3f}" data-track-index="2"></div>')
    els.append(f'<div id="h-title" class="title clip" data-start="0" data-duration="{t:.3f}" data-track-index="6">'
               f'<b>StowLM</b><span>Models propose. Software decides.</span></div>')
    write(out, "StowLM README hero", 1920, 1080, t, HERO_CSS, els,
          {"cut": "readme-hero", "segments": HERO, "poster_at_s": 2.5, "muted": True})


XRAY_CSS = """
.bg{position:absolute;left:0;top:0;width:1920px;height:1080px;background:radial-gradient(ellipse at 50% 30%,#0f2347 0%,#050a16 72%);z-index:0}
.hd{position:absolute;left:140px;top:90px;font-size:28px;letter-spacing:.14em;font-weight:700;color:#4be0a0;z-index:2}
.q{position:absolute;left:140px;top:140px;width:1640px;font-size:52px;font-weight:800;line-height:1.15;z-index:2;animation:rise .5s ease-out both}
.row{position:absolute;left:140px;width:1640px;z-index:2;animation:fade .5s ease-out both}
.row .lab{font-size:32px;color:#c9d2e3;margin-bottom:12px}
.row .num{font-size:96px;font-weight:800;line-height:1}
.track{position:relative;height:30px;margin-top:18px;background:rgba(160,180,215,.16);border-radius:15px;overflow:hidden}
.bar{position:absolute;left:0;top:0;bottom:0;border-radius:15px}
.before .num{color:#ffb547}.before .bar{width:91%;background:#ffb547;animation:grow1 1.2s ease-out both}
.after .num{color:#4be0a0}.after .bar{width:1%;background:#4be0a0;animation:grow2 .8s ease-out both}
@keyframes grow1{from{width:0}to{width:91%}}
@keyframes grow2{from{width:0}to{width:1%}}
.cav{position:absolute;left:140px;width:1640px;top:840px;font-size:30px;line-height:1.4;color:#f4f6fb;z-index:2;animation:fade .5s ease-out both}
.cav p+p{margin-top:8px}
.lbl{position:absolute;right:60px;top:40px;padding:8px 16px;border-radius:999px;border:1.5px solid rgba(160,180,215,.45);background:#0b1630;font-size:28px;font-weight:600;z-index:3}
"""


def build_xray(cfg, out: Path):
    T = 10.0
    els = [
        f'<div id="x-bg" class="bg clip" data-start="0" data-duration="{T}" data-track-index="0"></div>',
        f'<div id="x-lbl" class="lbl clip" data-start="0" data-duration="{T}" data-track-index="5">Recorded answer · 200 validation sentences, not the locked test</div>',
        f'<div id="x-hd" class="hd clip" data-start="0" data-duration="{T}" data-track-index="1">STOWLM READER · X-RAY</div>',
        f'<div id="x-q" class="q clip" data-start="0.2" data-duration="{T - 0.2}" data-track-index="2">How often did the mask override the model\'s first choice?</div>',
        f'<div id="x-before" class="row before clip" style="top:330px" data-start="1.0" data-duration="{T - 1.0}" data-track-index="3">'
        '<div class="lab">Before fine-tuning (untrained Muse Glimmer 30B)</div><div class="num">182 of 200</div>'
        '<div class="track"><div class="bar"></div></div></div>',
        f'<div id="x-after" class="row after clip" style="top:590px" data-start="2.6" data-duration="{T - 2.6}" data-track-index="3">'
        '<div class="lab">After fine-tuning (StowLM Reader)</div><div class="num">2 of 200</div>'
        '<div class="track"><div class="bar"></div></div></div>',
        f'<div id="x-cav" class="cav clip" data-start="3.7" data-duration="{T - 3.7}" data-track-index="4">'
        "<p>Almost all were the output format, not ship facts; none was an illegal bay or tier.</p>"
        "<p>Both of the 2 were sentences it should have refused; what's left is legal and still wrong, which is why a person confirms.</p></div>",
    ]
    write(out, "StowLM x-ray explainer", 1920, 1080, T, XRAY_CSS, els,
          {"cut": "xray", "source": "docs/headline.md (D267 mask wording); experiments/mvp/pipeline/xray/fixtures/"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cut", choices=["social", "readme-hero", "xray"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cfg, out = load(), contained_out(a.out)
    {"social": build_social, "readme-hero": build_hero, "xray": build_xray}[a.cut](cfg, out)


if __name__ == "__main__":
    main()

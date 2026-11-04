"""Generate a HyperFrames composition from shots.json.

  build_comp.py demo   --out DIR [--vo DIR] [--scratch DIR] [--cam DIR]
  build_comp.py launch --out DIR

Writes DIR/composition/index.html (+ assets/), DIR/subtitles.srt (demo) and
DIR/timing.json. Stdlib only. Animation is CSS keyframes keyed off each clip's
data-start, so there is no GSAP and no CDN.
"""
import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
ROOT = KIT.parents[3]  # repo root
MIN_RATE = 0.8
VOICE_PAD = 0.6


from safe_path import VIDEO, safe_dest, safe_mkdir, write_text  # noqa: E402  (write fence, final-code P2-3)


def contained_out(arg: str) -> Path:
    """--out: a folder strictly inside experiments/mvp/video/, no symlink on the way (code12 P1, P2-3)."""
    return safe_dest(arg)


def load():
    return json.loads((KIT / "shots.json").read_text())


def audio_seconds(p: Path) -> float:
    try:
        with wave.open(str(p)) as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        out = subprocess.run(["afinfo", str(p)], capture_output=True, text=True).stdout
        m = re.search(r"estimated duration: ([\d.]+)", out)
        if not m:
            raise SystemExit(f"can't read duration of {p}")
        return float(m.group(1))


def shot_proxy(src: Path, dst_dir: Path) -> str:
    """Constant-30-fps H.264 copy of a recorded shot. Playwright's webm has no duration and a variable
    frame rate, which HyperFrames rejects (VIDEO_SOURCE_UNRENDERABLE). Re-made whenever the source changes."""
    if not src.exists():
        raise SystemExit(f"missing input: {src}")
    dst = safe_dest(dst_dir / (src.stem + ".mp4"))
    stamp = safe_dest(dst.with_suffix(".src"))
    key = f"{src.stat().st_size} {src.stat().st_mtime_ns}"
    if not dst.exists() or not stamp.exists() or stamp.read_text() != key:
        safe_mkdir(dst_dir)
        safe_dest(dst)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-an", "-vf", "fps=30",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(dst)], check=True)
        write_text(stamp, key)
    return dst.name


def take_bounds(proxy: Path) -> tuple[float, float]:
    """(end of the black head, start of the black tail) of a recorded take, in take seconds. The stage director
    fades every take in and out; cutting those fades removes the black dips at shot boundaries (film-judge edit 3)."""
    stamp = safe_dest(proxy.with_suffix(".black.json"))
    key = f"{proxy.stat().st_size} {proxy.stat().st_mtime_ns}"
    if stamp.exists():
        c = json.loads(stamp.read_text())
        if c.get("key") == key:
            return c["head"], c["tail"]
    dur = video_seconds(proxy) or 0.0
    err = subprocess.run(["ffmpeg", "-i", str(proxy), "-vf", "blackdetect=d=0.05:pix_th=0.08:pic_th=0.97", "-an", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    spans = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)]
    head = next((b for a, b in spans if a < 0.05), 0.0)
    tail = next((a for a, b in reversed(spans) if b > dur - 0.1), dur)
    write_text(stamp, json.dumps({"key": key, "head": head, "tail": tail}))
    return head, tail


def plan_segments(s, cfg, assets, shots_dir):
    """The pieces of take footage a shot plays, in order: {pn, ms, me, rate, zoom, chips, big}.
    Default: the shot's own take with its black head and tail cut (keep_head keeps the fade, e.g. into a card)."""
    files = {x["id"]: x["file"] for x in cfg["shots"]}
    out = []
    for seg in s.get("segments") or [{}]:
        src = shots_dir / files[seg.get("shot", s["id"])]
        pn = shot_proxy(src, assets / "shots")
        head, tail = take_bounds(assets / "shots" / pn)
        if "ms" in seg:
            ms, me = float(seg["ms"]), float(seg["me"])
        else:
            # the director's fade runs about 0.3 s inside the black at each end; cut past it
            ms = 0.0 if s.get("keep_head") else head + 0.3
            me = min(ms + float(s["dur"]), tail - 0.35)
        out.append({"pn": pn, "ms": round(ms, 3), "me": round(me, 3), "rate": float(seg.get("rate", 1.0)),
                    "zoom": seg.get("zoom"), "chips": seg.get("chips"), "big": seg.get("big")})
    return out


def video_seconds(p: Path) -> float | None:
    if shutil.which("ffprobe"):
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                             capture_output=True, text=True).stdout.strip()
        try:
            return float(out)
        except ValueError:
            pass
    out = subprocess.run(["mdls", "-raw", "-name", "kMDItemDurationSeconds", str(p)],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return None


def copy(src: Path, dst: Path):
    if not src.exists():
        raise SystemExit(f"missing input: {src}")
    dst = safe_dest(dst)
    safe_mkdir(dst.parent)
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime or dst.stat().st_size != src.stat().st_size:
        shutil.copy2(src, safe_dest(dst))


def esc(s):
    return html.escape(s, quote=True)


def sentences(text):
    parts = re.split(r"(?<=[.!?;:])\s+", text.strip())
    out = []
    for p in parts:  # keep captions to two short lines
        words = p.split()
        while len(words) > 14:
            cut = len(words) // 2 if len(words) < 28 else 12
            out.append(" ".join(words[:cut]))
            words = words[cut:]
        if words:
            out.append(" ".join(words))
    return out


def caption_cues(text, start, dur):
    segs = sentences(text)
    weights = [len(s.split()) + 1 for s in segs]
    total = sum(weights)
    t, cues = start, []
    for s, w in zip(segs, weights):
        d = dur * w / total
        cues.append((round(t, 3), round(d, 3), s))
        t += d
    return cues


def srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


CSS = """
:root{--ink:#f4f6fb;--muted:#c9d2e3;--panel:rgba(6,12,26,.86);--line:rgba(160,180,215,.35);--amber:#ffb547;--green:#4be0a0;--chip:#0b1630}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1920px;height:1080px;overflow:hidden;background:#050a16}
#root{position:relative;width:100%;height:100%;font-family:Inter,sans-serif;color:var(--ink);background:#050a16}
.shot{position:absolute;left:0;top:0;width:1600px;height:900px;object-fit:contain;z-index:1}
.rail{position:absolute;left:1600px;top:0;width:320px;height:1080px;background:#081022;border-left:2px solid rgba(75,224,160,.35);z-index:2}
.band{position:absolute;left:0;top:900px;width:1600px;height:180px;background:#050a16;border-top:2px solid rgba(75,224,160,.35);z-index:2}
.mark{position:absolute;left:1624px;top:26px;font-size:40px;font-weight:800;color:var(--green);z-index:5}
.chips{position:absolute;top:96px;left:1624px;width:276px;display:flex;flex-direction:column;align-items:flex-start;gap:10px;z-index:5}
.chip{display:block;padding:7px 14px;border-radius:14px;background:var(--chip);border:1.5px solid var(--line);font-size:24px;line-height:1.25;font-weight:600;color:var(--ink);animation:pop .45s ease-out both}
.chip.held{border-color:var(--amber);color:#ffd9a0}
.chip.proj{border-color:var(--amber);color:#ffd9a0;font-size:32px}
.lt{position:absolute;left:1624px;top:640px;width:276px;padding:14px 16px;background:var(--panel);border-left:5px solid var(--green);border-radius:6px;z-index:5;animation:slide .5s ease-out both}
.lt b{display:block;font-size:28px;font-weight:700;color:var(--ink)}
.lt span{display:block;font-size:22px;line-height:1.3;color:var(--muted);margin-top:4px}
.cap{position:absolute;left:40px;width:1520px;top:912px;height:156px;display:flex;align-items:center;justify-content:center;text-align:center;z-index:6;animation:fade .25s ease-out both}
.cap span{font-size:40px;line-height:1.3;font-weight:600;color:#fff}
.cap.withlt{left:40px;width:1520px}
.scratch{position:absolute;left:1624px;top:1000px;width:276px;padding:6px 10px;border:2px solid #ff5a5a;color:#ff8a8a;background:rgba(40,0,0,.85);font:700 20px 'JetBrains Mono',monospace;letter-spacing:.06em;z-index:9}
.cam{position:absolute;left:1140px;top:420px;width:420px;height:420px;border-radius:50%;object-fit:cover;border:5px solid var(--green);z-index:7}
.name{position:absolute;left:1140px;top:850px;width:420px;text-align:center;z-index:7;font-size:28px;font-weight:700;color:#fff;background:rgba(0,0,0,.78);padding:6px 10px;border-radius:6px}
.card{position:absolute;inset:0;background:radial-gradient(ellipse at 50% 35%,#0f2347 0%,#050a16 70%);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:0 160px;text-align:center;z-index:8}
.card h1{font-size:96px;font-weight:800;letter-spacing:-.02em;animation:rise .6s ease-out both}
.card p{font-size:30px;color:var(--muted);max-width:1600px;line-height:1.35;animation:fade .6s ease-out both}
.card p.lead{font-size:40px;color:var(--ink);font-weight:600}
.card p.claim{font-size:38px;color:#ffd9a0;font-weight:700}
.card p.small{font-size:28px}
.patch{position:absolute;z-index:3;background:#05070f;color:#8fa1bf;font:600 15px 'JetBrains Mono',monospace;display:flex;align-items:center;padding:0 6px;white-space:nowrap}
.bigword{position:absolute;left:40px;width:1520px;top:912px;height:156px;display:flex;align-items:center;justify-content:center;text-align:center;z-index:8;animation:rise .3s ease-out both}
.bigword span{font-size:76px;line-height:1.05;font-weight:900;letter-spacing:.01em;color:#fff}
.card p.url{font-size:40px;color:var(--green);font-weight:800}
.big{position:absolute;left:40px;width:1520px;top:912px;height:156px;display:flex;align-items:center;justify-content:center;text-align:center;z-index:6;animation:rise .4s ease-out both}
.big span{font-size:50px;line-height:1.2;font-weight:800;color:#fff}
@keyframes pop{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:none}}
@keyframes slide{from{opacity:0;transform:translateX(-30px)}to{opacity:1;transform:none}}
@keyframes fade{from{opacity:0}to{opacity:1}}
@keyframes rise{from{opacity:0;transform:translateY(24px)}to{opacity:1;transform:none}}
"""


def chip_windows(s, slot, rate, ms=0.0):
    """(start, end, chips) in slot seconds. A shot whose take changes label mid-shot lists chip_beats in take
    seconds; each beat's chips show only while the take shows that beat (final-logic S21)."""
    beats = s.get("chip_beats")
    if not beats:
        return [(0.2, slot, s["chips"])]
    out = []
    for i, b in enumerate(beats):
        # beats are in take seconds; the slot starts at the take's media start ms
        a0 = 0.2 if i == 0 else max(0.0, (b["from"] - ms) / rate)
        a1 = slot if i == len(beats) - 1 else min(slot, max(0.0, (b["to"] - ms) / rate))
        if a1 > a0:
            out.append((round(a0, 3), round(a1, 3), b["chips"]))
    return out


def chip_class(text):
    if text == "Projection":
        return "chip proj"
    if "held-out" in text.lower() or "frozen pipeline" in text.lower() or "third attempt" in text.lower():
        return "chip held"
    return "chip"


def duck_lane(windows, clip_start, clip_dur, bed, duck):
    """Volume points (clip-local seconds) that dip under every voice window."""
    pts = [(0.0, bed)]
    for a, b in windows:
        a, b = a - clip_start, b - clip_start
        if b <= 0 or a >= clip_dur:
            continue
        a0, b1 = max(0.0, a - 0.35), min(clip_dur, b + 0.5)
        pts += [(a0, bed), (max(a0, a), duck), (max(a, b), duck), (b1, bed)]
    pts.append((clip_dur, bed))
    pts.sort()
    clean = []
    for t, v in pts:
        if clean and abs(clean[-1][0] - t) < 1e-3:
            clean[-1] = (t, min(clean[-1][1], v))
        else:
            clean.append((t, v))
    return {"version": 1, "lanes": [{"target": "volume", "points": [{"t": round(t, 3), "v": v} for t, v in clean[:500]]}]}


def frame(end):
    """Rail and caption band behind the overlays, so nothing we add covers the recording."""
    return (f'<div id="frame-rail" class="rail clip" data-start="0" data-duration="{end:.3f}" data-track-index="1"></div>\n'
            f'<div id="frame-band" class="band clip" data-start="0" data-duration="{end:.3f}" data-track-index="1"></div>\n'
            f'<div id="frame-mark" class="mark clip" data-start="0" data-duration="{end:.3f}" data-track-index="5">StowLM</div>')


def page(title, total, body, scratch):
    mark = f'<div id="scratch-mark" class="scratch clip" data-start="0" data-duration="{total:.3f}" data-track-index="9">SCRATCH VOICE · NOT FOR RELEASE</div>' if scratch else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=1920, height=1080" />
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div id="root" data-composition-id="main" data-no-timeline data-start="0" data-duration="{total:.3f}" data-width="1920" data-height="1080">
{body}
{mark}
</div>
</body>
</html>
"""


def build_demo(cfg, out: Path, vo_dir: Path, scratch_dir: Path, cam_dir: Path):
    comp = out / "composition"
    assets = comp / "assets"
    shots_dir = ROOT / cfg["shots_dir"]
    presenter = {}
    pj = vo_dir.parent / "presenter.json"
    if pj.exists():
        presenter = json.loads(pj.read_text())
    els, cues, voice_windows, report, radio_cues = [], [], [], [], []
    t = 0.0
    any_scratch = False
    z = 0

    def el(s):
        els.append(s)

    for s in cfg["shots"]:
        n = s["id"]
        nn = f"{n:02}"
        vid_src = shots_dir / s["file"]
        segs = plan_segments(s, cfg, assets, shots_dir)
        vdur = sum((g["me"] - g["ms"]) / g["rate"] for g in segs)
        cam = None
        if s.get("face_cam") == "intro" and (cam_dir / "intro.mov").exists():
            cam = cam_dir / "intro.mov"
        voice_at = float(s.get("voice_at_s", 0.3))
        vo = vo_dir / f"shot-{nn}.wav"
        src_kind = "user"
        if cam:
            vo_len = audio_seconds(cam) if cam.suffix == ".wav" else (video_seconds(cam) or 12.0)
            src_kind = "face-cam"
        elif vo.exists():
            vo_len = audio_seconds(vo)
        else:
            vo = scratch_dir / f"scratch-{nn}.wav"
            if not vo.exists():
                raise SystemExit(f"no voice for shot {n}: record {vo_dir}/shot-{nn}.wav or run make_scratch.sh")
            vo_len = audio_seconds(vo)
            src_kind = "scratch"
            any_scratch = True
        need = voice_at + vo_len + VOICE_PAD
        slot = max(vdur, need)
        rate = vdur / slot
        over = 0.0
        if rate < MIN_RATE:
            rate = MIN_RATE
            over = need - vdur / MIN_RATE
            slot = vdur / MIN_RATE  # the voice spills into the next shot; reported
        if s.get("no_slow"):  # footage carries its own speed label (e.g. "sped up 3×"): never change its rate
            rate, slot, over = 1.0, vdur, max(0.0, need - vdur)
        slot = round(slot, 3)
        rate = round(rate, 4)
        acc = 0.0
        windows = []
        for k, g in enumerate(segs):
            r = g["rate"] * rate
            d = (g["me"] - g["ms"]) / r
            if k == len(segs) - 1:
                d = slot - acc  # rounding: the last piece ends the slot
            style = ""
            if g["zoom"]:
                zs, ox, oy = g["zoom"]["scale"], g["zoom"]["origin"][0], g["zoom"]["origin"][1]
                style = f' style="transform:scale({zs});transform-origin:{ox}px {oy}px"'
            el(f'<video id="shot-{nn}-{k}" class="shot clip" muted playsinline src="assets/shots/{g["pn"]}"{style} '
               f'data-start="{t + acc:.3f}" data-duration="{d:.3f}" data-media-start="{g["ms"]}" '
               f'data-playback-rate="{round(r, 4)}" data-track-index="0"></video>')
            if g["big"]:
                el(f'<div id="big-{nn}-{k}" class="bigword clip" data-start="{t + acc:.3f}" data-duration="{d:.3f}" data-track-index="8">'
                   f'<span>{esc(g["big"])}</span></div>')
            windows.append((acc, acc + d, g["chips"]))
            acc += d
        v_start = t + voice_at
        if cam:
            copy(cam, assets / "cam" / cam.name)
            el(f'<video id="cam-intro" class="cam clip" playsinline data-has-audio="true" data-volume="1" src="assets/cam/{cam.name}" '
               f'data-start="{v_start:.3f}" data-duration="{vo_len:.3f}" data-track-index="7"></video>')
            if presenter.get("name"):
                el(f'<div id="cam-intro-name" class="name clip" data-start="{v_start:.3f}" data-duration="{vo_len:.3f}" data-track-index="7">'
                   f'{esc(presenter["name"])}</div>')
        else:
            rel = f"assets/vo/{vo.name}"
            copy(vo, comp / rel)
            el(f'<audio id="vo-{nn}" data-start="{v_start:.3f}" data-duration="{vo_len:.3f}" data-volume="1" '
               f'data-track-index="3" src="{rel}"></audio>')
        voice_windows.append((v_start, v_start + vo_len))
        for use in cfg["radio_clip"]["uses"]:
            if use["shot"] != n:
                continue
            rc = ROOT / cfg["radio_clip"]["path"]
            copy(rc, assets / "radio" / rc.name)
            rl = audio_seconds(rc)
            ra = t + use["at_s"]
            el(f'<audio id="radio-clip-{nn}" data-start="{ra:.3f}" data-duration="{rl:.3f}" data-volume="1" '
               f'data-track-index="4" src="assets/radio/{rc.name}"></audio>')
            voice_windows.append((ra, ra + rl))
            radio_cues.append((round(ra, 3), round(min(rl, 2.6), 3), "[Radio, generated voice] Nothing goes into bay nine on this call."))
        seg_chips = [(a0, a1, cs) for a0, a1, cs in windows if cs]
        for k, (a0, a1, cs) in enumerate(seg_chips or chip_windows(s, slot, rate, segs[0]["ms"])):
            chips = "".join(f'<span class="{chip_class(c)}">{esc(c)}</span>' for c in cs)
            el(f'<div id="chips-{nn}-{k}" class="chips clip" data-start="{t + a0:.3f}" data-duration="{a1 - a0:.3f}" data-track-index="5">{chips}</div>')
        for k, pt in enumerate(s.get("patches", [])):  # covers a tag baked into the take with the correct wording
            el(f'<div id="patch-{nn}-{k}" class="patch clip" style="left:{pt["x"]}px;top:{pt["y"]}px;width:{pt["w"]}px;height:{pt["h"]}px" '
               f'data-start="{t:.3f}" data-duration="{slot:.3f}" data-track-index="5" data-layout-allow-overflow>{esc(pt["text"])}</div>')
        lt = s.get("lower_third")
        if lt:
            el(f'<div id="lt-{nn}" class="lt clip" data-start="{t + 0.6:.3f}" data-duration="{min(5.5, slot - 0.8):.3f}" data-track-index="6">'
               f'<b>{esc(lt["name"])}</b><span>{esc(lt["role"])}</span></div>')
        text = s["voice"] if not cam else presenter.get("intro_text", "")
        if text:
            for i, (a, d, line) in enumerate(caption_cues(text, v_start, vo_len)):
                withlt = lt and a < t + 0.6 + min(5.5, slot - 0.8)
                cls = "cap clip withlt" if withlt else "cap clip"
                if not s.get("big_captions"):
                    el(f'<div id="cap-{nn}-{i}" class="{cls}" data-start="{a:.3f}" data-duration="{d:.3f}" data-track-index="8"><span>{esc(line)}</span></div>')
                cues.append((a, d, line))
        report.append({"shot": n, "title": s["title"], "video_s": round(vdur, 3), "voice": src_kind,
                       "voice_s": round(vo_len, 3), "slot_s": slot, "playback_rate": rate, "pieces": len(segs), "media_start": segs[0]["ms"],
                       "over_s": round(over, 2), "start_s": round(t, 3)})
        t += slot

    els[0:0] = frame(t).split("\n")
    # credits card, optionally with the face-cam outro
    cr = cfg["credits"]
    cdur = float(cr["dur"])
    outro = cam_dir / "outro.mov"
    narration = "scratch macOS voice (timing only)" if any_scratch else presenter.get("narration_credit", "the builder, own voice")
    if outro.exists():
        olen = video_seconds(outro) or 12.0
        cdur = max(cdur, olen + 0.6)
        copy(outro, assets / "cam" / "outro.mov")
        el(f'<video id="cam-outro" class="cam clip" playsinline data-has-audio="true" data-volume="1" src="assets/cam/outro.mov" '
           f'data-start="{t + 0.3:.3f}" data-duration="{olen:.3f}" data-track-index="7"></video>')
        voice_windows.append((t + 0.3, t + 0.3 + olen))
    lines = [(c, x.replace("{narration}", narration)) for c, x in cr["lines"]]
    ps = []
    for c, x in lines:
        if c == "h1":
            ps.append(f"<h1>{esc(x)}</h1>")
        else:
            ps.append(f'<p class="{c}">{esc(x)}</p>')
    over = float(cr.get("overlay_s", 0.0))
    c0 = max(0.0, t - over)
    last = report[-1]
    if cfg["shots"][-1].get("end_card_at_take_s") is not None:  # the take's own end card starts here: ours replaces it
        c0 = last["start_s"] + (float(cfg["shots"][-1]["end_card_at_take_s"]) - last["media_start"]) / last["playback_rate"]
    for i, e in enumerate(els):  # rail, mark and the last shot's chips end where the end card starts
        m = re.search(r'id="(frame-[a-z]+|chips-%02d-\d+)"[^>]*data-start="([\d.]+)" data-duration="([\d.]+)"' % cfg["shots"][-1]["id"], e)
        if m and float(m.group(2)) + float(m.group(3)) > c0:
            els[i] = e.replace(f'data-duration="{m.group(3)}"', f'data-duration="{max(0.05, c0 - float(m.group(2))):.3f}"', 1)
    el(f'<div id="credits" class="card clip" data-start="{c0:.3f}" data-duration="{t - c0 + cdur:.3f}" data-track-index="4">{"".join(ps)}</div>')
    report.append({"shot": "credits", "slot_s": round(t - c0 + cdur, 3), "start_s": round(c0, 3), "overlay_s": over})
    t += cdur
    total = round(t, 3)

    # music: vol-1 then vol-10 under the tail, ducked under every voice window
    m = cfg["music"]
    mdir = ROOT / m["dir"]
    a, b = m["demo"]
    copy(mdir / a, assets / "music" / a)
    copy(mdir / b, assets / "music" / b)
    alen = audio_seconds(mdir / a)
    a_dur = round(min(alen, total), 3)
    lane = duck_lane(voice_windows, 0.0, a_dur, m["bed_volume"], m["duck_volume"])
    el(f'<audio id="music-a" data-start="0" data-duration="{a_dur:.3f}" data-volume="1" data-fade-in="1.5" data-fade-out="3" '
       f'data-track-index="2" data-automation="{esc(json.dumps(lane))}" src="assets/music/{a}"></audio>')
    if total > a_dur - 0.5:
        b_start = max(0.0, a_dur - 4.0)
        b_dur = round(total - b_start, 3)
        lane_b = duck_lane(voice_windows, b_start, b_dur, m["bed_volume"], m["duck_volume"])
        el(f'<audio id="music-b" data-start="{b_start:.3f}" data-duration="{b_dur:.3f}" data-volume="1" data-fade-in="3" data-fade-out="2.5" '
           f'data-track-index="1" data-automation="{esc(json.dumps(lane_b))}" src="assets/music/{b}"></audio>')

    write_text(comp / "index.html", page("StowLM demo", total, "\n".join(els), any_scratch))
    cues = sorted(cues + radio_cues)
    fixed = []
    for i, (a, d, line) in enumerate(cues):  # no overlaps: each cue ends before the next starts
        b = a + d
        if i + 1 < len(cues):
            b = min(b, cues[i + 1][0] - 0.001)
        fixed.append((a, b - a, line))
    srt = "\n".join(f"{i}\n{srt_time(a)} --> {srt_time(a + d)}\n{line}\n" for i, (a, d, line) in enumerate(fixed, 1))
    write_text(out / "subtitles.srt", srt)
    info = {"cut": "demo", "total_s": total, "cap_s": cfg["cap_s"], "within_cap": total <= cfg["cap_s"],
            "scratch_voice": any_scratch, "shots": report}
    write_text(out / "timing.json", json.dumps(info, indent=2) + "\n")
    return info


def launch_segments(cfg, assets, shots_dir):
    """The launch segments with their proxies: shot, ms, me, rate, caption, chips, radio, crop."""
    files = {x["id"]: x["file"] for x in cfg["shots"]}
    out = []
    for seg in cfg["launch"]["segments"]:
        pn = shot_proxy(shots_dir / files[seg["shot"]], assets / "shots")
        g = dict(seg, pn=pn, rate=float(seg.get("rate", 1.0)))
        g["dur"] = round((float(seg["me"]) - float(seg["ms"])) / g["rate"], 3)
        out.append(g)
    return out


def build_launch(cfg, out: Path):
    comp = out / "composition"
    assets = comp / "assets"
    shots_dir = ROOT / cfg["shots_dir"]
    els, t, report, windows = [], 0.0, [], []
    for k, g in enumerate(launch_segments(cfg, assets, shots_dir)):
        d = g["dur"]
        els.append(f'<video id="l-seg-{k}" class="shot clip" muted playsinline src="assets/shots/{g["pn"]}" '
                   f'data-start="{t:.3f}" data-duration="{d:.3f}" data-media-start="{g["ms"]}" '
                   f'data-playback-rate="{g["rate"]}" data-track-index="0"></video>')
        chips = "".join(f'<span class="{chip_class(c)}">{esc(c)}</span>' for c in g["chips"])
        els.append(f'<div id="l-chip-{k}" class="chips clip" data-start="{t + 0.1:.3f}" data-duration="{d - 0.1:.3f}" data-track-index="5">{chips}</div>')
        els.append(f'<div id="l-cap-{k}" class="big clip" data-start="{t + 0.05:.3f}" data-duration="{d - 0.05:.3f}" data-track-index="6">'
                   f'<span>{esc(g["caption"])}</span></div>')
        if g.get("radio"):
            rc = ROOT / cfg["radio_clip"]["path"]
            copy(rc, assets / "radio" / rc.name)
            rl = audio_seconds(rc)
            els.append(f'<audio id="l-radio-{k}" data-start="{t:.3f}" data-duration="{rl:.3f}" data-volume="1" '
                       f'data-track-index="4" src="assets/radio/{rc.name}"></audio>')
            windows.append((t, t + rl))
        report.append({"shot": g["shot"], "start_s": round(t, 3), "dur_s": d, "rate": g["rate"]})
        t += d
    els.insert(0, frame(t))
    ec = cfg["launch"]["end_card"]
    ps = []
    for c, x in ec["lines"]:
        ps.append(f"<h1>{esc(x)}</h1>" if c == "h1" else f'<p class="{c}">{esc(x)}</p>')
    els.append(f'<div id="l-end" class="card clip" data-start="{t:.3f}" data-duration="{ec["dur"]:.3f}" data-track-index="4">{"".join(ps)}</div>')
    t += float(ec["dur"])
    total = round(t, 3)
    m = cfg["music"]
    track = m["launch"]
    copy(ROOT / m["dir"] / track, assets / "music" / track)
    lane = duck_lane(windows, 0.0, total, 0.8, 0.3)
    els.append(f'<audio id="l-music" data-start="0" data-duration="{total:.3f}" data-volume="1" data-fade-in="0.4" data-fade-out="2" '
               f'data-track-index="2" data-automation="{esc(json.dumps(lane))}" src="assets/music/{track}"></audio>')
    write_text(comp / "index.html", page("StowLM launch", total, "\n".join(els), False))
    info = {"cut": "launch", "total_s": total, "segments": report}
    write_text(out / "timing.json", json.dumps(info, indent=2) + "\n")
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cut", choices=["demo", "launch"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--vo", default=str(ROOT / "experiments/mvp/video/record-kit/vo"))
    ap.add_argument("--cam", default=str(ROOT / "experiments/mvp/video/record-kit/cam"))
    ap.add_argument("--scratch", default=str(ROOT / "experiments/mvp/video/demo-cut/scratch"))
    a = ap.parse_args()
    cfg = load()
    out = contained_out(a.out)
    if a.cut == "demo":
        info = build_demo(cfg, out, Path(a.vo), Path(a.scratch), Path(a.cam))
        for r in info["shots"]:
            if r.get("over_s"):
                print(f"shot {r['shot']}: voice runs {r['over_s']} s past the slowest allowed shot; use its trim note", file=sys.stderr)
        print(f"demo: {info['total_s']} s (cap {info['cap_s']}), scratch voice: {info['scratch_voice']}")
        if not info["within_cap"]:
            print("OVER THE 3:00 CAP", file=sys.stderr)
            sys.exit(2)
    else:
        info = build_launch(cfg, out)
        print(f"launch: {info['total_s']} s")


if __name__ == "__main__":
    main()

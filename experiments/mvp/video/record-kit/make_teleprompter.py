"""Build the voice-over teleprompter and RECORD.md from shots.json.

Stdlib only. Run from anywhere:
    uv run --no-project python experiments/mvp/video/record-kit/make_teleprompter.py

Reads ../stowlm-brag/shots.json (relative to this file) and writes, next to this file:
    teleprompter.html  (self-contained, works offline from file://)
    RECORD.md          (recording steps with the per-shot table)
"""

from __future__ import annotations

import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHOTS = HERE.parent / "stowlm-brag" / "shots.json"
PAD_S = 0.5  # silence the build keeps after the line, inside the shot
# footage length of each shot in the current demo cut (black fades trimmed, recuts applied), from the last build
try:
    import json as _json
    CUT_VIDEO = {r["shot"]: r["video_s"] for r in _json.loads((Path(__file__).resolve().parents[1] / "demo-cut/out/timing.json").read_text())["shots"]
                 if isinstance(r["shot"], int)}
except (OSError, ValueError, KeyError):
    CUT_VIDEO = {}

# Suggestions only: the user rewrites these in their own words.
# Kept to headline.md: no "industry level", no numbers beyond shots.json and the build window.
INTRO_LINE = (
    "I'm [your first name]. My family works in Thai freight forwarding. "
    "At Laem Chabang, Thailand's main port, every ship needs a plan: "
    "which box goes where, safely."
)
OUTRO_LINE = (
    "I built StowLM between 26 October and 4 November 2026. "
    "Search software plans, our fine-tuned Muse Glimmer reads the instructions, "
    "and the checker decides. Drag a box and try to break it."
)
HERO_LINE = "Nothing goes into bay nine on this call."


def words(text: str) -> int:
    return len(text.split())


def build_screens(data: dict) -> list[dict]:
    wps = float(data.get("words_per_s", 2.4))
    screens: list[dict] = []
    for s in data["shots"]:
        at = float(s.get("voice_at_s", 0) or 0)
        video = CUT_VIDEO.get(s["id"], s["dur"])
        target = round(video - at - PAD_S, 1)
        n = words(s["voice"])
        hints = []
        if s.get("trim_note"):
            hints.append(s["trim_note"])
        if s.get("conditional"):
            hints.append("Conditional: " + s["conditional"])
        notes = []
        if at:
            notes.append(f"Voice starts {at:g} s into the shot (after the radio clip). Record only your line.")
        if s.get("face_cam") == "intro":
            notes.append("If you record the face-cam intro (cam/intro.mov), its audio replaces this line. Record this anyway as a fallback.")
        if s.get("face_cam") == "outro":
            notes.append("Optional face-cam outro plays later, over the credits; this line is still needed.")
        screens.append({
            "kind": "shot",
            "label": f"Shot {s['id']:02d} of {len(data['shots'])}",
            "title": s["title"],
            "file": f"vo/shot-{s['id']:02d}.wav",
            "line": s["voice"],
            "words": n,
            "est": round(n / wps, 1),
            "target": target,
            "video": round(video, 1),
            "delivery": s.get("delivery", ""),
            "hints": hints,
            "notes": notes,
        })
    return screens


def extra_screens(data: dict) -> list[dict]:
    wps = float(data.get("words_per_s", 2.4))
    return [
        {
            "kind": "extra",
            "label": "Optional · face-cam intro",
            "title": "Intro on camera (plays over shot 2)",
            "file": "cam/intro.mov",
            "line": INTRO_LINE,
            "words": words(INTRO_LINE),
            "est": round(words(INTRO_LINE) / wps, 1),
            "target": 11.5,
            "video": 12,
            "delivery": "Look into the camera, not the screen. Warm, like telling a friend.",
            "hints": [],
            "notes": [
                "A suggestion: rewrite it in your own words. Its audio replaces shot 2's line, so cover the same ground: who you are, the Thai freight-forwarding family, why the plan matters at Laem Chabang.",
                "Say only what you're happy to publish: no addresses, employer or customer names.",
            ],
        },
        {
            "kind": "extra",
            "label": "Optional · face-cam outro",
            "title": "Outro on camera (plays over the credits)",
            "file": "cam/outro.mov",
            "line": OUTRO_LINE,
            "words": words(OUTRO_LINE),
            "est": round(words(OUTRO_LINE) / wps, 1),
            "target": 14.5,
            "video": 15,
            "delivery": "Relaxed, a small smile on the dare. 10 to 15 s is fine.",
            "hints": [],
            "notes": [
                "A suggestion: rewrite it, but keep it honest (no 'industry level', no new numbers).",
            ],
        },
        {
            "kind": "extra",
            "label": "Optional · hero sentence",
            "title": "The radio sentence in your voice",
            "file": "vo/hero-sentence.wav",
            "line": HERO_LINE,
            "words": words(HERO_LINE),
            "est": round(words(HERO_LINE) / wps, 1),
            "target": 3.0,
            "video": 3.5,
            "delivery": "Like a planner on a bad line: clear, a little clipped.",
            "hints": [],
            "notes": [
                "Caveat: this can NOT replace the generated radio clip yet. It needs a fresh Muse Voice transcription and a reader read first (user item 134), which this kit doesn't do. Record it now only so it's ready.",
            ],
        },
    ]


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StowLM Teleprompter</title>
<style>
:root{--bg:#101214;--fg:#f4f4f2;--muted:#b9bcc2;--card:#1c1f23;--accent:#7cc4ff;--ok:#3ecf6e;--amber:#ffb020;--red:#ff5a4f;--fs:64px}
body.light{--bg:#fbfaf7;--fg:#111;--muted:#444a52;--card:#ecebe6;--accent:#0b5cad;--ok:#1f8a44;--amber:#a86400;--red:#c0271c}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--fg);font-family:-apple-system,system-ui,"Segoe UI",Helvetica,Arial,sans-serif;display:flex;flex-direction:column}
main{flex:1;display:flex;flex-direction:column;padding:24px 4vw 12px;overflow:auto}
main.mirror{transform:scaleX(-1)}
.top{display:flex;flex-wrap:wrap;gap:8px 24px;align-items:baseline;font-size:22px;color:var(--muted)}
.top b{color:var(--fg)}
.file{font-family:ui-monospace,Menlo,monospace;color:var(--accent);font-size:24px}
.line{font-size:var(--fs);line-height:1.35;font-weight:600;margin:28px 0 20px;max-width:30ch}
.delivery{font-size:20px;color:var(--muted);margin:4px 0}
.hint{font-size:20px;margin:8px 0;padding:8px 12px;border-left:4px solid var(--amber);background:var(--card)}
.note{font-size:20px;margin:8px 0;padding:8px 12px;border-left:4px solid var(--accent);background:var(--card)}
.keys{font-size:24px;line-height:1.6}
.keys kbd{font-family:ui-monospace,Menlo,monospace;background:var(--card);border:1px solid var(--muted);border-radius:6px;padding:1px 8px}
footer{padding:10px 4vw 18px;background:var(--card)}
.bar{height:22px;border-radius:11px;background:var(--bg);overflow:hidden;border:1px solid var(--muted)}
.fill{height:100%;width:0;background:var(--ok)}
.fill.amber{background:var(--amber)} .fill.red{background:var(--red)}
.status{display:flex;justify-content:space-between;font-size:20px;margin-top:6px;color:var(--muted)}
.status b{color:var(--fg);font-variant-numeric:tabular-nums}
</style>
</head>
<body>
<main id="m"></main>
<footer id="f"><div class="bar"><div class="fill" id="fill"></div></div>
<div class="status"><span id="st">Space to start the timer</span><span><b id="el">0.0</b> / <b id="tg">0.0</b> s</span></div></footer>
<script>
var SCREENS = __DATA__;
var i = 0, fs = 64, mirror = false, light = false, t0 = null, acc = 0, raf = null;
try { var s = JSON.parse(localStorage.getItem("stowlm-prompter") || "{}"); if (typeof s.i === "number") i = s.i; if (typeof s.fs === "number") fs = s.fs; } catch (e) {}
var m = document.getElementById("m"), fill = document.getElementById("fill"), el = document.getElementById("el"), tg = document.getElementById("tg"), st = document.getElementById("st"), foot = document.getElementById("f");
function save(){ try { localStorage.setItem("stowlm-prompter", JSON.stringify({i:i, fs:fs})); } catch (e) {} }
function esc(x){ return String(x).replace(/[&<>"]/g, function(c){ return {"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"}[c]; }); }
function cur(){ return SCREENS[i]; }
function render(){
  if (i < 0) i = 0; if (i >= SCREENS.length) i = SCREENS.length - 1;
  document.documentElement.style.setProperty("--fs", fs + "px");
  var s = cur(), h = "";
  if (s.kind === "keys") {
    h = '<div class="top"><b>StowLM voice-over teleprompter</b><span>' + (SCREENS.length - 1) + ' screens follow</span></div>' +
      '<div class="keys" style="margin-top:28px">' + s.html + '</div>';
    foot.style.visibility = "hidden";
  } else {
    h = '<div class="top"><b>' + esc(s.label) + '</b><span>' + esc(s.title) + '</span><span>Save as <span class="file">' + esc(s.file) + '</span></span></div>' +
      '<div class="top" style="margin-top:6px"><span>Target <b>' + s.target.toFixed(1) + ' s</b> (video ' + s.video + ' s)</span><span>' + s.words + ' words &asymp; <b>' + s.est.toFixed(1) + ' s</b> at 2.4 words/s</span></div>' +
      '<p class="line">' + esc(s.line) + '</p>' +
      (s.delivery ? '<p class="delivery">Delivery: ' + esc(s.delivery) + '</p>' : '');
    s.notes.forEach(function(n){ h += '<p class="note">' + esc(n) + '</p>'; });
    s.hints.forEach(function(n){ h += '<p class="hint">If over time: ' + esc(n) + '</p>'; });
    foot.style.visibility = "visible";
  }
  m.innerHTML = h; m.className = mirror ? "mirror" : ""; document.body.className = light ? "light" : "";
  resetTimer(); save();
}
function elapsed(){ return acc + (t0 === null ? 0 : (performance.now() - t0) / 1000); }
function tick(){
  var s = cur(); if (!s.target) return;
  var e = elapsed(), p = e / s.target;
  fill.style.width = Math.min(p, 1) * 100 + "%";
  fill.className = "fill" + (p > 1 ? " red" : p > 0.9 ? " amber" : "");
  el.textContent = e.toFixed(1); tg.textContent = s.target.toFixed(1);
  st.textContent = t0 === null ? (e > 0 ? "Paused. Space to resume, R to reset" : "Space to start the timer") : (p > 1 ? "Over time" : "Recording...");
  if (t0 !== null) raf = requestAnimationFrame(tick);
}
function toggle(){ if (t0 === null) { t0 = performance.now(); tick(); } else { acc = elapsed(); t0 = null; cancelAnimationFrame(raf); tick(); } }
function resetTimer(){ t0 = null; acc = 0; cancelAnimationFrame(raf); tick(); }
document.addEventListener("keydown", function(ev){
  var k = ev.key;
  if (k === "ArrowRight" || k === "ArrowDown" || k === "PageDown" || k === "Enter") { i++; render(); }
  else if (k === "ArrowLeft" || k === "ArrowUp" || k === "PageUp") { i--; render(); }
  else if (k === " ") { toggle(); }
  else if (k === "r" || k === "R") { resetTimer(); }
  else if (k === "+" || k === "=") { fs = Math.min(fs + 6, 160); render(); }
  else if (k === "-" || k === "_") { fs = Math.max(fs - 6, 24); render(); }
  else if (k === "m" || k === "M") { mirror = !mirror; render(); }
  else if (k === "d" || k === "D") { light = !light; render(); }
  else return;
  ev.preventDefault();
});
render();
</script>
</body>
</html>
"""

KEYS_HTML = (
    "<p>One shot per screen. Read the big line; save the take under the file name shown.</p>"
    "<p><kbd>&rarr;</kbd> <kbd>&darr;</kbd> <kbd>PageDown</kbd> <kbd>Enter</kbd> next &nbsp; "
    "<kbd>&larr;</kbd> <kbd>&uarr;</kbd> <kbd>PageUp</kbd> back</p>"
    "<p><kbd>Space</kbd> start / stop the timer &nbsp; <kbd>R</kbd> reset the timer</p>"
    "<p><kbd>+</kbd> / <kbd>-</kbd> text size &nbsp; <kbd>M</kbd> mirror (glass prompter) &nbsp; <kbd>D</kbd> dark / light</p>"
    "<p>The bar fills over the target time: amber past 90 %, red past 100 %. "
    "Target = shot length minus any lead-in minus 0.5 s. A little over is fine: the build can slow a shot to 0.8x.</p>"
    "<p>The last three screens are optional: face-cam intro, face-cam outro, hero sentence.</p>"
    "<p>Press <kbd>&rarr;</kbd> to begin. Your place and text size are remembered.</p>"
)

RECORD_TEMPLATE = """**NARRATION LOCKED, 3 Nov 2026.** The lines below are final (plan 1.5). Don't change them without a new leader decision.

# Recording the voice-over

Generated by `make_teleprompter.py` from `experiments/mvp/video/stowlm-brag/shots.json`. Edit the script, not this file, then re-run:

```
uv run --no-project python experiments/mvp/video/record-kit/make_teleprompter.py
```

You don't need to be a professional. Clear and natural beats polished. Plan on about **20-30 minutes** including retakes (__TOTAL__ s of finished voice).

## 1. Set up (5 minutes)

1. Pick a quiet room with soft things in it (sofa, curtains, bed, rug). They soak up echo. Avoid bare kitchens and bathrooms.
2. Turn off fans, air conditioning if you can, and notifications (Focus mode).
3. The MacBook's built-in mic is fine. Sit about 20-30 cm from the laptop and keep the same position and distance for every take, so the shots sound alike.
4. Open `experiments/mvp/video/record-kit/teleprompter.html` in any browser (double-click it; it works offline). Make it full screen. The first screen lists the keys.
5. Open **QuickTime Player > File > New Audio Recording** (or any recorder you like). Click the arrow by the record button and pick the built-in mic.

## 2. Record each shot

For each screen in the teleprompter:

1. Start the recorder. Wait about half a second (leave ~0.3 s of silence at the start).
2. Press **Space** in the teleprompter and read the line. Press **Space** again when you finish. The bar shows how you did against the target: amber past 90 %, red past 100 %.
3. Wait about half a second (~0.3 s of silence at the end), then stop the recorder.
4. Save it, then convert it to 48 kHz mono WAV with the built-in macOS command (Terminal, from `experiments/mvp/video/record-kit/`):

   ```
   afconvert -f WAVE -d LEI16@48000 -c 1 in.m4a vo/shot-01.wav
   ```

   Replace `in.m4a` with your saved file and `shot-01` with the shot number. **One file per shot, two-digit names:** `vo/shot-01.wav` ... `vo/shot-12.wav`.
5. Retake as often as you like. Keep the best one by overwriting the file.

| Shot | Title | File | Words | Estimate at 2.4 words/s | Target s | Delivery |
|---:|---|---|---:|---:|---:|---|
__TABLE__
| | **Total** | | __WORDS__ | __EST__ | __TOTAL__ | |

Target = the shot's footage in the current cut minus any lead-in minus 0.5 s of pad. Shot 1 starts at about 2.6 s, when the radio voice ends (its speech stops at 2.51 s of a 2.95 s clip); it always plays at normal speed because its replan is labelled \"sped up 3×\", so its target can't be stretched. Shot 4 starts at 3.3 s.

## 3. If a line runs long

The build slows a shot's video down to **0.8x at most** to fit your take, then reports any shot that is still over time. If a shot goes red:

- Read it once more a touch faster, without rushing the numbers.
- Or use the trim note (shown as "If over time" in the teleprompter):
__TRIMS__
- Never cut the lines the delivery notes protect ("most boxes stop for a person", "It missed two stricter gates", "That's a projection, not a result").

## 4. Optional: face-cam intro and outro

Both are optional; the film works without them.

1. Open **Photo Booth** or **QuickTime Player > File > New Movie Recording**. Set quality to 1080p if offered.
2. Light your face from the front (face a window or a lamp; no bright window behind you).
3. Look at the camera, not the screen.
4. **Intro** (~12 s, plays over shot 2 and its sound **replaces** shot 2's voice line): cover the same ground: who you are, your family's Thai freight-forwarding background, why the stowage plan matters at Laem Chabang. Save as `cam/intro.mov`. Suggested (rewrite it in your words):
   > __INTRO__
5. **Outro** (10-15 s, plays over the credits): what you built in 10 days, and an invitation to try to break it. Save as `cam/outro.mov`. Suggested:
   > __OUTRO__
6. Keep it honest: no "industry level", no numbers that aren't already in the film, and "search software plans, our fine-tuned Muse Glimmer reads the instructions".

## 5. Optional: the hero sentence

You can record "__HERO__" as `vo/hero-sentence.wav`. **It can't replace the generated radio clip yet**: your recording would first need a fresh Muse Voice transcription and a reader read (user item 134), and this kit doesn't do that. The film keeps the generated, labelled clip until then.

## 6. Build the cut

```
experiments/mvp/video/demo-cut/build.sh
```

Any shot without a `vo/shot-NN.wav` falls back to a scratch voice and shows **SCRATCH** on screen, so you can record a few shots, build, watch, and come back for the rest.

## Privacy

Say only what you're happy to publish. No home address, no employer names, no customer names. "My family works in Thai freight forwarding" is enough.
"""


def main() -> None:
    data = json.loads(SHOTS.read_text(encoding="utf-8"))
    shots = build_screens(data)
    screens = [{"kind": "keys", "html": KEYS_HTML}] + shots + extra_screens(data)
    payload = json.dumps(screens, ensure_ascii=False).replace("</", "<\\/")
    (HERE / "teleprompter.html").write_text(PAGE.replace("__DATA__", payload), encoding="utf-8")

    def cell(x: str) -> str:
        return x.replace("|", "\\|")

    rows, trims = [], []
    for s, src in zip(shots, data["shots"]):
        rows.append(f"| {src['id']} | {cell(s['title'])} | `{s['file']}` | {s['words']} | {s['est']:.1f} | {s['target']:.1f} | {cell(s['delivery'])} |")
        for h in s["hints"]:
            trims.append(f"  - Shot {src['id']}: {h}")
    total = sum(s["target"] for s in shots)
    md = (RECORD_TEMPLATE.replace("__TABLE__", "\n".join(rows))
          .replace("__TRIMS__", "\n".join(trims))
          .replace("__WORDS__", str(sum(s["words"] for s in shots)))
          .replace("__EST__", f"{sum(s['est'] for s in shots):.1f}")
          .replace("__TOTAL__", f"{total:.1f}")
          .replace("__INTRO__", INTRO_LINE)
          .replace("__OUTRO__", OUTRO_LINE)
          .replace("__HERO__", HERO_LINE))
    (HERE / "RECORD.md").write_text(md, encoding="utf-8")
    print(f"wrote teleprompter.html ({len(shots)} shots) and RECORD.md; total target {total:.1f} s")


if __name__ == "__main__":
    main()

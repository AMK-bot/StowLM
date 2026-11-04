"""YouTube description with chapters from the demo cut's timing (D320 B4).

  youtube.py --out DIR      reads DIR/timing.json, writes DIR/youtube-description.txt

YouTube chapters need the first at 0:00, at least three, each 10 s or longer: shorter shots merge into the next.
Every claim line is copied from shots.json or docs/headline.md wording; check_honesty.py checks the file.
"""
import argparse
import json
from pathlib import Path

from build_comp import load
from safe_path import write_text

CHAPTER = {1: "Cold open: one sentence, the plan changes", 2: "Why stowage plans matter", 3: "Messy papers in: Muse Spark",
           4: "A bad radio line: Muse Voice Transcribe", 5: "StowLM Reader: fine-tuned Muse Glimmer 30B on the laptop",
           6: "Inside the model: the decode mask", 7: "The plan changes, and stays legal", 8: "Try to break it",
           9: "The copilot: Spark picks tools, software decides", 10: "Signed handover package", 11: "Held-out test voyages",
           12: "Forward pilot (projection)", "credits": "Credits"}


def mmss(t):
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def chapters(timing):
    rows = [(s["start_s"], s["shot"]) for s in timing["shots"]]
    out = []
    for i, (a, shot) in enumerate(rows):
        end = rows[i + 1][0] if i + 1 < len(rows) else timing["total_s"]
        if out and (a - out[-1][0] < 10 or end - a < 10) and shot != 1:
            if end - a < 10:  # too short on its own: fold into the chapter before it
                out[-1] = (out[-1][0], out[-1][1] + " · " + CHAPTER[shot])
                continue
        out.append((0.0 if not out else a, CHAPTER[shot]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    out = Path(ap.parse_args().out)
    cfg = load()
    timing = json.loads((out / "timing.json").read_text())
    ch = "\n".join(f"{mmss(a)} {name}" for a, name in chapters(timing))
    narration = "scratch macOS voice for timing (draft)" if timing.get("scratch_voice") else "the builder, own voice"
    text = f"""StowLM: messy voyage documents and a planner's own words go in, a checked plan and a handover package come out, with a fine-tuned Meta model on the laptop reading every instruction. Models propose. Software decides.

Three Meta models: Muse Glimmer 30B, fine-tuned by us as StowLM Reader, reads the planner's words on the laptop · Muse Spark (Meta Model API, cloud) reads paper and chooses tools · Muse Voice Transcribe (cloud) hears the radio. Plans come from Frontier Engine 0.13.0, our search software. Every plan shown has been through the full checker; a plan that doesn't pass says which rules it breaks.

24 of 24 held-out test voyages inside our design limit passed the full checker (held-out maps, frozen pipeline, run after two interrupted attempts; not the pre-registered sealed claim; third attempt; two earlier runs stopped on our own guard, both published). The planning step, from a clean load list. Designed for up to 195 boxes in a half-ship section of 500 to 800 TEU feeder ships, up to 70 % fill. No lift saving is quotable: the simple two-step baseline passed 0 of 24, so there are no mutual passes.

Try to break it: https://amk-bot.github.io/StowLM/replay/
Play it in your browser (recorded playback): https://amk-bot.github.io/StowLM/play/
Code: https://github.com/AMK-bot/StowLM (Apache-2.0)

Chapters
{ch}

Labels on screen: Recorded answer, Stored plan (computed on 2 Nov 2026, rechecked now), Replay of a measured run, Generated voice, Projection. The dollar figures in the last shot are a projection, not a result.
Synthetic ships and cargo, simulated port. Research demonstration on synthetic data. The Muse Glimmer usage policy prohibits use related to operating transportation technologies where harm is possible; this must not be used to plan or operate real vessels.
Built 26 Oct – 4 Nov 2026 for the Meta hackathon.
Radio voice: stock text-to-speech, generated · Narration: {narration}
Music: Sascha Ende, ende.app (CC BY 4.0)
"""
    write_text(out / "youtube-description.txt", text)
    print(f"youtube description: {len(ch.splitlines())} chapters")


if __name__ == "__main__":
    main()

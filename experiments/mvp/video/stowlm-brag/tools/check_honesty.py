"""Claims check for a cut: every number has a source, no banned wording, required labels present.

  check_honesty.py demo|launch FILE [FILE ...]

FILEs are the composition index.html, subtitles .srt and share copy. Exit 1 on any failure.
Rules come from docs/headline.md and D298/D299; numbers and sources from shots.json.
"""
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
ROOT = KIT.parents[3]

BANNED = [
    "industry level", "industry-level", "industry grade", "production ready", "production-ready",
    "real-time", "real time", "instant", "certified", "class-approved", "approved plan", "whole ship",
    "wi-fi off", "beats spark", "beats navis", "62 rules", "never guessed", "stowlm planned",
    "anyone can verify", "solves real ships", "learned physics", "fewer crane moves", "stays on the laptop",
    "the replacement", "run once", "frozen once", "planner-confirmed",
]
SEALED_OK = "not the pre-registered sealed claim"
HELD_LABEL = "frozen pipeline, run after two interrupted attempts; not the pre-registered sealed claim"
SHORT_FORM = "third attempt; two earlier runs stopped on our own guard, both published"
MUSIC = "Music: Sascha Ende, ende.app (CC BY 4.0)"
MODELS = ["Muse Glimmer 30B", "Muse Spark", "Muse Voice Transcribe"]
NUM_WORDS = {"two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
             "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
             "thousand", "million", "billion", "percent"}


def visible_text(p: Path) -> str:
    s = p.read_text()
    if p.suffix == ".srt":
        return "\n".join(ln for ln in s.splitlines() if ln.strip() and not re.match(r"^\d+$|^\d\d:\d\d", ln))
    if p.suffix in (".html", ".htm"):
        s = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", s, flags=re.S)
        s = re.sub(r"<[^>]+>", " ", s)
        return html.unescape(s)
    return s


def allowed(cfg):
    entries = []
    for s in cfg["shots"]:
        entries += s.get("numbers", [])
    entries += cfg["credits"].get("numbers", [])
    entries += cfg["launch"]["end_card"].get("numbers", [])
    errs = []
    digits, words = set(), set()
    for e in entries:
        if not (ROOT / e["source"]).exists():
            errs.append(f"number {e['text']!r}: source file missing: {e['source']}")
        digits.update(re.findall(r"\d+(?:[.,]\d+)*", e["text"]))
        words.update(w for w in re.findall(r"[a-z]+", e["text"].lower()) if w in NUM_WORDS)
    return digits, words, errs


def main():
    cut, files = sys.argv[1], [Path(f) for f in sys.argv[2:]]
    cfg = json.loads((KIT / "shots.json").read_text())
    digits, words, errs = allowed(cfg)
    texts = {}
    for f in files:
        if not f.exists():
            errs.append(f"missing file: {f}")
            continue
        texts[f] = visible_text(f)
    for f, t in texts.items():
        low = t.lower()
        # chapter and subtitle timestamps (m:ss) are positions in the video, not claims
        for n in re.findall(r"\d+(?:[.,]\d+)*", re.sub(r"(?m)^\d+:\d\d(?=\s)", "", t)):
            if n not in digits:
                errs.append(f"{f.name}: number {n!r} on screen has no source in shots.json")
        for w in re.findall(r"[a-z]+", low):
            if w in NUM_WORDS and w not in words:
                errs.append(f"{f.name}: number word {w!r} has no source in shots.json")
        for b in BANNED:
            if re.search(r"(?<![a-z])" + re.escape(b) + r"(?![a-z])", low):
                errs.append(f"{f.name}: banned wording {b!r} (docs/headline.md)")
        if "sealed" in low.replace(SEALED_OK, ""):
            errs.append(f"{f.name}: 'sealed' used outside '{SEALED_OK}'")
        if re.search(r"24 of 24", t):
            if HELD_LABEL not in low:
                errs.append(f"{f.name}: 24 of 24 without the held-out label")
            if SHORT_FORM not in low and f.suffix != ".srt":
                errs.append(f"{f.name}: 24 of 24 without the partial-run short form")
        if "billion" in low and "projection" not in low:
            errs.append(f"{f.name}: dollar figure without 'Projection'")
    screen = [t for f, t in texts.items() if f.suffix == ".html"]
    share = [t for f, t in texts.items() if f.suffix in (".txt", ".md")]
    for t in (screen + share) if cut != "explainer" else share:
        if MUSIC not in t:
            errs.append("music credit missing: " + MUSIC)
    if cut == "demo":
        for t in screen:
            for m in MODELS:
                if m not in t:
                    errs.append(f"Meta model not named on screen: {m}")
            if "Generated voice" not in t:
                errs.append("radio clip not labelled 'Generated voice'")
            if "Projection" not in t:
                errs.append("shot 12 not labelled 'Projection'")
            if "26 Oct – 4 Nov 2026" not in t:
                errs.append("new-work statement (built 26 Oct – 4 Nov 2026) missing")
    for e in dict.fromkeys(errs):
        print("FAIL", e)
    if errs:
        sys.exit(1)
    print(f"honesty check passed: {cut}, {len(texts)} files")


if __name__ == "__main__":
    main()

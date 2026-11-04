"""Tokens per boxslot action under candidate text forms, measured with the Muse Glimmer 30B tokenizer (plan 3.8).

The final serialisation is 4.5's job; this only sizes the action space. Boxes are referred to by their index in the
prompt's load list (as 4.5 will list them), slots by BAPLIE ``BBBRRTT`` fields, yard stacks by ``BLOCK-BBB-RR``.
Values are drawn from a feeder-sized geometry (bays 01-40, rows 00-12, hold tiers 02-12, deck 82-90).

Run: ``uv run python experiments/action-space/tokens.py [path/to/tokenizer.json]``
"""

from __future__ import annotations

import random
import statistics
import sys

from tokenizers import Tokenizer

FORMS = {
    # name: (place, relocate) formatters; newline = action separator
    "A  P<box><slot>":           (lambda b, s: f"P{b:03d}{s}\n", lambda b, k: f"R{b:03d}{k}\n"),
    "B  <box> <slot>":           (lambda b, s: f"{b:03d} {s}\n", lambda b, k: f"R{b:03d} {k}\n"),
    "C  <box>@<bay>.<row>.<tier>": (lambda b, s: f"{b}@{s[:3]}.{s[3:5]}.{s[5:]}\n", lambda b, k: f"R{b}@{k}\n"),
    "D  <box>,<bay><row>,<tier>": (lambda b, s: f"{b:03d},{s[:3]},{s[3:5]},{s[5:]}\n", lambda b, k: f"R{b:03d},{k}\n"),
}


def sample(rng: random.Random, n_boxes: int) -> tuple[int, str, str]:
    bay = rng.randrange(1, 41)
    row = rng.randrange(0, 13)
    tier = rng.choice([2, 4, 6, 8, 10, 12, 82, 84, 86, 88, 90])
    block = rng.choice(["A", "B", "C1", "D2"])
    key = f"{block}-{rng.randrange(1, 40):03d}-{rng.randrange(1, 9):02d}"
    return rng.randrange(n_boxes), f"{bay:03d}{row:02d}{tier:02d}", key


def main(path: str) -> None:
    tok = Tokenizer.from_file(path)
    rng = random.Random(0)
    for n_boxes in (100, 1000, 1200):
        samples = [sample(rng, n_boxes) for _ in range(2000)]
        print(f"\nload list of {n_boxes} boxes")
        for name, (place, reloc) in FORMS.items():
            p = [len(tok.encode(place(b, s), add_special_tokens=False)) for b, s, _ in samples]
            r = [len(tok.encode(reloc(b, k), add_special_tokens=False)) for b, _, k in samples]
            print(f"  {name:30s} place mean {statistics.mean(p):.2f} max {max(p)} | relocate mean {statistics.mean(r):.2f}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "models/glimmer-30b-4bit/tokenizer.json")

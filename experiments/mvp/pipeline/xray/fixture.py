"""A tiny scripted model: no weights, model inference, network or paid call."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from experiments.mvp.reader.schema import SCHEMA_VERSION
from experiments.mvp.reader.v3.decoder import ReaderDecoder, ReaderLogitsProcessor
from .recorder import RecordingLogitsProcessor

CONTEXT = {"bays": [3, 7, 11], "max_tiers": {"hold": 4, "deck": 3},
           "has_accommodation": True, "hatch_covers": ["h1"]}
SENTENCE = "Close bay 7."
PREFIX = '{"schema_version":' + json.dumps(SCHEMA_VERSION) + ',"constraint":'
TOKENS = [PREFIX, '{"type":', '"close_bay"', ',"bay":', '7', '}}',
          '99', '99}}', '7x', '"max_tiers"', '2', '}', 'x', '<eos>']
STOP = len(TOKENS) - 1
SCRIPT = [0, 1, 2, 3, 4, 5, STOP]


class ScriptedTokenizer:
    """Minimal BPE-shaped vocabulary; explicitly not Glimmer tokenization."""
    def to_str(self):
        return json.dumps({"model": {"type": "BPE", "vocab": {t: i for i, t in enumerate(TOKENS)}},
                           "decoder": {"type": "ByteLevel"},
                           "added_tokens": [{"id": STOP, "content": "<eos>"}]})


def scripted_logits(index: int, *, batch: bool = False):
    values = np.full(len(TOKENS), -5., dtype=np.float32)
    values[SCRIPT[index]] = 3.
    values[12] = 1.  # malformed x remains visible below the desired token
    if index == 4:
        values[7], values[6], values[8], values[4] = 7., 6., 5., 4.
    return values[None, :] if batch else values


def make_fixture() -> dict:
    """Generate JSON-able FAKE trace by applying the actual v3 mask to logits."""
    tokenizer = ScriptedTokenizer()
    processor = ReaderLogitsProcessor(ReaderDecoder(tokenizer, CONTEXT), 2, STOP)
    recorder = RecordingLogitsProcessor(processor, sentence=SENTENCE, top_k=6,
                                        fake=True, model="FAKE-scripted-logits")
    history = [42, 43]
    for index in range(len(SCRIPT)):
        masked = recorder(np.array(history), scripted_logits(index))
        chosen = int(np.argmax(masked))
        history.append(chosen)
        recorder.observe_token(chosen)
    output = ''.join(TOKENS[t] for t in SCRIPT if t != STOP)
    trace = recorder.finish(output)
    return trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "fixtures/fake-illegal-bay.json")
    args = parser.parse_args()
    trace = make_fixture()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(trace, indent=2, ensure_ascii=False) + "\n"
    args.output.write_text(serialized)
    args.output.with_suffix('.js').write_text("window.XRAY_FIXTURE = " + serialized.rstrip() + ";\n")
    print(f"Wrote FAKE trace with {len(trace['steps'])} steps: {args.output}")


if __name__ == "__main__":
    main()

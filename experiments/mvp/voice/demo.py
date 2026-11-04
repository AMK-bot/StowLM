"""Interactive WAV/typed entry point; importing this module makes no paid calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.mvp.voice.flow import VoiceFlow

DEFAULT_INSTANCE = Path("experiments/mvp/corpus/val/small/feeder_500/simulated_yard/seed-040100002.json")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--wav", type=Path)
    source.add_argument("--text")
    context = parser.add_mutually_exclusive_group()
    context.add_argument("--context", type=Path, help="Reader vessel-context JSON")
    context.add_argument("--instance", type=Path, default=None, help="Synthetic instance JSON")
    args = parser.parse_args(argv)
    try:
        if args.context:
            vessel_context = json.loads(args.context.read_text())
        else:
            from experiments.mvp.reader.schema import context_from_instance
            vessel_context = context_from_instance(json.loads((args.instance or DEFAULT_INSTANCE).read_text()))
    except Exception:
        print("Could not load vessel context. Provide a valid --context or --instance file.")
        return 2
    flow = VoiceFlow(vessel_context)
    try:
        if args.wav:
            result = flow.prepare_audio(args.wav)
        else:
            result = flow.prepare_typed(args.text if args.text is not None else input("Type an instruction: "))
        while True:
            if result.status == "typed_required":
                print(result.message)
                result = flow.prepare_typed(input("Type an instruction (empty cancels): "))
                if result.status == "typed_required":
                    flow.cancel()
                    return 1
            print("Transcript: " + result.transcript)
            edit = input("Edit transcript, or press Enter to keep: ")
            if edit:
                print("Transcript: " + edit)
            if input("Confirm this instruction? [yes/no]: ").strip().lower() != "yes":
                flow.cancel()
                print("Instruction cancelled.")
                return 1
            result = flow.confirm(edit if edit else None)
            if result.status == "typed_required":
                continue
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            return 0
    except (EOFError, KeyboardInterrupt):
        flow.cancel()
        print("\nInstruction cancelled.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

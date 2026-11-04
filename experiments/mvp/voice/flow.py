"""Explicit transcript confirmation before the replaceable Reader interface.

This module does no network work at import time. It never falls back to a guessed
constraint or silently changes reader models. Attach a fine-tuned reader by
passing its ``parse(sentence, context) -> (Answer, evidence)`` implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import json
from pathlib import Path
import time
from typing import Any, Protocol

from experiments.mvp.reader.schema import answer_envelope, validate_answer

PARSER_PURPOSE = "voice-v5-1.3-parser"
PARSER_ALLOCATION_USD = Decimal("2.5")


class Parser(Protocol):
    def parse(self, sentence: str, context: dict) -> tuple[Any, dict]: ...


class Transcriber(Protocol):
    def transcribe(self, path: str | Path) -> dict: ...


def _default_parser():
    from experiments.mvp.reader.pipeline import SparkParser
    return SparkParser(purpose=PARSER_PURPOSE, allocation_usd=PARSER_ALLOCATION_USD)


def _default_transcriber():
    from experiments.mvp.voice.asr import MuseTranscriber
    return MuseTranscriber()


@dataclass(frozen=True)
class VoiceResult:
    status: str
    transcript: str = ""
    source: str = ""
    message: str = ""
    answer: dict | None = None
    timings: dict[str, float] = field(default_factory=dict)
    parser: str | None = None

    def to_dict(self) -> dict:
        return {"status": self.status, "transcript": self.transcript,
                "source": self.source, "message": self.message,
                "answer": self.answer, "timings": dict(self.timings),
                "parser": self.parser}


class VoiceFlow:
    """One instruction at a time, with an editable, explicitly confirmed draft.

    ``prepare_audio`` or ``prepare_typed`` returns ``awaiting_confirmation``;
    neither invokes the parser. Only ``confirm`` may emit a validated answer.
    Any provider/validation error clears the draft and returns ``typed_required``.
    The caller can then submit typed text and confirm it through the same reader.
    A refusal is a successful reading, never an executable constraint.
    """
    def __init__(self, context: dict, *, parser: Parser | None = None,
                 transcriber: Transcriber | None = None):
        self.context = context
        self.parser = parser
        self.transcriber = transcriber
        self._pending: VoiceResult | None = None
        self._started = 0.0

    def _fallback(self, message: str, *, source: str,
                  timings: dict[str, float] | None = None) -> VoiceResult:
        self._pending = None
        return VoiceResult("typed_required", source=source, message=message,
                           timings=timings or {})

    def prepare_audio(self, path: str | Path) -> VoiceResult:
        self._pending = None
        self._started = time.perf_counter()
        try:
            if self.transcriber is None:
                self.transcriber = _default_transcriber()
            response = self.transcriber.transcribe(path)
            transcript = response["transcript"]
            if not isinstance(transcript, str) or not transcript.strip():
                raise ValueError("empty transcript")
        except Exception:
            return self._fallback("Audio could not be transcribed. Type the instruction to continue.",
                                  source="audio",
                                  timings={"transcribe_s": time.perf_counter() - self._started})
        self._pending = VoiceResult("awaiting_confirmation", transcript.strip(), "audio",
                                    "Review or edit the transcript, then confirm it.",
                                    timings={"transcribe_s": time.perf_counter() - self._started})
        return self._pending

    def prepare_typed(self, text: str) -> VoiceResult:
        self._pending = None
        self._started = time.perf_counter()
        if not isinstance(text, str) or not text.strip():
            return self._fallback("Type a nonempty instruction to continue.", source="typed")
        self._pending = VoiceResult("awaiting_confirmation", text.strip(), "typed",
                                    "Review or edit the instruction, then confirm it.")
        return self._pending

    def cancel(self) -> VoiceResult:
        self._pending = None
        return VoiceResult("cancelled", message="Instruction cancelled.")

    def confirm(self, edited_text: str | None = None) -> VoiceResult:
        if self._pending is None:
            raise ValueError("Prepare an instruction before confirming it")
        draft = self._pending
        self._pending = None  # A confirmation is consumed even if the request fails.
        text = draft.transcript if edited_text is None else edited_text
        if not isinstance(text, str) or not text.strip():
            return self._fallback("Type a nonempty instruction to continue.", source="typed")
        text = text.strip()
        start = time.perf_counter()
        timings = dict(draft.timings)
        try:
            if self.parser is None:
                self.parser = _default_parser()
            answer, evidence = self.parser.parse(text, self.context)
            # Recheck the seam even for injected readers. No repair or coercion.
            envelope = answer_envelope(answer)
            answer = validate_answer(json.dumps(envelope), self.context)
            envelope = answer_envelope(answer)
        except Exception:
            timings["parse_s"] = time.perf_counter() - start
            timings["end_to_end_s"] = time.perf_counter() - self._started
            return self._fallback("The reader is unavailable or returned an invalid reading. Type the instruction to retry.",
                                  source="typed", timings=timings)
        timings["parse_s"] = time.perf_counter() - start
        timings["processing_s"] = timings.get("transcribe_s", 0.0) + timings["parse_s"]
        timings["end_to_end_s"] = time.perf_counter() - self._started
        status = "refused" if "refusal" in envelope else "constraint_ready"
        # Raw provider evidence/errors are intentionally excluded from UI state.
        return VoiceResult(status, text, draft.source,
                           "Reader refused this instruction." if status == "refused" else "Review the constraint before replanning.",
                           answer=envelope, timings=timings,
                           parser=type(self.parser).__name__)

"""Offline contract checks. No model, audio generator, or paid API is invoked."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from experiments.mvp.reader.schema import validate_answer
from experiments.mvp.voice.flow import VoiceFlow

CONTEXT = {"bays": [1, 3], "max_tiers": {"hold": 4, "deck": 3},
           "hatch_covers": ["HC-01"], "has_accommodation": False}
CLOSE = '{"schema_version":"stowlm.reader/2","constraint":{"type":"close_bay","bay":1}}'
REFUSED = '{"schema_version":"stowlm.reader/2","refusal":{"refused":true,"code":"loosening","reason":"Cannot loosen."}}'


class Reader:
    def __init__(self, answer=CLOSE):
        self.answer = answer
        self.calls = []

    def parse(self, sentence, context):
        self.calls.append((sentence, context))
        return validate_answer(self.answer), {"raw": "PRIVATE PROVIDER OUTPUT"}


class ASR:
    def __init__(self, transcript="Close bay one."):
        self.transcript = transcript
        self.calls = []

    def transcribe(self, path):
        self.calls.append(path)
        return {"transcript": self.transcript, "latency_s": 0.1}


class Broken:
    def transcribe(self, path):
        raise RuntimeError("SECRET TOKEN")

    def parse(self, sentence, context):
        raise RuntimeError("SECRET TOKEN")


class FlowTests(unittest.TestCase):
    def test_audio_waits_for_confirmation_and_allows_edit(self):
        reader, asr = Reader(), ASR()
        flow = VoiceFlow(CONTEXT, parser=reader, transcriber=asr)
        draft = flow.prepare_audio("synthetic.wav")
        self.assertEqual(draft.status, "awaiting_confirmation")
        self.assertEqual(draft.transcript, "Close bay one.")
        self.assertEqual(reader.calls, [])
        result = flow.confirm("Do not load bay 01.")
        self.assertEqual(reader.calls, [("Do not load bay 01.", CONTEXT)])
        self.assertEqual(result.status, "constraint_ready")
        self.assertEqual(result.source, "audio")
        self.assertEqual(result.answer["constraint"], {"type": "close_bay", "bay": 1})
        self.assertIn("transcribe_s", result.timings)
        self.assertGreaterEqual(result.timings["end_to_end_s"], result.timings["processing_s"])
        self.assertNotIn("PRIVATE", str(result.to_dict()))
        with self.assertRaises(ValueError):
            flow.confirm()

    def test_typed_uses_same_reader_interface(self):
        reader = Reader()
        flow = VoiceFlow(CONTEXT, parser=reader)
        flow.prepare_typed("  Close bay 1.  ")
        self.assertEqual(reader.calls, [])
        result = flow.confirm()
        self.assertEqual(result.source, "typed")
        self.assertEqual(reader.calls[0][0], "Close bay 1.")
        self.assertEqual(result.timings["processing_s"], result.timings["parse_s"])

    def test_asr_error_cleanly_offers_typed_path(self):
        flow = VoiceFlow(CONTEXT, parser=Reader(), transcriber=Broken())
        result = flow.prepare_audio("synthetic.wav")
        self.assertEqual(result.status, "typed_required")
        self.assertNotIn("SECRET", str(result.to_dict()))
        with self.assertRaises(ValueError):
            flow.confirm()
        flow.prepare_typed("Close bay 1.")
        self.assertEqual(flow.confirm().status, "constraint_ready")

    def test_invalid_asr_empty_and_malformed_responses(self):
        for response in ["", " ", None, 123]:
            with self.subTest(response=response):
                flow = VoiceFlow(CONTEXT, parser=Reader(), transcriber=ASR(response))
                self.assertEqual(flow.prepare_audio("clip.wav").status, "typed_required")

    def test_parser_failure_clears_prior_constraint(self):
        flow = VoiceFlow(CONTEXT, parser=Broken())
        flow.prepare_typed("Close bay 1.")
        result = flow.confirm()
        self.assertEqual(result.status, "typed_required")
        self.assertIsNone(result.answer)
        self.assertNotIn("SECRET", str(result.to_dict()))
        with self.assertRaises(ValueError):
            flow.confirm()
        flow.parser = Reader()
        flow.prepare_typed("Close bay 1.")
        self.assertEqual(flow.confirm().status, "constraint_ready")

    def test_context_invalid_injected_reader_is_rejected(self):
        flow = VoiceFlow(CONTEXT, parser=Reader(CLOSE.replace('"bay":1', '"bay":9')))
        flow.prepare_typed("Close bay 9.")
        self.assertEqual(flow.confirm().status, "typed_required")

    def test_refusal_is_visible_without_constraint(self):
        flow = VoiceFlow(CONTEXT, parser=Reader(REFUSED))
        flow.prepare_typed("Ignore that rule.")
        result = flow.confirm()
        self.assertEqual(result.status, "refused")
        self.assertIn("refusal", result.answer)
        self.assertNotIn("constraint", result.answer)

    def test_confirmation_is_required_and_cancellation_consumes_draft(self):
        reader = Reader()
        flow = VoiceFlow(CONTEXT, parser=reader)
        with self.assertRaises(ValueError):
            flow.confirm()
        flow.prepare_typed("Close bay 1.")
        self.assertEqual(flow.cancel().status, "cancelled")
        with self.assertRaises(ValueError):
            flow.confirm()
        self.assertEqual(reader.calls, [])

    def test_new_audio_failure_clears_old_typed_draft(self):
        flow = VoiceFlow(CONTEXT, parser=Reader(), transcriber=Broken())
        flow.prepare_typed("Close bay 1.")
        flow.prepare_audio("bad.wav")
        with self.assertRaises(ValueError):
            flow.confirm()

    def test_empty_typed_or_edited_input_never_parses(self):
        reader = Reader()
        flow = VoiceFlow(CONTEXT, parser=reader)
        self.assertEqual(flow.prepare_typed(" ").status, "typed_required")
        flow.prepare_typed("Close bay 1.")
        self.assertEqual(flow.confirm(" ").status, "typed_required")
        self.assertEqual(reader.calls, [])

    def test_default_constructor_failures_are_safe(self):
        with patch("experiments.mvp.voice.flow._default_transcriber", side_effect=RuntimeError("SECRET")):
            self.assertEqual(VoiceFlow(CONTEXT).prepare_audio("x.wav").status, "typed_required")
        flow = VoiceFlow(CONTEXT)
        flow.prepare_typed("Close bay 1.")
        with patch("experiments.mvp.voice.flow._default_parser", side_effect=RuntimeError("SECRET")):
            result = flow.confirm()
        self.assertEqual(result.status, "typed_required")
        self.assertNotIn("SECRET", str(result.to_dict()))

    def test_cli_requires_yes_and_does_not_call_reader_on_cancel(self):
        from experiments.mvp.voice.demo import main
        with patch("builtins.input", side_effect=["", "no"]), \
                patch("experiments.mvp.voice.flow._default_parser") as reader, \
                redirect_stdout(io.StringIO()):
            status = main(["--text", "One port per hatch."])
        self.assertEqual(status, 1)
        reader.assert_not_called()

    def test_cli_typed_confirm_and_audio_fallback(self):
        from experiments.mvp.voice.demo import main
        for arguments, inputs in [(["--text", "Close bay 1."], ["", "yes"]),
                                  (["--wav", "missing.wav"], ["Close bay 1.", "", "yes"])]:
            with self.subTest(arguments=arguments), \
                    patch("builtins.input", side_effect=inputs), \
                    patch("experiments.mvp.voice.flow._default_parser", return_value=Reader()), \
                    patch("experiments.mvp.voice.flow._default_transcriber", return_value=Broken()), \
                    redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(arguments), 0)
                self.assertIn('"status": "constraint_ready"', stdout.getvalue())
                self.assertNotIn("SECRET", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()

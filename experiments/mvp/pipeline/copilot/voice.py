"""Existing VoiceFlow, with distinct transcript and reading confirmations."""
from __future__ import annotations

from experiments.mvp.reader.schema import validate_answer
from experiments.mvp.pipeline.contract.schema import canonical_json
from experiments.mvp.voice.flow import VoiceFlow


class _ReaderBridge:
    def __init__(self, tools):
        self.tools = tools
        self.result = None

    def parse(self, sentence, context):
        self.result = self.tools.call('read_instruction', {'sentence': sentence})
        proposed = self.result.get('proposed_reading')
        if proposed is None or proposed.get('parsed') is None:
            raise ValueError('No authenticated reading')
        answer = validate_answer(canonical_json(proposed['parsed']), context)
        return answer, {'parser': 'authenticated-copilot-reader', 'adapter_sha256': None}


class CopilotVoice:
    """The host confirms transcript first, then confirms the proposed reading.

    ``confirm_transcript`` invokes the reader through Tools, but never calls
    ``confirm_reading``. The host uses Tools.confirm_reading for that second step.
    """
    def __init__(self, tools, *, transcriber=None):
        from experiments.mvp.reader.schema import context_from_instance
        self.tools = tools
        self._bridge = _ReaderBridge(tools)
        self.flow = VoiceFlow(context_from_instance(tools.instance), parser=self._bridge,
                              transcriber=transcriber)

    def prepare_audio(self, path):
        return self.flow.prepare_audio(path).to_dict()

    def prepare_typed(self, text):
        return self.flow.prepare_typed(text).to_dict()

    def confirm_transcript(self, edited_text=None):
        self._bridge.result = None
        transcript = self.flow.confirm(edited_text).to_dict()
        return dict(transcript=transcript, reading=self._bridge.result,
                    reading_confirmed=False)

    def cancel(self):
        return self.flow.cancel().to_dict()


VoiceBridge = CopilotVoice

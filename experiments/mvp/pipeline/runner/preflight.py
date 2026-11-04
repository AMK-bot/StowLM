"""Map editable voice drafts to pipeline statuses before making a packet."""
from __future__ import annotations


def prepare_voice(path, *, transcriber):
    """Transcribe only; the planner edits/confirms text before the packet reader.

    The caller supplies its service or recording. This helper never selects a
    network provider, reads an instruction, or runs an engine.
    """
    from experiments.mvp.voice.flow import VoiceFlow
    if transcriber is None:
        return dict(status='needs_answers', message='Choose a recorded transcript or type the instruction and retry.',
                    passed=False, plan=None, package=None, transcript='', evidence={}, synthetic=True)
    draft = VoiceFlow({}, transcriber=transcriber).prepare_audio(path).to_dict()
    ready = draft['status'] == 'awaiting_confirmation'
    return dict(status='needs_confirmation' if ready else 'needs_answers',
                message=('Review or edit the transcript, then submit it as a voyage sentence for reading.'
                         if ready else 'Audio could not be transcribed. Type the instruction or use a recorded transcript and retry.'),
                passed=False, plan=None, package=None, transcript=draft['transcript'],
                evidence=dict(voice=draft), synthetic=True)

"""Generation-identity binding for paid Spark responses (pure; no stowage imports, no runtime).

A saved response is evidence only for the exact request that produced it. The paid run
(run_pilot.py) records, at the `complete` boundary:

- instance_id, instance_sha256 = sha256(instance.model_dump_json()) under rule_freeze_v1
- prompt_version, max_output_tokens, prompt_appendix (absent = compact-only prompt)
- prompt_sha256 = sha256 of the exact prompt bytes sent (also saved to prompts/<id>.txt)
- response_sha256 = sha256 of the response text bytes (saved to responses/<id>.txt)

replay_pilot.py checks every one of these before a saved response is scored.
Rows from the pre-fix pilot have prompt_sha256 = null and
prompt_binding = PRE_FIX_PROMPT_BINDING (see bind_existing.py).
"""
from __future__ import annotations

from hashlib import sha256

PRE_FIX_PROMPT_BINDING = 'unrecorded (pre-fix pilot)'
UNBOUND_PROMPT_REASON = 'prompt bytes not recorded'
UNBOUND_LABEL = 'instance+response only'
FULL_LABEL = 'instance+prompt+response'


def digest(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode('utf-8')
    return sha256(data).hexdigest()


def instance_digest(instance) -> str:
    return digest(instance.model_dump_json())


class BindingMismatch(Exception):
    """A saved response does not belong to the request now being replayed."""

"""One-off: bind the pre-fix pilot rows in results.jsonl to their saved responses (no HTTP, no runtime).

The 6 pilot calls ran before run_pilot.py recorded prompt bytes. For each row that has
no prompt_sha256 field yet: response_sha256 = sha256 of responses/<id>.txt (null if no
response was saved), prompt_sha256 = null, prompt_binding = "unrecorded (pre-fix pilot)"
(or "no request sent" for rows the allocation guard skipped).
replay_pilot.py then refuses those rows ("prompt bytes not recorded") unless run with
--accept-unbound-prompt. Rows written by the fixed run_pilot.py are left unchanged.
Idempotent; results-raw.jsonl is not touched.

Usage: uv run python experiments/spark-planner/w2-pilot/bind_existing.py
"""
from __future__ import annotations

from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from binding import PRE_FIX_PROMPT_BINDING, digest  # noqa: E402

ROOT = Path(__file__).parent
NO_REQUEST = 'no request sent'  # skipped by the allocation guard before any HTTP request


def bind(row: dict, root: Path = ROOT) -> dict:
    if 'prompt_sha256' in row and row.get('prompt_binding') not in (PRE_FIX_PROMPT_BINDING, NO_REQUEST):
        return row  # recorded at the paid-call boundary
    text_path = root/'responses'/f"{row['instance_id']}.txt"
    row['response_sha256'] = digest(text_path.read_bytes()) if text_path.exists() else None
    row['prompt_sha256'] = None
    row['prompt_binding'] = PRE_FIX_PROMPT_BINDING if row.get('attempted') else NO_REQUEST
    return row


def main(path: Path = ROOT/'results.jsonl'):
    rows = [bind(json.loads(line), path.parent) for line in path.read_text().splitlines() if line.strip()]
    path.write_text(''.join(json.dumps(r, sort_keys=True)+'\n' for r in rows))
    return rows


if __name__ == '__main__':
    for r in main():
        print(r['instance_id'], r['prompt_binding'], r['response_sha256'])

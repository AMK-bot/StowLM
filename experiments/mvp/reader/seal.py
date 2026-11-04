"""Extract and seal independent Claude output; never edits sentence text or labels."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from experiments.mvp.reader.schema import canonicalize, system_prompt

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    target = HERE / 'data/sealed.jsonl'
    if target.exists():
        raise ValueError('sealed output already exists; never silently regenerate it')
    raw_path = HERE / 'sealed/claude_response.json'
    response = json.loads(raw_path.read_text())
    if response.get('is_error'):
        raise ValueError('Independent reviewer returned an error')
    rows = [json.loads(line) for line in response['result'].splitlines() if line.strip()]
    if len(rows) != 120 or len({r['sentence'].casefold() for r in rows}) != 120:
        raise ValueError('Reviewer must produce exactly 120 unique sentences')
    if [r['id'] for r in rows] != [f'sealed-{i:03d}' for i in range(1, 121)]:
        raise ValueError('Reviewer ids differ from public sealed contract')
    for row in rows:
        canonicalize(row['expected'], row['context'])
        row['source'] = 'independent-claude-opus-reviewer-no-grammar-access'
    freeze = json.loads((HERE / 'results/presealed-freeze.json').read_text())
    if freeze['system_prompt_sha256'] != hashlib.sha256(system_prompt().encode()).hexdigest():
        raise ValueError('Prompt changed after presealed freeze')
    target.write_text(''.join(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n' for row in rows))
    provenance = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'author': 'Separate Claude Opus CLI reviewer process',
        'isolation': 'cwd=/private/tmp, --tools empty, --no-session-persistence; only public schema/context supplied on stdin',
        'training_generator_seen': False, 'grammar_seen': False,
        'training_or_tuning_on_sealed': False,
        'raw_response_sha256': sha(raw_path),
        'reviewer_prompt_sha256': sha(HERE / 'sealed/reviewer_prompt.txt'),
        'sealed_sha256': sha(target),
        'system_prompt_sha256': freeze['system_prompt_sha256'],
        'rows': len(rows), 'kinds': dict(Counter(r['kind'] for r in rows)),
        'reviewer_session_id': response.get('session_id'),
        'reviewer_model_usage': response.get('modelUsage'),
        'reviewer_cost_usd_reported': response.get('total_cost_usd'),
        'policy': 'Read only for fixed evaluation; no training, hyperparameter, threshold, vocabulary or prompt selection from this set.',
    }
    (HERE / 'sealed/provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(json.dumps({k: provenance[k] for k in ('rows', 'kinds', 'sealed_sha256')}, indent=2))


if __name__ == '__main__':
    main()

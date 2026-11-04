"""Normalise results-raw.jsonl into results.jsonl (no HTTP): syntax from saved text, blocked/skip flags.

Needs the stowage runtime (syntax recompute). The binding and blocked-null steps are also
runnable alone and light: bind_existing.py, blocked_metrics.py.
"""
import json
from pathlib import Path
import run_pilot
from stowage.io import load_instance
import bind_existing
import blocked_metrics

ROOT = Path(__file__).parent
tokenizer = run_pilot.load_tokenizer()
out = []
for line in (ROOT/'results-raw.jsonl').read_text().splitlines():
    row = json.loads(line)
    metrics = row.get('raw_metrics', {})
    if metrics.get('error_type') == 'SpendCapExceeded':
        row.update(attempted=False, raw_plan_legal=None, own_checker_passed=None, shipped_after_repair=None,
                   skipped_reason=('allocation guard refused before any HTTP request: the $1.70 reservation for a '
                                   '131072-token cap exceeded the $5 job allowance under conservative accounting '
                                   '(unknown $1.36 hold from an interrupted probe + concurrent explainer spend)'))
    text_path = ROOT/'responses'/f"{row['instance_id']}.txt"
    if text_path.exists():
        instance = load_instance(ROOT/row['instance_path'])
        row['syntax'] = run_pilot.syntax_score(instance, tokenizer, text_path.read_text())
    row['environment_blocked'] = bool(row.get('attempted')) and str(metrics.get('mask_global_screen', '')).startswith('unavailable')
    if row['environment_blocked']:
        row['legality_note'] = ('Replay engine had no legal action at depth 0: global stability screen unavailable '
                                '(production linear policy epoch stale, plan 4.11). Legality/checker not measurable; '
                                're-score with replay_pilot.py after requalification.')
    # Pre-fix rows: bind to saved responses (prompt bytes unrecorded); blocked rows: null legality.
    out.append(blocked_metrics.apply(bind_existing.bind(row, ROOT)))
(ROOT/'results.jsonl').write_text(''.join(json.dumps(r, sort_keys=True)+'\n' for r in out))
for r in out:
    print(r['instance_id'], r.get('attempted'), r.get('environment_blocked'), json.dumps(r.get('syntax'))[:160])

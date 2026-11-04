"""D115 CPU-only availability probe on one TRAIN map per ship and MVP rung."""
import hashlib
import json
import resource
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

from llm.exit.identity import identity_guard
from llm.exit.selection import decoder_factory
from llm.exit.securing_guard import securing_metrics
from llm.stowlm.format import ActionCodec
from llm.stowlm.mask import MaskConfig
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.io import load_instance


class CharacterTokenizer:
    def encode(self, text, **kwargs):
        return SimpleNamespace(ids=[ord(c) for c in text])

    def decode(self, ids, **kwargs):
        return ''.join(chr(i) for i in ids)


def main():
    started = monotonic()
    root = Path(__file__).resolve().parents[3]
    corpus = root / 'experiments/mvp/corpus'
    manifest = json.loads((corpus / 'manifest.json').read_text())
    train = [row for row in manifest['files'] if row.get('split') == 'train'
             and row.get('active_for_current_release', True)
             and row.get('rung') in ('tiny', 'small')]
    ships = sorted({row['ship'] for row in train})
    guard = identity_guard(corpus)
    rows = []
    for ship in ships:
        for rung in ('tiny', 'small'):
            choices = sorted(row['path'] for row in train
                             if row['ship'] == ship and row['rung'] == rung)
            if choices:
                path = corpus / choices[0]
                source = 'active train manifest'
            else:
                # Streaming corpus publication may precede manifest refresh.
                choices = sorted(p for p in (corpus / 'train' / rung / ship).rglob('*.json')
                                 if not p.name.endswith('.audit.json'))
                if not choices:
                    rows.append(dict(ship=ship, rung=rung, status='no train map available'))
                    continue
                path = choices[0]
                source = 'train directory; ship present in manifest, rung not yet listed'
            phase = monotonic()
            row = dict(ship=ship, rung=rung, input=str(path.relative_to(root)), source=source)
            try:
                instance = load_instance(path)
                row['canonical_sha256'] = guard.require_allowed(instance, role='train', input_path=path,
                    raw_sha=hashlib.sha256(path.read_bytes()).hexdigest())
                engine = make_feasibility_engine(instance, get_action_space(),
                    config=FeasibilityConfig(extra={'global_screen': 0}))
                codec = ActionCodec(CharacterTokenizer(), [box.id for box in instance.containers])
                decoder = decoder_factory()(instance, engine, codec, MaskConfig(), 1)[0]
                row['legal_actions'] = len(decoder.engine.legal_actions())
                row['securing'] = securing_metrics([decoder])
                row['status'] = 'available'
            except Exception as exc:
                row.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            row['wall_s'] = monotonic() - phase
            rows.append(row)
            print(json.dumps(row), flush=True)
            if row['status'] == 'failed':
                break
        if rows[-1]['status'] == 'failed':
            break
    report = dict(timestamp=datetime.now(timezone.utc).isoformat(),
                  scope='TRAIN only; shared production/lookahead/guarded securing decoder legal-action query; no MLX, solver, or full checker',
                  ships=ships, rows=rows, wall_s=monotonic()-started,
                  peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  passed=all(row['status'] == 'available' for row in rows),
                  evaluations=sum(row.get('securing', {}).get('evaluations', 0) for row in rows),
                  unavailable=sum(row.get('securing', {}).get('unavailable', 0) for row in rows),
                  fail_open=sum(row.get('securing', {}).get('fail_open', 0) for row in rows))
    (root / 'experiments/mvp/exit/securing-ships-D115.json').write_text(json.dumps(report, indent=2)+'\n')
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

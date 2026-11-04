"""Per-map table for SCALE.md, from the run folders: boxes aboard at the end of the decoded sequence (replay of the plan's
moves), distinct boxes ever placed, moves, checker outcome, time, memory, and the decode curve. Prefixes are never plans."""
import json, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent


def last_position(moves):
    where = {}
    for m in moves:
        box = m.get('container_id')
        if box:
            where[box] = m.get('dst')
    return where


def row(rec_path):
    rec = json.loads(rec_path.read_text())
    folder = rec_path.parent/rec['instance_id']
    result = json.loads((folder/'result.json').read_text())
    report = json.loads((folder/'report.json').read_text()) if (folder/'report.json').exists() else None
    outcome = json.loads((folder/'outcome.json').read_text()) if (folder/'outcome.json').exists() else {}
    plan = result.get('plan') or {}
    moves = plan.get('moves') or []
    instance = json.loads((Path(__file__).resolve().parents[4]/rec['input']).read_text())
    required = set(instance['load_list'])
    where = last_position(moves)
    slots = {b for b, d in where.items() if d and str(d)[:1].isdigit()}
    aboard = len(required & slots)
    m = rec['metrics']
    count = (report or {}).get('count') or {}
    rules = Counter(v.get('rule_id') for v in (report or {}).get('violations', []) if v.get('severity') == 'hard')
    return dict(id=rec['instance_id'], rung=rec['rung'], boxes=rec['n_boxes'], aboard=aboard, touched=len(required & set(where)),
                actions=m.get('n_actions'), lifts=len(moves), tokens_in=m.get('tokens_in'), tokens_out=m.get('tokens_out'),
                status=rec['status'], outcome=outcome.get('outcome'), shippable=rec['shippable'], checker=rec['report_passed'],
                dead_ends=m.get('n_dead_end'), backtracks=m.get('gen_backtracks'), rewound=m.get('gen_rewound_actions'),
                finisher=m.get('candidate_failures'), finisher_timeouts=m.get('finisher_budget_timeouts'),
                decode_s=m.get('gen_wall_s'), select_s=m.get('candidate_selection_s'), plan_s=rec['plan_s'],
                pipeline_s=rec['pipeline_s'], peak_gb=rec['peak_memory_gb'], message=rec['message'], objective=rec['objective'],
                count={k: v for k, v in count.items() if not isinstance(v, (list, dict))},
                future=count.get('future'), hard_rules=dict(rules.most_common()), tok_s=m.get('gen_tok_s'))


rows = [row(p) for d in sys.argv[1:] for p in sorted(Path(d, 'instances').glob('*.json'))]
for r in rows:
    print(json.dumps({k: v for k, v in r.items() if k not in ('future',)}, default=str))
(HERE/'scale_rows.json').write_text(json.dumps(rows, indent=1, default=str))

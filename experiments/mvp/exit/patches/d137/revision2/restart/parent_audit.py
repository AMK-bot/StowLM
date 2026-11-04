"""D142 F6: audit 8.1 and 8.2 on the 30 audit maps under the installed epoch, then choose the loop's parent.

Runs the existing rebaseline_on_host.py once per adapter, one after the other (one GPU job at a time; a finished audit
resumes from its ready marker). Rule, fixed before either audit: more passes, then fewer mean weighted moves on the maps
both pass; 8.2 on an exact tie (it trained on more gold). The other adapter stays in the final comparison.
APPROXIMATION: rebaseline_on_host.py uses its recorded 90 s finisher for both adapters; the loop re-audits its parent at
the 120 s budget. Equal for both, so the choice is fair, but these numbers aren't 9.1 numbers.
Usage: uv run python .../revision2/restart/parent_audit.py [--check-only]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from state import ROOT, DEFAULT_OUT, atomic_json

ADAPTERS = {'8.1': ROOT/'experiments/mvp/sft/stowlm-v1/adapter', '8.2': ROOT/'experiments/mvp/sft/stowlm-v2/adapter'}
SCHEMA = 'exit-d142-parent-choice-v1'
CODE_READY = DEFAULT_OUT/'D137-CODE.READY'  # written by write_code_ready.py; rebaseline_on_host.py checks its pins


def missing_inputs():
    """Each adapter and the DONE.json beside its folder (run_81.sh writes <out>/DONE.json next to <out>/adapter)."""
    needed = [p for adapter in ADAPTERS.values() for p in (adapter, adapter.parent/'DONE.json')] + [CODE_READY]
    return [str(p) for p in needed if not p.exists()]


def outcome(rows):
    return {row['instance_id']: row.get('weighted_moves') for row in rows if row.get('passed') is True}


def choose(rows81, rows82):
    """Pure rule: passes, then mutual mean moves, then 8.2."""
    pass81, pass82 = outcome(rows81), outcome(rows82)
    mutual = sorted(set(pass81) & set(pass82))
    mean = lambda values: sum(values[i] for i in mutual)/len(mutual) if mutual else None
    moves81, moves82 = mean(pass81), mean(pass82)
    if len(pass81) != len(pass82):
        label = '8.1' if len(pass81) > len(pass82) else '8.2'
        reason = 'more passes'
    elif moves81 is not None and moves81 < moves82:
        label, reason = '8.1', 'equal passes, fewer mutual moves'
    elif moves81 is not None and moves82 < moves81:
        label, reason = '8.2', 'equal passes, fewer mutual moves'
    else:
        label, reason = '8.2', 'exact tie: 8.2 by the fixed rule'
    return dict(parent_label=label, reason=reason, passes={'8.1': len(pass81), '8.2': len(pass82)},
                mutual_maps=len(mutual), mutual_mean_moves={'8.1': moves81, '8.2': moves82})


def audit(label, out):
    target = out/f'parent-audit-{label}'
    marker = target/'rebaseline-ready.json'
    if not marker.exists():
        env = dict(os.environ, STOWLM_MLX_CACHE_LIMIT_GB='2')
        env.pop('STOWAGE_BALLAST_V4', None)
        subprocess.run([sys.executable, str(ROOT/'experiments/mvp/exit/rebaseline_on_host.py'), '--out', str(target),
                        '--parent', str(ADAPTERS[label]), '--code-ready', str(CODE_READY)], cwd=ROOT, env=env, check=True)
    return json.loads((target/'audit-baseline'/'summary.json').read_text()), json.loads(marker.read_text())['summary']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    missing = missing_inputs()
    if args.check_only or missing:
        print(json.dumps(dict(missing=missing, audits=[str(args.out/f'parent-audit-{k}') for k in ADAPTERS], gpu_launched=False)))
        raise SystemExit(1 if missing else 0)
    rows81, summary81 = audit('8.1', args.out)
    rows82, summary82 = audit('8.2', args.out)  # sequential: the second starts after the first has released the GPU
    choice = choose(rows81, rows82)
    other = '8.2' if choice['parent_label'] == '8.1' else '8.1'
    atomic_json(args.out/'parent-choice.json', dict(schema=SCHEMA, **choice, parent=str(ADAPTERS[choice['parent_label']]),
        comparison_adapter=str(ADAPTERS[other]), summaries={'8.1': summary81, '8.2': summary82}))
    print(json.dumps(choice, indent=1))


if __name__ == '__main__':
    main()

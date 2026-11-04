"""Score immutable decisions again after a documented scorer-only correction.

This does not replay tools or ask Spark. Agent, authorization, safety and tool
policy identities must match the paid campaign; metrics are a separate version.
"""
from datetime import datetime, timezone
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_file
from .behavior_scores import score_trace, score_behavior

HERE = Path(__file__).resolve().parent


def rescore(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    value = json.loads(source.read_text())
    frozen = value['freeze']['files']
    for name in ('agent.py', 'authorization.py', 'safety.py', 'tools.py', 'jobs.py'):
        if frozen[name] != sha256_file(HERE / name):
            raise ValueError('This scorer-only report requires unchanged agent policy')
    results = []
    for row in value['results']:
        turns = []
        for turn in row.get('turns', []):
            # A provider-incomplete event is retained in the original model calls
            # and final_gate termination; completed turns need no usage inference.
            gate = next((event.get('content', {}) for event in reversed(turn['trace'])
                         if event.get('role') == 'final_gate'), {})
            status = 'unavailable' if gate.get('termination') == 'model_unavailable' else None
            scored = score_trace(turn['trace'], turn=turn['turn'], completion_status=status)
            turns.append(scored)
        results.append(dict(id=row['id'], turns=turns, behavior=score_behavior(turns),
                            route=row.get('route'), goal=row.get('goal')))
    report = dict(label='Scorer-only correction of immutable paid decisions; no new model decisions or tool execution.',
        source_file=str(source.relative_to(HERE)), source_sha256=sha256_file(source),
        source_complete=value.get('complete'), source_freeze=value['freeze'],
        scorer_sha256=sha256_file(HERE/'behavior_scores.py'), created_utc=datetime.now(timezone.utc).isoformat(),
        corrections=['Descriptive requiring/together are not alternative IDs.',
                     'Provider-unavailable and step-limited turns are N/A for every metric.'],
        behavior=score_behavior([turn for row in results for turn in row['turns']]), results=results)
    destination.write_text(json.dumps(report,indent=2)+'\n')
    return {key: val for key,val in report.items() if key!='results'}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('destination',type=Path)
    args=parser.parse_args()
    print(json.dumps(rescore(args.source,args.destination),indent=2))

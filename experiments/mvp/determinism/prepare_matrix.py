"""Preregister fresh-process hash/cache comparisons; never starts a worker."""
import hashlib
import json
from pathlib import Path

H = Path(__file__).resolve().parent
inv = json.loads((H / 'inventory.json').read_text())
jobs = []
for item in inv['prefixes']:
    prefix = json.loads(Path(item['path']).read_text())
    case = prefix['case']
    assert case in ('train20-31100000', 'train20-31100001')
    meta_path = Path(item['path']).parent / 'old-case-result.json'
    meta = json.loads(meta_path.read_text())
    history = [r for r in meta['rollouts'] if r['arm'] == 'baseline'
               and r['repetition'] < prefix['repetition']]
    assert len(history) == prefix['repetition']
    for seed in (0, 1, 42):
        for mode in ('cold', 'history-warm'):
            name = f'{case}-rep{prefix["repetition"]:02}-seed{seed}-{mode}'
            jobs.append(dict(
                case=case, repetition=prefix['repetition'], prefix=item['path'],
                prefix_sha256=item['sha256'], runtime=inv['runtime'], hashseed=seed,
                cache_mode=mode,
                prior_baseline_history=dict(path=str(meta_path), sha256=hashlib.sha256(meta_path.read_bytes()).hexdigest(),
                                            repetitions=list(range(prefix['repetition']))) if mode == 'history-warm' else None,
                directory=str(H / 'matrix-current-1' / name), os_cpu_soft_s=240,
                os_cpu_hard_s=250, duty_fraction=.9))
value = dict(
    status='QUEUED; no campaign admission', jobs=jobs, train_only=True,
    config=dict(C2_global_screen=0, rule_freeze=False, lookahead=False,
                screen='NecessaryDeckSecuringScreen'),
    cache_protocol=dict(
        cold='New instance and native root; replay fixed actions directly, fresh securing screen at target prefix. Module/tokenizer initialization naturally populates static caches.',
        history_warm='New process and instance; native root copies and one shared securing screen replay all prior saved BASELINE repetitions of this same train case. Query each actual prefix; record any historical next action now masked, then continue fixed native replay. Target replay from the shared root and screen. No cross-instance or test cache entries.',
        scope='Initially vary native+securing jointly; if a difference reproduces, preregister2x2 isolation. Cache contents/metrics and full force-component results will be recorded.'),
    comparisons='Within each exact prefix, compare sorted native legalACTIONs, securing legalACTIONs and actual MaskedDecoder TOKEN sets for every proper prefix of each legal action. Hashseed matrix fresh process; all prefixes/inputs/wholetraintraces pinned.',
    estimate=dict(cores=1, ram_gb=1, expected_cpu_minutes=[8, 12],
                  expected_wall_minutes=[10, 20],
                  basis='Admitted current-cold pilot11.342wall/6.251CPU plus prior-repetition warmup; upper uncertainty due warm traces up to15earlier repetitions.'))
(H / 'matrix-preregistration.json').write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
print(json.dumps(dict(jobs=len(jobs), train_only=True, status=value['status'])))

"""Prepare review-only v5 scope without Git or reading sealed test inputs."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import time
import os
import tempfile
from collections import defaultdict

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    spec = importlib.util.spec_from_file_location('release_closure', HERE / 'closure.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy = json.loads((HERE / 'closure-policy.json').read_text())
    policy['exclude_parts'] += ['sealed', 'sealed-v2', 'sealed-v3', 'test-v3', 'test_v3',
        'RCSPPSuite', 'rcsppsuite', 'before_source', 'before-source', 'archive',
        'data-v2', 'data', 'results', 'results-v2', 'runs', 'runs-brief']
    # data/fixtures remains an explicitly permitted root; restrict generated experiment data instead.
    policy['exclude_parts'].remove('data')
    policy['exclude_patterns'] += ['**/*RCSPPSuite*', '**/*rcsppsuite*', '**/test-v3/**',
        '**/test_v3/**', '**/before_source/**', '**/before-source/**', '**/source_before/**',
        '**/*private*key*', '**/*signing*key*', '**/expert*', '**/provider*', '**/*_optimizer*']
    policy['exclude_prefixes'] += ['experiments/mvp/reader/data', 'experiments/mvp/reader/data-v2',
        'experiments/mvp/reader/sealed', 'experiments/mvp/reader/sealed-v2',
        'experiments/mvp/reader/sealed-v3', 'experiments/mvp/corpus/test-v3']
    # Include v5 reader/refusal/voice helpers through the same import analysis as the app.
    extras = []
    for folder in ['experiments/mvp/reader', 'experiments/mvp/voice',
                   'experiments/mvp/strategy/fitted-rule', 'experiments/mvp/hybrid']:
        base = ROOT / folder
        if base.is_dir():
            extras += [p.relative_to(ROOT).as_posix() for p in base.glob('*.py') if not p.is_symlink()]
    extras += ['app/brief_prepared.json', 'app/prize_config.json',
               'experiments/mvp/reader/contract.json', 'experiments/mvp/reader/config-v2.json']
    extras = [p for p in extras if (ROOT / p).is_file()]
    original = module.read_list(HERE / 'commit-list.txt')
    began = time.monotonic()
    # Other executives own the live tree. Pin each complete file once, then run
    # dependency analysis entirely against immutable scratch bytes.
    frozen = Path(tempfile.mkdtemp(prefix='stowlm-v5-source-', dir='/private/tmp'))
    deny = module.Closure(ROOT, policy)
    seeds = set(original) | set(extras)
    prior = HERE/'generated-v5/closure.json'
    if prior.exists(): seeds.update(r['path'] for r in json.loads(prior.read_text())['dependency_rows'])
    for folder in ['src','tests','app','params','data/fixtures']:
        seeds.update(deny.walk(folder))
    for name in sorted(seeds):
        if deny.deny(name) or not (ROOT/name).is_file(): continue
        source = ROOT/name
        for attempt in range(3):
            raw = source.read_bytes()
            if source.read_bytes()==raw: break
        else: raise RuntimeError('File continuously changing: '+name)
        dest=frozen/name; dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(raw)
    closure = module.Closure(frozen, policy)
    closure.build(original, extras)
    rows = [dict(closure.files[p], reasons=sorted(closure.reasons[p])) for p in sorted(closure.files)]
    # Never promote a sensitive literal merely because a dependency uses its file.
    home = re.compile(rb'(?:/Users/|/home/|C:\\\\Users\\\\)[^\s\x00\"\'<>]*')
    key = re.compile(rb'-----BEGIN (?:[A-Z ]*PRIVATE KEY)-----|\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})')
    holds, selected = [], []
    for row in rows:
        raw = (frozen / row['path']).read_bytes()
        assert module.digest(raw) == row['sha256'], 'Concurrent source change; rerun preparation'
        reasons = []
        if home.search(raw): reasons.append('absolute home path; owner must scrub and rerun')
        if key.search(raw): reasons.append('credential/private-key pattern; owner review required')
        if row['path'] in ['app/REVIEW_FIXES.md']:
            reasons.append('internal review notes; not needed for public runtime')
        if reasons: holds.append(dict(row, hold_reasons=reasons))
        else: selected.append(row)
    out = HERE / 'generated-v5'
    out.mkdir(exist_ok=True)
    (out / 'policy.json').write_text(json.dumps(policy, indent=2) + '\n')
    report = dict(created_utc=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        source_root='.', pinned_source=str(frozen), baseline='experiments/mvp/repo/commit-list.txt',
        baseline_sha256=module.digest((HERE / 'commit-list.txt').read_bytes()),
        rows=selected, dependency_rows=rows, selected=len(selected), bytes=sum(r['bytes'] for r in selected),
        errors=closure.errors, excluded={p:sorted(v) for p,v in sorted(closure.excluded.items())},
        publication_holds=holds, runtime_references=closure.references,
        unresolved_local_imports=sorted(closure.unresolved), wall_s=time.monotonic()-began,
        limitations=['No Git/history audit; 29 Oct comparison uses the prior explicit manifest.',
          'Held files are omitted, so dependency closure may be broken until owner scrubs them.',
          'No sealed test-v3 input read; all sealed reader datasets omitted.',
          'Pattern scan does not certify absence of unknown credentials or third-party material.',
          'Publication requires exact-snapshot review; this manifest is not permission.'])
    (out / 'closure.json').write_text(json.dumps(report, indent=2) + '\n')
    (HERE / 'publication-holds-v5.json').write_text(json.dumps(holds, indent=2) + '\n')
    groups = defaultdict(list)
    for row in selected:
        p = row['path']
        group = ('01 installation' if '/' not in p else '02 core source' if p.startswith('src/')
            else '03 demo app' if p.startswith('app/') else '04 tests' if p.startswith('tests/')
            else '05 parameters and synthetic fixtures' if p.startswith(('params/', 'data/fixtures/'))
            else '06 v5 reader voice hybrid fitted-rule' if any(p.startswith('experiments/mvp/'+f+'/') for f in ['reader','voice','hybrid','strategy/fitted-rule'])
            else '07 reproducibility helpers')
        groups[group].append(row)
    lines = ['# v5 explicit commit proposal; no staging or publication authorized',
        '# bytes<TAB>repo-relative path; every group remains conditional on final rights/sensitivity review', '']
    for group, entries in sorted(groups.items()):
        lines += [f'[{group}] {len(entries)} files; {sum(r["bytes"] for r in entries)} bytes']
        lines += [f'{r["bytes"]}\t{r["path"]}' for r in entries]
        lines.append('')
    (HERE / 'candidate-paths-v5.txt').write_text('\n'.join(lines) + '\n')
    old = set(original)
    new = {r['path'] for r in selected}
    diff = ['# Commit proposal v5', '', 'Review only. Do not stage this prep directory or any group wholesale.', '',
        f'{len(selected)} existing paths, {report["bytes"]:,} bytes. {len(new-old)} additions and {len(old-new)} omissions versus the 29 Oct explicit list.', '',
        '| Proposed commit group | Paths | Bytes |', '|---|---:|---:|']
    diff += [f'| {g} | {len(rr)} | {sum(r["bytes"] for r in rr):,} |' for g,rr in sorted(groups.items())]
    diff += ['', 'Exact paths: [candidate-paths-v5.txt](candidate-paths-v5.txt). Hashes, dependency errors and runtime references: [generated-v5/closure.json](generated-v5/closure.json).', '',
        '## Separate final additions', '',
        'Promote the approved README draft to root README.md; install approved LICENSE and NOTICE; add only the frozen synthetic demo bundle with checked plans, bound explanations and lineage. Those future files are absent from this measured list. Final Reader adapter belongs on Hugging Face, with its approved card and hashes; base weights and optimizer state stay out of Git.', '',
        '## Always out until separately authorized or resolved', '',
        '- All docs/ (including the five docs in the old conditional list); CLAUDE.md, expert communications and internal review notes.',
        '- RCSPPSuite downloads, converted instances and derived training rows; raw KCS and other rights-uncleared raw data; sealed/test-v3 inputs.',
        '- Credentials, private signing fixtures, provider envelopes, .env, .git, local environments/caches, model weights/adapters/checkpoints/optimizer state.',
        '- Files with home paths or credential-like patterns: see publication-holds-v5.json (paths and reasons only, no matched values). Scrub by the owning executive; this order does not edit them.',
        '- This repo-preparation directory, intermediate manifests and scratch receipts are leader review artifacts, not a public source group.', '',
        f'Publication holds: {len(holds)}. Static dependency errors: {len(closure.errors)}. A filtered scope may fail until these are resolved; do not claim ready-to-publish.', '', '## Added since prior list', '']
    diff += ['- `'+p+'`' for p in sorted(new-old)]
    diff += ['', '## Omitted from prior list', '']
    diff += ['- `'+p+'`' for p in sorted(old-new)]
    diff += ['', '## Assumptions', '',
        'No Git is used, so this is a proposed file snapshot, not a commit-history or deletion audit. Existing originals are retained for continuity except explicit exclusion/holds. The 29 Oct baseline is the prior allowlist, not midnight filesystem history. The tree is shared: regenerate and recheck hashes after the freeze. Standards and data citations need a public-safe root NOTICE because docs/ is excluded.']
    (HERE / 'COMMIT-PROPOSAL-V5.md').write_text('\n'.join(diff)+'\n')
    print(json.dumps({k:report[k] for k in ['selected','bytes','wall_s']} | dict(holds=len(holds), errors=len(closure.errors))))


if __name__ == '__main__':
    main()

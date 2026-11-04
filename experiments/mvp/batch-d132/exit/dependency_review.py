"""Static-only ExIt patch dependency proof; never imports project runtime code."""
from __future__ import annotations

import ast
from hashlib import sha256
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
MANIFEST = ROOT / 'experiments/mvp/batch-d132/assembly/revision2-combined-manifest.json'
EXPECTED = '779bc9901801c4a02c3ae0c2853db689d784e7dabd84ea4dee84e6c446047780'
V1 = Path('/private/tmp/stowlm-d132-assembly-20261030/candidate')


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def source_files(root):
    return {str(p.relative_to(root)): sha(p) for base in ('src', 'params')
            for p in (root / base).rglob('*') if p.is_file()
            and p.suffix in {'.py', '.json', '.toml'} and '__pycache__' not in p.parts}


def closure(root, entries):
    pending, visited, paths, dynamic = list(entries), set(), {}, []

    def resolve(name):
        if not name.startswith(('stowage', 'llm', 'tests')):
            return None
        base = root if name.startswith('tests') else root / 'src'
        path = base.joinpath(*name.split('.'))
        if (path / '__init__.py').is_file(): return path / '__init__.py'
        if path.with_suffix('.py').is_file(): return path.with_suffix('.py')
        return None

    while pending:
        name = pending.pop()
        if name in visited: continue
        visited.add(name)
        path = resolve(name)
        if path is None: continue
        relative = str(path.relative_to(root))
        paths[relative] = sha(path)
        parts = name.split('.')
        pending.extend('.'.join(parts[:n]) for n in range(1, len(parts)))
        package = name if path.name == '__init__.py' else name.rpartition('.')[0]
        source = path.read_text()
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if isinstance(node, ast.Import):
                pending.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                parent = package.split('.')
                base = parent[:len(parent) - node.level + 1] if node.level else []
                imported = '.'.join(base + ([node.module] if node.module else []))
                pending.append(imported)
                pending.extend(imported + '.' + alias.name for alias in node.names if alias.name != '*')
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Conservatively include local literal targets of lazy import maps.
                if node.value.startswith(('stowage.', 'llm.')):
                    pending.append(node.value)
            elif isinstance(node, ast.Call):
                call = node.func.id if isinstance(node.func, ast.Name) else (
                    node.func.attr if isinstance(node.func, ast.Attribute) else '')
                if call in {'__import__', 'import_module', 'SourceFileLoader'}:
                    dynamic.append({'path': relative, 'line': node.lineno,
                                    'call': ast.get_source_segment(source, node)})
    return {'files': dict(sorted(paths.items())), 'dynamic_load_sites': dynamic}


assert sha(MANIFEST) == EXPECTED
manifest = json.loads(MANIFEST.read_text())
V2 = Path(manifest['candidate'])
first, second = source_files(V1), source_files(V2)
changed = sorted(p for p in first.keys() | second.keys() if first.get(p) != second.get(p))
assert changed == sorted(manifest['changed_exit'])
assert len(changed) == 10 and all(p.startswith('src/llm/exit/') for p in changed)
patches = []
for patch in manifest['exit_patches']:
    original, copied = ROOT / patch['original_path'], ROOT / patch['copied_path']
    assert sha(original) == sha(copied) == patch['sha256']
    targets = [line.removeprefix('+++ b/') for line in original.read_text().splitlines()
               if line.startswith('+++ b/')]
    assert all(p.startswith('src/llm/exit/') for p in targets)
    patches.append({**patch, 'targets': targets, 'actual_hashes_match': True})
scopes = {
    'raw_checker_100': ['stowage.checker', 'stowage.io', 'stowage.frozen', 'stowage.vessel.fleet',
        'stowage.linear.cache', 'stowage.checker.stability_tables',
        'stowage.checker.stability_liquid_tables', 'stowage.checker.stability_sounding'],
    'mask_214': ['llm.stowlm.mask', 'llm.stowlm.format', 'llm.securing.mask_filter',
        'llm.lookahead.mask_filter', 'llm.instructions.mask_filter',
        'stowage.feasibility._engine', 'stowage.actions.samples', 'stowage.io'],
    'manual_40_and_GM_18': ['tests.checker.test_rules_heat_ids',
        'tests.vessel.test_fleet_manual_restore', 'tests.vessel.test_fleet_lashing_design',
        'tests.vessel.test_fleet_d56', 'tests.conftest', 'stowage.vessel.fleet',
        'stowage.vessel.fleet_lashing_design'],
}
proof = {}
for label, entries in scopes.items():
    found = closure(V2, entries)
    found['entry_modules'] = entries
    found['changed_target_intersection'] = sorted(set(found['files']) & set(changed))
    found['changed_dependency_bytes_vs_v1'] = [p for p, h in found['files'].items()
                                              if not (V1 / p).is_file() or sha(V1 / p) != h]
    assert not found['changed_target_intersection'] and not found['changed_dependency_bytes_vs_v1']
    proof[label] = found


def checker_epoch(root):
    tree = ast.parse((root / 'src/stowage/versions.py').read_text())
    version = next(node.value.value for node in tree.body if isinstance(node, ast.AnnAssign)
                   and isinstance(node.target, ast.Name) and node.target.id == 'CHECKER_VERSION')
    digest = sha256(version.encode())
    for path in sorted((root / 'src/stowage').rglob('*.py')):
        digest.update(str(path.relative_to(root)).encode()); digest.update(path.read_bytes())
    return digest.hexdigest()


epochs = {label: checker_epoch(path) for label, path in [('live_prebatch', ROOT), ('v1_candidate', V1), ('v2_candidate', V2)]}
assert epochs['v1_candidate'] == epochs['v2_candidate'] and epochs['live_prebatch'] != epochs['v2_candidate']
result = {'status': 'PASS_STATIC_DEPENDENCY_PROOF', 'revision': 2,
    'revision2_manifest_sha256': EXPECTED, 'changed_source_paths': changed,
    'only_ten_exit_targets_changed_vs_v1_candidate': True, 'patches': patches,
    'closure_proof': proof, 'checker_epoch_reproduced_from_definition_without_runtime_import': epochs,
    'method': 'Conservative AST imports including function bodies, package initializers, from-import aliases and local literal lazy-import targets.',
    'dynamic_load_review': 'Project dynamic sites in the guarded closure use unchanged stowage solver export/hook catalogs; no llm.exit target is selected. Native/external packages remain under the same environment/build freeze.',
    'carry_forward': ['V1 raw physical CheckReport evidence and input bindings',
        'V1 40 manual/heat tests and 18 GM condition values under identical dependencies',
        'V1 checked sample plans under unchanged physical/checker/mask dependencies; do not relabel the original receipts as V2 runs'],
    'fresh_required': ['New ExIt audit outcome identity / checker ACK transport and real child ACK test',
        'Parallel dataset serial/pool byte equivalence and worker/admission failure tests',
        'Checkpoint suffix save/commit/resume tests; actual host MLX behavior remains separately unverified',
        'Fresh process boundary, broad source receipts, fresh replay-prefetch output and shelf',
        'Final V2 package seal and private tooling rehearsal'],
    'fresh_214_receipt': 'experiments/mvp/batch-d132/revision2/masks/v1-candidate-identity.json',
    'prior_v1_seal': 'Historical only; superseded for installation by pending revision2 qualification',
    'runtime_imports_or_numeric_jobs_performed_by_this_script': False,
    'test_input_bodies_read': False}
(HERE / 'dependency-review.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'status': result['status'], 'changed': len(changed),
                  'closures': {name: len(value['files']) for name, value in proof.items()}, 'checker_epochs': epochs}))

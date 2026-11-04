"""Freeze selected runtime dependencies with standard-library source edits only.

No solver, checker, data map, or copied numerical package is imported here.
Run before measurement, never while measured source is being used.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / 'src/stowage').is_dir())
LAB = ROOT / 'experiments/mvp/frontier/lab'
ENGINE = LAB / 'c5/c5-peel/engine_snapshot'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def runtime(path):
    rel = path.relative_to(ENGINE)
    return (not any(p in {'bench', 'results', '__pycache__'} for p in rel.parts)
            and not path.name.startswith('test_') and not path.name.endswith('_test.py')
            and path.name not in {'conftest.py', 'provenance.py', 'mask_differential.py'})


FRONTIER = {name: f'frozen_frontier.{name}' for name in
            ('alns', 'planner', 'blocked', 'master', 'sight', 'sequence', 'columns', 'blocks', 'e9fix')}
SECURING = {name: f'frozen_securing.{name}' for name in
            ('method', 'stow', 'gap', 'rows', 'chainlab', 'pages_sight', 'pages_balanced',
             'pages', 'pages_base', 'pages_fast_dense', 'repair', 'ballast_opt', 'physics')}
STRATEGY = {'experiments.mvp.strategy.blockrule.capacity_candidate': 'frozen_frontier.capacity_candidate',
            'experiments.mvp.strategy.blockrule.rule': 'frozen_frontier.strategy_rule',
            'experiments.mvp.strategy.headroom.layout': 'frozen_frontier.headroom_layout',
            'experiments.mvp.strategy.compact': 'frozen_frontier.compact'}


def namespace(name, package):
    if name == 'engine' or name.startswith('engine.'):
        return 'frozen_engine12' + name[len('engine'):]
    if name in STRATEGY:
        return STRATEGY[name]
    if name in FRONTIER:
        return FRONTIER[name]
    if package == 'frozen_securing' and name in SECURING:
        return SECURING[name]
    return name


def imports(text, package):
    lines = text.splitlines(keepends=True)
    offsets, total = [], 0
    for line in lines:
        offsets.append(total)
        total += len(line)
    edits = []
    for node in ast.walk(ast.parse(text)):
        changed = False
        replacement = None
        if isinstance(node, ast.ImportFrom) and not node.level:
            if node.module in {'stowage.checker', 'stowage.checker.full'} and any(a.name == 'check' for a in node.names):
                observed = ast.ImportFrom(module='frozen_checks', names=[a for a in node.names if a.name == 'check'], level=0)
                replacement = ast.unparse(observed)
                other = [a for a in node.names if a.name != 'check']
                if other:
                    remaining = ast.ImportFrom(module=node.module, names=other, level=0)
                    replacement += '\n' + ' ' * node.col_offset + ast.unparse(remaining)
                changed = True
            else:
                mapped = namespace(node.module or '', package)
                changed, node.module = mapped != node.module, mapped
        elif isinstance(node, ast.Import):
            for alias in node.names:
                mapped = namespace(alias.name, package)
                if mapped != alias.name:
                    alias.asname = alias.asname or alias.name.split('.')[0]
                    alias.name, changed = mapped, True
        if changed:
            start = offsets[node.lineno - 1] + node.col_offset
            end = offsets[node.end_lineno - 1] + node.end_col_offset
            edits.append((start, end, replacement or ast.unparse(node)))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text, bool(edits)


def rewrite(text, target):
    rel, package = target.as_posix(), target.parts[0]
    changes = []
    if rel == 'frozen_frontier/compact.py':
        text = text[:text.index('\ndef mask_ok(')]
        changes.append('retain original compact_plan/library helpers; omit unrelated LLM mask/batch CLI')
    if rel in {'frozen_engine12/planner.py', 'frozen_engine12/scout/layout_bound.py'}:
        text = text[:text.index('\ndef main(')]
        changes.append('omit standalone catalog/list-loading CLI; retain callable solver API')
    if rel == 'frozen_engine12/frozen.py':
        return ('"""Frozen original Frontier planner, isolated from peel/engine planners."""\n'
                'from frozen_frontier import planner\n'), ['relink loader to local original Frontier planner package']
    text, changed = imports(text, package)
    if changed:
        changes.append('qualify experimental imports with isolated frozen package namespaces')
    if 'from frozen_checks import' in text:
        changes.append('full checker imports use local observer delegate; native checker/report unchanged')
    if rel == 'frozen_frontier/blocked.py':
        text = re.sub(r'def _importable\(\):\n(?:    .*\n|\n)+?(?=def blocked_plan)',
                      'def _importable():\n    return None\n\n\n', text)
        changes.append('remove repository sys.path injection; strategy imports are local')
    # Bootstrap only native src; never add an old experimental directory.
    text = re.sub(r"(?m)^ROOT = (?:HERE\.parents\[\d+\]|Path\(__file__\)\.resolve\(\)\.parents\[\d+\])$",
                  'from frozen_env import ROOT', text)
    text = text.replace('_ROOT = Path(__file__).resolve().parents[5]', 'from frozen_env import ROOT as _ROOT')
    text = text.replace('LAB = HERE.parents[1]', "from frozen_env import ROOT\nLAB = ROOT / 'experiments/mvp/frontier/lab'")
    text = text.replace('ROOT = HERE.parents[5]', 'from frozen_env import ROOT')
    text = text.replace("TABLE_PATH = HERE.parent / 'leader' / 'tiers.toml'", "TABLE_PATH = HERE / 'tiers.toml'")
    text = re.sub(r"for path in \(ROOT / 'src', HERE\.parent, HERE\.parent / 'lab'\):\n"
                  r"    if str\(path\) not in sys\.path:\n        sys\.path\.insert\(0, str\(path\)\)\n", '', text)
    text = re.sub(r'(?m)^sys\.path(?:\.insert\([^\n]*|\[:0\]\s*=[^\n]*)\n', '', text)
    if rel == 'frozen_engine12/gates/screen.py':
        text = text.replace('experiments/mvp/frontier/lab/c1/fx-scout/method.py',
                            'experiments/mvp/frontier/lab/c6/c3-multiport/frozen_engine12/gates/learning-method-source.txt')
        changes.append('proof source hash guard reads exact local frozen source bytes')
    if rel == 'frozen_engine12/gates/mask.py':
        text = text.replace("name == 'alns'", "name == 'frozen_frontier.alns'")
        changes.append('mask binding recognizes isolated Frontier module name in sys.modules')
    changes.append('native-only root bootstrap/local paths; solver bodies and settings unchanged')
    return text, changes


def main():
    sources = {}
    def add(source, target):
        sources[Path(target)] = ROOT / source
    for name in ('alns', 'planner', 'blocked', 'master', 'sight', 'sequence', 'columns', 'blocks'):
        add(f'experiments/mvp/frontier/{name}.py', f'frozen_frontier/{name}.py')
    add('experiments/mvp/frontier/lab/c1/leader/e9fix.py', 'frozen_frontier/e9fix.py')
    for source, target in [('blockrule/capacity_candidate.py', 'capacity_candidate.py'),
                           ('blockrule/rule.py', 'strategy_rule.py'),
                           ('headroom/layout.py', 'headroom_layout.py'), ('compact.py', 'compact.py')]:
        add('experiments/mvp/strategy/' + source, 'frozen_frontier/' + target)
    for name in ('method', 'stow', 'gap', 'rows', 'chainlab'):
        add(f'experiments/mvp/frontier/lab/c5/c5-securing/{name}.py', f'frozen_securing/{name}.py')
    for name in ('pages_sight', 'pages_balanced', 'pages', 'pages_base', 'pages_fast_dense',
                 'repair', 'ballast_opt', 'physics'):
        add(f'experiments/mvp/frontier/lab/c4/c2-layout/{name}.py', f'frozen_securing/{name}.py')
    for path in sorted(ENGINE.rglob('*.py')):
        if runtime(path):
            sources[Path('frozen_engine12') / path.relative_to(ENGINE)] = path
    add('experiments/mvp/frontier/lab/c5/leader/completion.py', 'frozen_reporting/completion.py')
    add('experiments/mvp/frontier/lab/c5/leader/tiers.toml', 'frozen_reporting/tiers.toml')
    add('experiments/mvp/frontier/lab/c5/c5-peel/tiers.py', 'frozen_reporting/tiers.py')
    add('experiments/mvp/frontier/lab/c1/fx-scout/method.py',
        'frozen_engine12/gates/learning-method-source.txt')
    original = {target: path.read_bytes() for target, path in sources.items()}
    rows = []
    for target, path in sorted(sources.items()):
        data = original[target]
        text, rewrites = rewrite(data.decode(), target) if target.suffix == '.py' else (data.decode(), [])
        output = HERE / target
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
        if target.suffix == '.py':
            ast.parse(text, filename=str(target))
        rows.append(dict(source=str(path.relative_to(ROOT)), destination=target.as_posix(),
                         source_sha256=digest(data), copied_sha256=digest(output.read_bytes()), rewrites=rewrites))
    for target, path in sources.items():
        if path.read_bytes() != original[target]:
            raise RuntimeError(f'Source changed while copying: {path}')
    for package in ('frozen_frontier', 'frozen_securing', 'frozen_reporting'):
        (HERE / package / '__init__.py').write_text('"""Isolated frozen runtime; see source-manifest.json."""\nimport frozen_env\n')
    # Engine __init__ contains the original name/version and must stay intact.
    engine_init = HERE / 'frozen_engine12/__init__.py'
    engine_init.write_text(engine_init.read_text() + '\nimport frozen_env\n')
    for row in rows:
        row['copied_sha256'] = digest((HERE / row['destination']).read_bytes())
    packages = {'frozen_frontier', 'frozen_securing', 'frozen_engine12', 'frozen_reporting', 'frozen_env', 'frozen_checks'}
    allowed = set(sys.stdlib_module_names) | packages | {'numpy', 'scipy', 'ortools', 'stowage'}
    unknown = []
    for package in packages - {'frozen_env', 'frozen_checks'}:
        for path in sorted((HERE / package).rglob('*.py')):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                         [node.module] if isinstance(node, ast.ImportFrom) and not node.level else [])
                for name in names:
                    if name and name.split('.')[0] not in allowed:
                        unknown.append((str(path.relative_to(HERE)), name))
    if unknown:
        raise RuntimeError(f'Unfrozen absolute dependencies: {unknown}')
    native = sorted((ROOT / 'src/stowage').rglob('*.py'))
    native += sorted(p for p in (ROOT / 'params').rglob('*') if p.is_file())
    generated = [HERE / 'frozen_env.py', HERE / 'frozen_checks.py', HERE / 'freeze_dependencies.py']
    generated += [HERE / p / '__init__.py' for p in ('frozen_frontier', 'frozen_securing', 'frozen_reporting')]
    manifest = dict(status='frozen; static AST audit only; numerical/import verification pending',
                    baseline='Engine 0.12.0 from c5/c5-peel/engine_snapshot; original Frontier backend restored locally',
                    files=rows, generated={str(p.relative_to(HERE)): digest(p.read_bytes()) for p in generated},
                    shared_native={str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in native},
                    static_absolute_import_audit='all copied imports resolve to stdlib, local frozen packages, stowage, numpy/scipy/ortools')
    manifest['snapshot_sha256'] = digest(json.dumps(rows, sort_keys=True).encode())
    (HERE / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(dict(files=len(rows), snapshot_sha256=manifest['snapshot_sha256'])))


if __name__ == '__main__':
    main()

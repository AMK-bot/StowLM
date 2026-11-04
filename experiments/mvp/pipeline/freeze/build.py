"""Read-only pipeline inventory. The CLI writes a manifest only at freeze time."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from experiments.mvp.pipeline.freeze import imports
from experiments.mvp.pipeline.freeze import product_outputs
from experiments.mvp.pipeline.freeze import runtime_outputs
from experiments.mvp.pipeline.freeze.paths import contained_output

PIPELINE = 'experiments/mvp/pipeline'
FREEZE = PIPELINE + '/freeze'
ENGINE = 'experiments/mvp/frontier/engine'
REVIEW = ENGINE + '/source-manifest-0.13.0-review-fixes.json'
FINAL = ENGINE + '/source-manifest-0.13.0-final.json'
NATIVE = ENGINE + '/native-source-manifest.json'
MODEL = 'models/glimmer-30b-4bit'
TIMING = PIPELINE + '/reader_final/readings/timing.json'
INTAKE = PIPELINE + '/intake_lora'
SOURCE_ROOTS = (PIPELINE, 'app', 'src')
WEIGHT_SUFFIXES = {'.safetensors', '.bin', '.pt', '.pth', '.npz', '.gguf'}
SKIP_DIRS = {'runs', 'measurements', '__pycache__', '.pytest_cache', '.mypy_cache',
             '.ruff_cache', '.cache', 'caches', 'node_modules', '.git', '.venv', '.keys'}
SUPPLEMENT_ROOTS = (ENGINE, 'params', 'data')
SCOPES = ('pipeline', 'film')
FILM_ROOTS = ('app', 'experiments/mvp/ui')
SCAN_SCRATCH = PIPELINE + '/repo/process-check'


def in_scope(name, scope, *, import_closure=()):
    if scope not in SCOPES:
        raise ValueError(f'unknown freeze scope: {scope}')
    if product_outputs.excluded_all(name):
        return False
    return scope == 'film' or (not any(name == prefix or name.startswith(prefix + '/') for prefix in FILM_ROOTS)
                               and not product_outputs.excluded_pipeline(name, import_closure=import_closure))


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(files):
    return hashlib.sha256(''.join(f'{name}\0{value}\n' for name, value in sorted(files.items())).encode()).hexdigest()


def _json(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _safe(root, name, checked=None):
    path = Path(name)
    path = path if path.is_absolute() else root / path
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError(f'path outside repository: {name}') from None
    if '..' in relative.parts:
        raise ValueError(f'noncanonical path: {name}')
    # The pipeline/sealed folder contains executable harness code; never descend
    # into any other sealed folder, or follow symlinks to sealed/private bytes.
    if 'sealed' in relative.parts and relative.parts[:4] != ('experiments', 'mvp', 'pipeline', 'sealed'):
        raise ValueError(f'sealed data excluded: {name}')
    for parent in (path, *path.parents):
        if checked is not None and parent in checked:
            break
        if parent.is_symlink():
            raise ValueError(f'symlink excluded: {name}')
        if checked is not None and parent != path:
            checked.add(parent)
        if parent == root:
            break
    return relative.as_posix()


def _walk(root, name, *, source=False, allowed_suffixes=None, symlinks=None, runtime_dirs=()):
    start = root / name
    # Repository audit tooling and its generated corpora are excluded in both
    # layers, including explicit import-graph paths, by the user's scope rule.
    relative = start.relative_to(root).as_posix()
    if product_outputs.excluded_all(relative) or runtime_outputs.contains(relative, runtime_dirs):
        return
    _safe(root, name)
    if not start.exists():
        return
    checked = {root}
    for directory, dirs, names in os.walk(start, followlinks=False):
        here = Path(directory)
        rel = here.relative_to(root).as_posix()
        admitted = sorted(d for d in dirs if d not in SKIP_DIRS
                         and not product_outputs.excluded_all((here / d).relative_to(root).as_posix())
                         and not runtime_outputs.contains((here / d).relative_to(root).as_posix(), runtime_dirs)
                         and not d.startswith(('unit-tmp', 'test-tmp', 'pytest-', '.pytest-')) and 'cache' not in d.lower()
                         and not (d == 'results' and (rel == ENGINE or rel.startswith(ENGINE + '/')))
                         and (d != 'sealed' or rel == PIPELINE))
        for name in admitted:
            _safe(root, here / name, checked)
        dirs[:] = admitted
        if rel.startswith(FREEZE):
            dirs[:] = [d for d in dirs if d == 'fixtures']
        for name in sorted(names):
            path = here / name
            if allowed_suffixes is not None and path.suffix not in allowed_suffixes:
                continue
            from experiments.mvp.pipeline.freeze.evidence import private_path
            if private_path(path.relative_to(root)):
                continue
            leaf_link = source and path.is_symlink()
            if not leaf_link:
                _safe(root, path, checked)
                if not path.is_file():
                    continue
            # Repository scan inventories are generated caches, not launch inputs.
            if rel == PIPELINE + '/repo' and path.suffix in {'.nul', '.bin', '.jsonl', '.txt', '.json'}:
                continue
            # Gate/result summaries are pinned; incremental scoring streams are measurements.
            if rel.startswith(INTAKE + '/results/') and path.name.endswith('.predictions.jsonl'):
                continue
            if path.suffix in {'.log', '.pid', '.lock', '.pyc', '.pyo'} or path.name in {
                'chain-status.jsonl', 'claude-gpu-lease.json', 'gpu-started.json', 'launch.claim'}:
                continue
            if source and (path.suffix in WEIGHT_SUFFIXES or path.suffix in {'.pyc', '.pyo'}):
                continue
            if rel == FREEZE and path.suffix not in {'.py', '.sh', '.md'}:
                continue
            # Harness code is public; its held-out inputs and output are not.
            if rel.startswith(PIPELINE + '/sealed') and path.suffix not in {'.py', '.sh', '.md'}:
                continue
            if leaf_link:
                if symlinks is not None:
                    symlinks.add(path.relative_to(root).as_posix())
                continue
            yield path.relative_to(root).as_posix()


def source_paths(root, *, import_closure=None, symlinks=None, scope='pipeline', runtime_dirs=None):
    root = Path(root).resolve()
    graph = imports.discover(root)['paths'] if import_closure is None else import_closure
    policy = runtime_outputs.load(root, import_closure=graph) if runtime_dirs is None else runtime_outputs.validate(
        root, {'directories': list(runtime_dirs)}, import_closure=graph, check_contents=False)
    runtime_dirs = policy['directories']
    roots = (*SOURCE_ROOTS, *SUPPLEMENT_ROOTS, *(('experiments/mvp/ui',) if scope == 'film' else ()))
    names = {name for prefix in roots if in_scope(prefix, scope) for name in _walk(root, prefix, source=True, symlinks=symlinks, runtime_dirs=runtime_dirs)}
    names.update(_safe(root, name) for name in graph if in_scope(name, scope, import_closure=graph))
    def flat_python(prefix):
        for path in (root / prefix).glob('*.py'):
            if path.is_symlink():
                if symlinks is not None:
                    symlinks.add(path.relative_to(root).as_posix())
            else:
                names.add(_safe(root, path))
    for prefix in ('experiments/mvp/reader', 'experiments/twostep', 'experiments/mvp/frontier',
                   'experiments/mvp/hybrid'):
        if (root / prefix).is_dir():
            flat_python(prefix)
    # Legacy strategy runtime imports need Python, not its training/evaluation outputs.
    names.update(_walk(root, 'experiments/mvp/strategy', source=True, allowed_suffixes={'.py'}, symlinks=symlinks))
    for prefix in ('experiments/mvp/reader/v3',):
        if (root / prefix).is_dir():
            flat_python(prefix)
    for name in ('audio.py', 'asr.py', 'flow.py'):
        path = 'experiments/mvp/voice/' + name
        if (root / path).is_file():
            names.add(_safe(root, path))
    if (root / runtime_outputs.CONFIG).is_file():
        names.add(_safe(root, runtime_outputs.CONFIG))
    from experiments.mvp.pipeline.freeze.evidence import private_path
    return sorted(name for name in names if not private_path(name) and in_scope(name, scope, import_closure=graph)
                  and not runtime_outputs.contains(name, runtime_dirs))


def collect_source_files(root, *, scope='pipeline', runtime_dirs=None):
    root = Path(root).resolve()
    return {name: sha256_file(root / name) for name in source_paths(root, scope=scope, runtime_dirs=runtime_dirs)}


def _engine_pins(root):
    review = _json(root / REVIEW)
    pins = {}
    for name, digest in review.get('source_files', {}).items():
        key = name if name.startswith(('src/', 'params/', 'experiments/')) else ENGINE + '/' + name
        pins[_safe(root, key)] = digest
    for name, digest in _json(root / NATIVE).items():
        if isinstance(digest, str):
            pins[_safe(root, name)] = digest
    return review, dict(sorted(pins.items()))


def _reader_paths(root):
    identity = _json(root / TIMING).get('identity', {})
    folder = identity.get('adapter_path')
    names = []
    if folder:
        prefix = _safe(root, folder)
        names.extend(_walk(root, prefix))
    for name in ('decoder.py', 'domain_words.py'):
        path = 'experiments/mvp/reader/v3/' + name
        if (root / path).is_file():
            names.append(path)
    frozen = 'experiments/mvp/reader/v3/results/final-freeze.json'
    if (root / frozen).is_file():
        names.append(_safe(root, frozen))
    return identity, sorted(set(names))


def inventory_paths(root, no_weights=True, *, import_closure=None, evidence_selection=None, scope='pipeline', runtime_dirs=None):
    """Selected file names without hashing; used by the fast launch verifier."""
    root = Path(root).resolve()
    import_closure = imports.discover(root)['paths'] if import_closure is None else import_closure
    policy = runtime_outputs.load(root, import_closure=import_closure) if runtime_dirs is None else runtime_outputs.validate(
        root, {'directories': list(runtime_dirs)}, import_closure=import_closure, check_contents=False)
    runtime_dirs = policy['directories']
    names = set(source_paths(root, import_closure=import_closure, scope=scope, runtime_dirs=runtime_dirs))
    from experiments.mvp.pipeline.freeze import evidence
    evidence_selection = evidence.inventory(root, scope=scope) if evidence_selection is None else evidence_selection
    names.update(_safe(root, name) for name in evidence_selection['paths'] if in_scope(name, scope)
                 and not runtime_outputs.contains(name, runtime_dirs))
    if (root / runtime_outputs.CONFIG).is_file():
        names.add(_safe(root, runtime_outputs.CONFIG))
    _, pins = _engine_pins(root)
    names.update(pins)
    names.update(name for name in (REVIEW, FINAL, NATIVE, ENGINE + '/__init__.py',
                                  'pyproject.toml', 'uv.lock') if (root / name).is_file())
    names.update(_walk(root, MODEL))
    _, reader = _reader_paths(root)
    names.update(reader)
    # Source inventory omits weights; adapter directories must still be pinned.
    intake = _intake_status(root)
    if intake['status'] == 'adopted':
        names.update(_walk(root, INTAKE + '/adapter-intake-v1'))
    names.update(p.relative_to(root).as_posix() for p in sorted((root / 'experiments/mvp/postfreeze').glob('0[1-5]-*.diff')))
    # Suite results are evidence, not part of the source tree or its digest.
    suites = root / FREEZE / 'suites' / scope
    if scope == 'film' and suites.is_dir():
        names.update(p.relative_to(root).as_posix() for p in suites.iterdir()
                     if p.is_file() and not p.is_symlink() and p.suffix in {'.json', '.xml'})
    return sorted(name for name in names if in_scope(name, scope, import_closure=import_closure)
                  and not runtime_outputs.contains(name, runtime_dirs) and (not no_weights or not (
        name.startswith(MODEL + '/') and Path(name).suffix in WEIGHT_SUFFIXES)))


def collect_inventory(root, no_weights=False, *, import_closure=None, evidence_selection=None, scope='pipeline', runtime_dirs=None):
    root = Path(root).resolve()
    return {name: sha256_file(root / name) for name in inventory_paths(root, no_weights, import_closure=import_closure, evidence_selection=evidence_selection, scope=scope, runtime_dirs=runtime_dirs) if (root / name).is_file()}


def inventory_symlinks(root, *, import_closure=None, evidence_selection=None, scope='pipeline', runtime_dirs=None):
    """Selected data leaf links, separate from independently pinned regular files."""
    root = Path(root).resolve()
    import_closure = imports.discover(root)['paths'] if import_closure is None else import_closure
    policy = runtime_outputs.load(root, import_closure=import_closure) if runtime_dirs is None else runtime_outputs.validate(
        root, {'directories': list(runtime_dirs)}, import_closure=import_closure, check_contents=False)
    runtime_dirs = policy['directories']
    from experiments.mvp.pipeline.freeze import evidence
    selected = set()
    source_paths(root, import_closure=import_closure, symlinks=selected, scope=scope, runtime_dirs=runtime_dirs)
    evidence_selection = evidence.inventory(root, scope=scope) if evidence_selection is None else evidence_selection
    selected.update(evidence_selection.get('symlink_paths', []))
    return sorted(name for name in selected if in_scope(name, scope, import_closure=import_closure or ())
                  and not runtime_outputs.contains(name, runtime_dirs))


def git_paths(text):
    """Decode Git's C-quoted UTF-8 byte strings, including octal escapes."""
    tokens = []
    position = 0
    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        match = re.match(r'"(?:[^"\\]|\\.)*"|[^\s"]+', text[position:])
        if match is None:
            raise ValueError('invalid git quoted path')
        tokens.append(match.group())
        position += match.end()
        if position < len(text) and not text[position].isspace():
            raise ValueError('ambiguous git path boundary')
    result = []
    for token in tokens:
        if not token.startswith('"'):
            if '"' in token or '\\' in token:
                raise ValueError('ambiguous unquoted git path')
            result.append(token)
            continue
        data = bytearray()
        i = 1
        while i < len(token) - 1:
            char = token[i]
            if char != '\\':
                data.extend(char.encode('utf-8')); i += 1
                continue
            i += 1
            if token[i] in '01234567':
                octal = token[i:i + 3]
                if len(octal) != 3 or any(c not in '01234567' for c in octal):
                    raise ValueError('invalid git octal escape')
                data.append(int(octal, 8)); i += 3
            else:
                escape = {'a': '\a', 'b': '\b', 't': '\t', 'n': '\n', 'v': '\v', 'f': '\f', 'r': '\r', '\\': '\\', '"': '"'}
                if token[i] not in escape:
                    raise ValueError('invalid git escape')
                data.extend(escape[token[i]].encode()); i += 1
        result.append(data.decode('utf-8'))
    return result


def diff_paths(path):
    """Both sides of unified and git rename headers count as touched."""
    paths = set()
    for line in Path(path).read_text().splitlines():
        if line.startswith(('--- ', '+++ ')):
            text = line[4:].split('\t', 1)[0]
            fields = git_paths(text) if text.startswith('"') else [text]
        elif line.startswith('diff --git '):
            fields = git_paths(line[len('diff --git '):])
            if len(fields) != 2:
                raise ValueError('ambiguous diff --git path header')
        elif line.startswith(('rename from ', 'rename to ')):
            text = line.split(' ', 2)[2]
            fields = git_paths(text) if text.startswith('"') else [text]
        else:
            continue
        for name in fields:
            if name != '/dev/null':
                paths.add(name[2:] if name.startswith(('a/', 'b/')) else name)
    return sorted(paths)


def _intake_status(root):
    results = root / INTAKE / 'results/sealed-final'
    gates = _json(results / 'gates.json')
    adoption = _json(results / 'adoption.json')
    if gates.get('decision') == 'cut' or gates.get('all_pass') is False:
        return {'status': 'cut', 'reason': gates.get('reason', 'failed intake gate'),
                'gate_path': INTAKE + '/results/sealed-final/gates.json', 'gate': gates,
                'fallback': 'Spark intake'}
    if gates.get('all_pass') is True and gates.get('decision') in {'eligible', 'adopted'} and adoption:
        return {'status': 'adopted', 'gate_path': INTAKE + '/results/sealed-final/gates.json',
                'gate': gates, 'adoption': adoption, 'adapter_path': INTAKE + '/adapter-intake-v1'}
    return {'status': 'pending', 'reason': 'complete intake gate and adoption record not available',
            'fallback': 'Spark intake'}


PROMPT_SOURCES = {
    'intake': [PIPELINE + '/intake/interpretation.py', PIPELINE + '/intake/spark_reader.py'],
    'explain': [PIPELINE + '/explain/render.py', PIPELINE + '/explain/summary.py', 'src/llm/spark/explain.py'],
    'copilot': [PIPELINE + '/copilot/agent.py'],
    'zero_shot_planner': ['src/llm/spark/planner.py', 'src/llm/stowlm/format_prompt.py', 'src/llm/spark/rules_appendix.py'],
}


def _prompts(root, files):
    result = {}
    for stage, sources in PROMPT_SOURCES.items():
        records = []
        for name in sources:
            if name not in files:
                continue
            text = (root / name).read_text()
            tree = ast.parse(text)
            templates = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    names = [t.id for t in targets if isinstance(t, ast.Name)]
                    if any('prompt' in n.lower() or n in {'GRAMMAR', 'job'} for n in names):
                        entry = {'name': ','.join(names), 'line': node.lineno,
                                 'template_sha256': hashlib.sha256(ast.get_source_segment(text, node).encode()).hexdigest()}
                        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                            entry['text_sha256'] = hashlib.sha256(node.value.value.encode()).hexdigest()
                        templates.append(entry)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and ('prompt' in node.name or node.name == 'rules_appendix'):
                    entry = {'name': node.name, 'line': node.lineno,
                             'template_sha256': hashlib.sha256(ast.get_source_segment(text, node).encode()).hexdigest()}
                    returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
                    if len(returns) == 1 and isinstance(returns[0].value, ast.Constant) and isinstance(returns[0].value.value, str):
                        entry['text_sha256'] = hashlib.sha256(returns[0].value.value.encode()).hexdigest()
                    templates.append(entry)
                elif isinstance(node, ast.Call) and node.args:
                    called = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
                    literals = [n.value for n in ast.walk(node.args[0]) if isinstance(n, ast.Constant)
                                and isinstance(n.value, str) and len(n.value) >= 40]
                    if called == 'complete' and literals:
                        templates.append({'name': 'complete request', 'line': node.lineno,
                                          'template_sha256': hashlib.sha256(ast.get_source_segment(text, node.args[0]).encode()).hexdigest(),
                                          'literal_text_sha256': hashlib.sha256(''.join(literals).encode()).hexdigest()})
            records.append({'path': name, 'source_sha256': files[name],
                            'templates': sorted(templates, key=lambda item: (item['line'], item['name']))})
        result[stage] = {'sources': records, 'sha256': tree_digest({row['path']: row['source_sha256'] for row in records}),
                         'method': 'AST prompt templates, exact static text when available, and full assembly source; runtime context is pinned separately'}
    return result


def _git(root):
    def run(*args):
        result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=False)
        return result.stdout.rstrip('\n') if result.returncode == 0 else None
    status = run('status', '--porcelain')
    modified = set()
    for line in status.splitlines() if status else []:
        text = line[3:]
        if text.startswith('"'):
            modified.update(name for name in git_paths(text) if name != '->')
        else:
            modified.update(text.split(' -> '))
    return {'head': run('rev-parse', 'HEAD'), 'status_porcelain': status.splitlines() if status else [],
            'modified_files': sorted(modified),
            'diff_stat': run('diff', '--stat')}


def _standard_profile(root):
    path = root / PIPELINE / 'runner/profiles.py'
    if not path.is_file():
        return {}
    # Reading the selected profile instantiates only a Config dataclass and
    # checks engine identity. No solver, reader or API entry point is called.
    script = '''import importlib.util,json,sys
from dataclasses import asdict,is_dataclass
from pathlib import Path
root=Path(sys.argv[1])
sys.path[:0]=[str(root),str(root/'src'),str(root/'experiments/mvp/frontier')]
spec=importlib.util.spec_from_file_location('_freeze_profiles',root/'experiments/mvp/pipeline/runner/profiles.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
value=module.get_profile('standard')
print(json.dumps({k:asdict(v) if is_dataclass(v) else v for k,v in value.items()},sort_keys=True))
'''
    result = subprocess.run([sys.executable, '-I', '-c', script, str(root)], cwd=root,
                            capture_output=True, text=True, check=False, timeout=20)
    if result.returncode:
        raise ValueError('standard profile unavailable: ' + result.stderr.strip())
    return json.loads(result.stdout)


def _pipeline_layer(root, manifest_path):
    from experiments.mvp.pipeline.freeze.verify import require_frozen
    name = _safe(root, manifest_path or FREEZE + '/manifest-pipeline.json')
    if not name.startswith(FREEZE + '/') or Path(name).suffix != '.json':
        raise ValueError('pipeline manifest must be a JSON file inside freeze')
    path = root / name
    identity = require_frozen(path, root=root)
    value = _json(path)
    if value.get('scope') != 'pipeline':
        raise ValueError('film freeze requires a frozen pipeline-scope manifest')
    if sha256_file(path) != identity['manifest_sha256']:
        raise ValueError('pipeline manifest changed during film build')
    return {'path': name, **identity}, value


def build_manifest(root, no_weights=False, *, scope='pipeline', pipeline_manifest=None):
    root = Path(root).resolve()
    in_scope('', scope)
    pipeline_layer, pipeline_pins = _pipeline_layer(root, pipeline_manifest) if scope == 'film' else (None, {})
    from experiments.mvp.pipeline.freeze import evidence
    graph = imports.discover(root)
    graph['paths'] = [name for name in graph['paths'] if in_scope(name, scope, import_closure=graph['paths'])]
    runtime_policy = runtime_outputs.load(root, import_closure=graph['paths'])
    runtime_dirs = runtime_policy['directories']
    selected_sources = source_paths(root, import_closure=graph['paths'], scope=scope, runtime_dirs=runtime_dirs)
    evidence_selection = evidence.inventory(root, scope=scope)
    files = collect_inventory(root, no_weights or scope == 'film', import_closure=graph['paths'], evidence_selection=evidence_selection, scope=scope, runtime_dirs=runtime_dirs)
    if scope == 'film' and not no_weights:
        files.update({name: digest for name, digest in pipeline_pins['files'].items()
                      if name.startswith(MODEL + '/') and Path(name).suffix in WEIGHT_SUFFIXES})
    sources = {name: files[name] for name in selected_sources if name in files}
    from experiments.mvp.pipeline.freeze import links
    symlinks, link_errors = {}, []
    for name in inventory_symlinks(root, import_closure=graph['paths'], evidence_selection=evidence_selection, scope=scope, runtime_dirs=runtime_dirs):
        try:
            symlinks[name] = links.inspect(root, name, files, hashes=files, import_closure=graph['paths'])
        except (OSError, ValueError, RuntimeError) as error:
            link_errors.append(str(error))
    review, pins = _engine_pins(root)
    changed = sorted(name for name, expected in pins.items() if files.get(name) != expected)
    unchanged = bool(pins) and not changed and review.get('version') == '0.13.0'
    verdicts = []
    for path in sorted((root / 'experiments/mvp/postfreeze').glob('0[1-5]-*.diff')):
        name = path.relative_to(root).as_posix()
        touched = diff_paths(path)
        overlap = sorted(set(touched) & (set(pins) | {REVIEW, FINAL, NATIVE}))
        verdicts.append({'path': name, 'sha256': files[name], 'touched_files': touched,
                         'pinned_touches': overlap, 'safe': unchanged and not overlap,
                         'reason': 'engine unchanged and no pinned file touched' if unchanged and not overlap
                         else 'diff touches engine pins' if overlap else 'current engine differs from fixed manifest'})
    identity, reader_names = _reader_paths(root)
    errors = list(evidence_selection['errors']) + link_errors + evidence.validate_fill_sources(evidence_selection, set(files) | set(symlinks))
    if not unchanged:
        errors.append('engine differs from review-fixes identity or required pins are missing')
    if not identity or not identity.get('adapter_path'):
        errors.append('reader identity is missing')
    checks = {}
    for key, name in (('decoder_sha256', 'experiments/mvp/reader/v3/decoder.py'),
                      ('domain_words_sha256', 'experiments/mvp/reader/v3/domain_words.py')):
        checks[key] = bool(identity.get(key)) and files.get(name) == identity.get(key)
    if identity.get('adapter_path'):
        weight = _safe(root, identity['adapter_path']) + '/adapters.safetensors'
        checks['adapter_sha256'] = bool(identity.get('adapter_sha256')) and files.get(weight) == identity.get('adapter_sha256')
        if not (root / weight).is_file():
            checks['adapter_sha256'] = False
    if not all(checks.values()) or not checks:
        errors.append('reader weights, decoder or domain identity does not match timing.json')
    intake = _intake_status(root)
    if intake['status'] == 'pending':
        errors.append('intake gate is pending; freeze requires adopted or explicitly cut')
    if intake['status'] == 'adopted':
        weight = INTAKE + '/adapter-intake-v1/adapters.safetensors'
        expected = intake['adoption'].get('adapter_sha256')
        if not expected or not (root / weight).is_file() or files.get(weight) != expected:
            errors.append('intake adapter differs from adoption record')
        if any(intake['gate'].get(key) is not True for key in ('I1', 'I2', 'I3', 'I4')):
            errors.append('intake adoption requires all four recorded gates')
        for name, digest in intake['adoption'].get('files_sha256', {}).items():
            key = INTAKE + '/results/sealed-final/' + name
            if files.get(key) != digest:
                errors.append('intake adoption result changed: ' + key)
    model_names = sorted(_walk(root, MODEL))
    weights = [name for name in model_names if Path(name).suffix in WEIGHT_SUFFIXES]
    if not (root / MODEL / 'config.json').is_file() or not weights:
        errors.append('base model config or weight shards are missing')
    index = _json(root / MODEL / 'model.safetensors.index.json').get('weight_map', {})
    for shard in sorted(set(index.values())):
        if MODEL + '/' + shard not in weights:
            errors.append('base model indexed shard missing: ' + shard)
    all_weights = sorted(name for name in inventory_paths(root, False, import_closure=graph['paths'], evidence_selection=evidence_selection, scope=scope, runtime_dirs=runtime_dirs) if Path(name).suffix in WEIGHT_SUFFIXES)
    weight_stats = {}
    for name in all_weights:
        path = root / name
        _safe(root, name)
        stat = path.stat()
        weight_stats[name] = {'exists': True, 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
    fixture_names = [name for name in sources if '/fixtures/' in name or '/lattice/store/' in name
                     or name.endswith('/lattice/hero.json') or '/copilot/replays/' in name]
    manifest = {'schema_version': 'stowlm.pipeline-freeze/1', 'created_utc': datetime.now(timezone.utc).isoformat(),
            'scope': scope,
            'task': '2.1', 'status': 'dry_run' if no_weights else 'frozen',
            'dry_run': bool(no_weights), 'files': files, 'source_files': sources, 'tree_sha256': tree_digest(sources),
            'symlinks': symlinks,
            'selection': {'source_roots': [name for name in SOURCE_ROOTS if in_scope(name, scope)] + (['experiments/mvp/ui'] if scope == 'film' else []), 'excluded_dirs': sorted(SKIP_DIRS),
                          'runtime_outputs': runtime_policy,
                          'scope': scope, 'excluded_scope_roots': list(FILM_ROOTS) if scope == 'pipeline' else [],
                          'product_outputs': {'roots': list(product_outputs.ROOTS),
                                              'patterns': list(product_outputs.ROOT_PATTERNS),
                                              'files': list(product_outputs.FILES),
                                              'policy': 'pipeline excludes generated data; code remains pinned; film pins generated data and its pipeline binding'},
                          'excluded_scan_scratch_roots': list(product_outputs.EXCLUDED_ROOTS),
                          'excluded_report_roots': list(product_outputs.EXCLUDED_ROOTS),
                          'supplement_roots': list(SUPPLEMENT_ROOTS), 'import_closure': graph,
                          'import_closure_additions': sorted(name for name in graph['paths'] if not any(name == prefix or name.startswith(prefix + '/') for prefix in SOURCE_ROOTS)),
                          'excluded_dir_patterns': ['*cache*', 'unit-tmp*', 'test-tmp*', 'pytest-*', '.pytest-*'],
                          'excluded_file_suffixes': ['.log', '.pid', '.lock', '.pyc', '.pyo'],
                          'excluded_volatile_files': ['chain-status.jsonl', 'claude-gpu-lease.json', 'gpu-started.json', 'launch.claim'],
                          'excluded_measurement_patterns': [INTAKE + '/results/**/*.predictions.jsonl', ENGINE + '/results/**'],
                          'excluded_scan_cache_patterns': [PIPELINE + '/repo/*' + suffix for suffix in ('.nul', '.bin', '.jsonl', '.txt', '.json')],
                          'source_weight_suffixes': sorted(WEIGHT_SUFFIXES),
                          'sealed_data': 'excluded; public pipeline/sealed harness code only',
                          'freeze_outputs': 'excluded except suites XML/JSON evidence and fixtures'},
            'model_weight_files': all_weights,
            'weight_stats': weight_stats,
            'engine': {'version': review.get('version'), 'review_manifest': REVIEW, 'final_manifest': FINAL,
                       'review_manifest_sha256': files.get(REVIEW), 'final_manifest_sha256': files.get(FINAL),
                       'review_check': {'unchanged': unchanged, 'drifts': changed, 'pinned_files': pins}},
            'base_model': {'path': MODEL, 'config_sha256': files.get(MODEL + '/config.json'),
                           'weight_files': {name: files.get(name) for name in weights},
                           'weights_hashed': not no_weights and scope == 'pipeline',
                           'weights_inherited': not no_weights and scope == 'film'},
            'adapters': {'reader': {'identity': identity, 'checks': checks,
                                    'files': {name: files[name] for name in reader_names if name in files}}, 'intake': intake},
            'prompts': _prompts(root, files),
            'profiles': {name: value for name, value in files.items() if name == PIPELINE + '/runner/fixtures/profile-selection.json'},
            'fixtures': {name: files[name] for name in fixture_names},
            'postfreeze_diffs': verdicts, 'git': _git(root), 'readiness': {'ready': not errors, 'errors': errors}}
    manifest['standard_profile'] = _standard_profile(root)
    manifest['artifacts'] = {'engine': sorted(name for name in files if name.startswith(ENGINE + '/') or name in pins),
                             'adapter': sorted(name for name in all_weights if not name.startswith(MODEL + '/')),
                             'prompt': sorted({row['path'] for stage in manifest['prompts'].values() for row in stage['sources']})}
    manifest['evidence'] = dict(evidence_selection, **evidence.describe(root, files, manifest,
        pipeline_manifest=pipeline_pins if scope == 'film' else None))
    if pipeline_layer is not None:
        bindings = evidence.product_output_bindings(root, files, pipeline_layer['manifest_sha256'],
                                                   pipeline_manifest=pipeline_pins)
        manifest['evidence']['product_output_bindings'] = bindings
        errors.extend(bindings['errors'])
        manifest['readiness'] = {'ready': not errors, 'errors': errors}
        current_layer, _ = _pipeline_layer(root, pipeline_layer['path'])
        if current_layer != pipeline_layer:
            raise ValueError('pipeline manifest changed during film build')
        manifest['pipeline_layer'] = pipeline_layer
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument('--no-weights', action='store_true', help='skip base-model weights for a dry inventory; adapter hashes are still checked')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--scope', choices=SCOPES, default='pipeline')
    parser.add_argument('--pipeline-manifest', type=Path)
    args = parser.parse_args()
    args.root = args.root.resolve()
    try:
        output = contained_output(args.out or args.root / FREEZE / f'manifest-{args.scope}.json', args.root / FREEZE)
    except ValueError as error:
        parser.error(str(error))
    finals = {(args.root / FREEZE / name).resolve() for name in ('manifest.json', 'manifest-pipeline.json', 'manifest-film.json')}
    if args.no_weights and (args.out is None or output in finals):
        parser.error('--no-weights requires --out with a dry path other than any final manifest')
    try:
        manifest = build_manifest(args.root, no_weights=args.no_weights, scope=args.scope, pipeline_manifest=args.pipeline_manifest)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, str(error) + '\n')
    if not args.no_weights and not manifest['readiness']['ready']:
        parser.exit(1, '\n'.join(manifest['readiness']['errors']) + '\n')
    with output.open('x') as stream:
        stream.write(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + '\n')
    print(json.dumps({'out': str(output), 'files': len(manifest['files']), 'source_files': len(manifest['source_files']),
                      'scope': args.scope,
                      'bytes_hashed': sum((args.root / name).stat().st_size for name in manifest['files']),
                      'tree_sha256': manifest['tree_sha256'], 'readiness': manifest['readiness'],
                      'diffs': [{'path': row['path'], 'safe': row['safe']} for row in manifest['postfreeze_diffs']]}, sort_keys=True))


if __name__ == '__main__':
    main()

"""Explicit film evidence and honest provenance for stored replay inputs."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re

PIPELINE = 'experiments/mvp/pipeline'
_PATH = re.compile(r'(?:experiments/|app/|src/|params/|data/|docs/)[^\s\]\[{}()<>`\"\',;:#]+')
_VOLATILE = {'.log', '.pid', '.lock', '.pyc', '.pyo'}


def private_path(name):
    """Classify by path only; private key bytes must never be opened."""
    path = Path(name)
    lower = path.name.lower()
    public = lower == 'published-public-key.pem' or lower.endswith('.pub') or ('public' in lower and path.suffix.lower() == '.pem')
    return not public and ('.keys' in path.parts or 'private' in lower and ('key' in lower or path.suffix in {'.pem', '.key'}) or path.suffix.lower() in {'.key', '.pem'})


def safe_path(root, name, *, allow_leaf_link=False):
    root = Path(root).resolve()
    path = Path(name)
    path = path if path.is_absolute() else root / path
    try:
        rel = path.relative_to(root)
    except ValueError:
        raise ValueError(f'evidence path outside repository: {name}') from None
    if '..' in rel.parts:
        raise ValueError(f'noncanonical evidence path: {name}')
    from .product_outputs import excluded_all
    if excluded_all(rel.as_posix()):
        raise ValueError(f'excluded repository report: {rel.as_posix()}')
    if rel.parts[:3] == ('experiments', 'mvp', 'testv3') or ('sealed' in rel.parts and rel.parts[:4] != ('experiments', 'mvp', 'pipeline', 'sealed')):
        raise ValueError(f'sealed evidence excluded: {name}')
    if private_path(rel):
        raise ValueError(f'private key excluded: {name}')
    for parent in (path, *path.parents):
        if parent.is_symlink() and not (allow_leaf_link and parent == path):
            raise ValueError(f'symlink excluded: {name}')
        if parent == root:
            break
    return rel.as_posix()


def app_result_sources(root):
    """Resolve stored input paths from app/v6.py's AST; never import the app.

    Values are sets because fallback branches and comprehensions may choose more
    than one recorded input. A dynamic filename preserves its resolved prefix.
    """
    root = Path(root).resolve()
    path = root / 'app/v6.py'
    if not path.is_file():
        return {'paths': [], 'references': [], 'unresolved': []}
    safe_path(root, 'app/v6.py')
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError, UnicodeError) as error:
        return {'paths': [], 'references': [], 'unresolved': [
            {'path': 'app/v6.py', 'owner': 'p22-demo', 'reason': 'app input discovery failed: ' + str(error)}]}
    globals_ = {'ROOT': {''}, '__file__': {'app/v6.py'}}
    functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    definitions = {target.id: node.value for node in tree.body if isinstance(node, ast.Assign)
                   for target in node.targets if isinstance(target, ast.Name)}
    returns = {}

    def values(node, env):
        if node is None:
            return set()
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.Name):
            return env.get(node.id, globals_.get(node.id, set()))
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return set().union(*(values(item.value if isinstance(item, ast.Starred) else item, env) for item in node.elts)) if node.elts else set()
        if isinstance(node, ast.IfExp):
            return values(node.body, env) | values(node.orelse, env)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
            left, right = values(node.left, env), values(node.right, env)
            if isinstance(node.op, ast.Add):
                if isinstance(node.left, (ast.List, ast.Tuple, ast.ListComp, ast.GeneratorExp)) or isinstance(node.right, (ast.List, ast.Tuple, ast.ListComp, ast.GeneratorExp)):
                    return left | right
                return {a + b for a in left for b in right}
            if not right:
                return {a.rstrip('/') + '/' for a in left if a not in {'', '.', '/'}}
            return {(Path(a) / b).as_posix() if a else b for a in left for b in right}
        if isinstance(node, ast.JoinedStr):
            literal = ''
            for item in node.values:
                if isinstance(item, ast.Constant):
                    literal += item.value
                else:
                    # Dynamic suffix: preserve only a directory, not a guessed file.
                    return {literal.rsplit('/', 1)[0] + '/'} if '/' in literal else set()
            return {literal}
        if isinstance(node, ast.Attribute) and node.attr == 'parent':
            return {Path(value).parent.as_posix() for value in values(node.value, env)}
        if isinstance(node, ast.Subscript):
            items = values(node.value, env)
            sequence = definitions.get(node.value.id) if isinstance(node.value, ast.Name) else node.value
            if isinstance(sequence, (ast.Tuple, ast.List)) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
                try:
                    return values(sequence.elts[node.slice.value], env)
                except IndexError:
                    return set()
            return items if isinstance(node.value, ast.Name) and node.value.id.isupper() else set()
        if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
            scope = dict(env)
            for generator in node.generators:
                if isinstance(generator.target, ast.Name):
                    scope[generator.target.id] = values(generator.iter, scope)
            return values(node.elt, scope)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                name = node.func.id
                if name in {'tuple', 'list', 'next', 'Path'} and node.args:
                    return values(node.args[0], env)
                return returns.get(name, set())
            if isinstance(node.func, ast.Attribute):
                original = values(node.func.value, env)
                if node.func.attr in {'resolve', 'absolute'}:
                    return original
                if node.func.attr == 'with_name' and node.args:
                    return {(Path(a).parent / b).as_posix() for a in original for b in values(node.args[0], env)}
                if node.func.attr == 'with_suffix' and node.args:
                    return {str(Path(a).with_suffix(b)) for a in original for b in values(node.args[0], env)}
        return set()

    def assignments(nodes, env):
        # Iterate to resolve aliases defined before or after other constants.
        for _ in range(8):
            changed = False
            for node in nodes:
                target, value = None, None
                if isinstance(node, ast.Assign):
                    target, value = node.targets[0], node.value
                elif isinstance(node, ast.AnnAssign):
                    target, value = node.target, node.value
                elif isinstance(node, (ast.For, ast.comprehension)):
                    target, value = node.target, node.iter
                if isinstance(target, ast.Name) and target.id not in {'ROOT', '__file__'}:
                    found = values(value, env)
                    old = env.get(target.id, set())
                    if found - old:
                        env[target.id] = old | found
                        changed = True
            if not changed:
                break

    assignments(tree.body, globals_)
    scopes = {name: dict(globals_) for name in functions}
    for name, function in functions.items():
        for parameter, default in zip(function.args.args[-len(function.args.defaults):], function.args.defaults):
            scopes[name][parameter.arg] = values(default, globals_)
    for _ in range(8):
        changed = False
        for name, function in functions.items():
            env = scopes[name]
            nodes = list(ast.walk(function))
            assignments(nodes, env)
            for call in (node for node in nodes if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions):
                callee = functions[call.func.id]
                for parameter, argument in zip(callee.args.args, call.args):
                    found = values(argument, env)
                    old = scopes[callee.name].get(parameter.arg, set())
                    if found - old:
                        scopes[callee.name][parameter.arg] = old | found
                        changed = True
            found = set().union(*(values(node.value, env) for node in nodes if isinstance(node, ast.Return)))
            if found - returns.get(name, set()):
                returns[name] = returns.get(name, set()) | found
                changed = True
        if not changed:
            break
    references = set()
    for function_name, function in functions.items():
        env = scopes[function_name]
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            requested = set()
            if isinstance(node.func, ast.Name) and node.func.id in {'_json', 'read_optional', '_file_sha256', '_reading_rows', 'incumbents'} and node.args:
                requested = values(node.args[0], env)
            elif isinstance(node.func, ast.Attribute) and node.func.attr in {'read_text', 'read_bytes', 'open'}:
                if node.func.attr == 'open' and node.args and isinstance(node.args[0], ast.Constant) and any(mode in str(node.args[0].value) for mode in 'wax'):
                    continue
                requested = values(node.func.value, env)
            for name in requested:
                if name and (name.startswith(('experiments/', 'app/', 'src/', 'params/', 'data/', 'docs/')) or Path(name).is_absolute()):
                    references.add((name, function_name, node.lineno))
    rows, selected, unresolved = [], set(), []
    for name, function, line in sorted(references):
        # New live jobs are created after launch; they are not stored replays.
        if name.startswith('experiments/mvp/ui/hero-live'):
            continue
        try:
            relative = safe_path(root, name.rstrip('/'), allow_leaf_link=True)
        except ValueError as error:
            unresolved.append({'path': name, 'function': function, 'line': line, 'reason': str(error), 'owner': 'p22-demo'})
            continue
        if relative == '.':
            continue
        candidate = root / relative
        row = {'path': relative, 'function': function, 'line': line,
               'kind': 'prefix' if name.endswith('/') or candidate.is_dir() else 'file', 'exists': candidate.exists()}
        rows.append(row)
        if candidate.exists():
            selected.add(relative)
        else:
            unresolved.append(dict(row, owner='p22-demo', reason='optional or unresolved app input is absent'))
    return {'paths': sorted(selected), 'references': rows, 'unresolved': unresolved}


def _paths_in(value):
    if isinstance(value, str):
        return sorted(set(match.rstrip('.') for match in _PATH.findall(value)))
    if isinstance(value, dict):
        names = {name for item in value.values() for name in _paths_in(item)}
        for key in ('source', 'source_path', 'named_source', 'path'):
            item = value.get(key)
            if isinstance(item, str) and not _paths_in(item):
                candidate = item.split('#', 1)[0].split(':', 1)[0].split(',', 1)[0].strip().strip('`')
                if '/' in candidate or Path(candidate).suffix:
                    names.add(candidate)
        return sorted(names)
    if isinstance(value, list):
        return sorted({name for item in value for name in _paths_in(item)})
    return []


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError, UnicodeError):
        return None


def inventory(root, *, scope='pipeline'):
    """Select common pipeline evidence, or the film superset, without imports."""
    if scope not in {'pipeline', 'film'}:
        raise ValueError('unknown evidence scope: ' + str(scope))
    from . import product_outputs
    root = Path(root).resolve()
    names, symlinks, errors, fills, cited = set(), set(), [], {}, set()

    def film_path(name):
        return name == 'app' or name.startswith('app/') or name == 'experiments/mvp/ui' or name.startswith('experiments/mvp/ui/')

    def add(name, required=False):
        if scope == 'pipeline' and (film_path(str(name)) or product_outputs.excluded_pipeline(str(name))):
            return
        try:
            name = safe_path(root, name, allow_leaf_link=True)
        except ValueError as error:
            errors.append(str(error))
            return
        path = root / name
        if path.is_symlink():
            try:
                target = safe_path(root, path.resolve())
                if not (root / target).is_file():
                    raise ValueError('symlink target is not a regular file: ' + name)
                symlinks.add(name)
            except ValueError as error:
                errors.append(str(error))
            return
        if not path.exists():
            if required:
                errors.append('missing film source: ' + name)
            return
        if path.is_dir():
            for here, dirs, files in os.walk(path, followlinks=False):
                kept = []
                for directory in sorted(dirs):
                    child = Path(here) / directory
                    relative = child.relative_to(root).as_posix()
                    if product_outputs.excluded_all(relative):
                        continue
                    if scope == 'pipeline' and product_outputs.excluded_pipeline(relative):
                        continue
                    if directory in {'__pycache__', '.git', '.venv', 'node_modules'}:
                        continue
                    if directory == 'sealed' and relative != PIPELINE + '/sealed':
                        continue
                    if directory == '.keys':
                        # List names only and include explicitly public halves.
                        for key in child.iterdir():
                            if not private_path(key):
                                add(key.relative_to(root).as_posix())
                        continue
                    if child.is_symlink():
                        errors.append('symlink excluded: ' + relative)
                    else:
                        kept.append(directory)
                dirs[:] = kept
                for filename in sorted(files):
                    child = Path(here) / filename
                    if private_path(child) or child.suffix in _VOLATILE or filename == 'chain-status.jsonl':
                        continue
                    add(child.relative_to(root).as_posix())
        elif path.is_file():
            names.add(name)

    # Runtime results remain fixed while the separate film layer is assembled.
    prefixes = [
        'experiments/mvp/voice/runs/v5-1.3',
        'experiments/mvp/reader/schema.py', 'experiments/mvp/reader/parser_check.py',
        'experiments/mvp/reader/v3/parser', 'experiments/mvp/reader/v3/results-final',
        'experiments/mvp/rules.json',
    ]
    for feature in ('intake', 'explain', 'copilot', 'xray', 'sandbox', 'package', 'radio', 'lattice', 'runner'):
        prefixes.append(PIPELINE + '/' + feature + '/fixtures')
    prefixes.extend([PIPELINE + '/lattice/store', PIPELINE + '/lattice/hero.json', PIPELINE + '/copilot/replays'])
    for prefix in prefixes:
        add(prefix)
    for pattern in ('experiments/mvp/reader/v3/*prompt*', 'experiments/mvp/reader/v3/*adopt*',
                    PIPELINE + '/binding/measurements/deadline*',
                    PIPELINE + '/runner/measurements/profile-study*/summary*.json'):
        for path in sorted(root.glob(pattern)):
            add(path.relative_to(root).as_posix())
    for name in ('audio.py', 'asr.py', 'flow.py'):
        add('experiments/mvp/voice/' + name)
    if scope == 'pipeline':
        # Do not even parse film content in this scope. Its errors and citations
        # belong to the film manifest and cannot alter runtime readiness.
        names = {name for name in names if not film_path(name) and not product_outputs.excluded_pipeline(name)}
        symlinks = {name for name in symlinks if not film_path(name) and not product_outputs.excluded_pipeline(name)}
        return {'scope': scope, 'paths': sorted(names), 'symlink_paths': sorted(symlinks),
                'link_paths': sorted(symlinks), 'fill_sources': {}, 'cited_sources': [],
                'figure_sources': {}, 'unresolved_figures': [],
                'app_result_sources': {'paths': [], 'references': [], 'unresolved': []},
                'errors': sorted(set(errors)), 'validation': sorted(set(errors))}
    for name in ('app', 'experiments/mvp/ui', 'experiments/mvp/video/STORY-NEEDS-v6.md'):
        add(name)
    for name in product_outputs.film_paths(root):
        add(name)
    # Hero configuration chooses alternate package, trace, and reading recordings.
    for name in _paths_in(_read_json(root / 'app/v6_hero.json')):
        add(name)
    figure_sources, unresolved_figures = {}, []
    figure_file = 'experiments/mvp/ui/fixtures/film-figures.json'
    document = _read_json(root / figure_file)
    figures = document.get('figures', {}) if isinstance(document, dict) else {}
    if (root / figure_file).exists() and (not isinstance(document, dict) or not isinstance(figures, dict)):
        unresolved_figures.append({'figure': '<film-figures>', 'source': figure_file,
                                   'reason': 'film figures must be a JSON object with a figures mapping', 'owner': 'p22-demo'})
        figures = {}
    for figure, row in sorted(figures.items()):
        sources = _paths_in({key: row[key] for key in ('source', 'named_source', 'source_path') if key in row}) if isinstance(row, dict) else []
        figure_sources[figure] = sources
        if not sources:
            unresolved_figures.append({'figure': figure, 'source': None, 'reason': 'figure has no resolvable source file', 'owner': 'p22-demo'})
        for source in sources:
            try:
                checked = safe_path(root, source, allow_leaf_link=True)
                if not (root / checked).is_file():
                    raise ValueError('figure citation is not an existing file: ' + source)
            except ValueError as error:
                unresolved_figures.append({'figure': figure, 'source': source, 'reason': str(error), 'owner': 'p22-demo'})
                continue
            cited.add(source)
            add(source, required=True)
    errors.extend('p22-demo figure source unresolved: ' + str(row['figure']) + ': ' + row['reason'] for row in unresolved_figures)
    app_sources = app_result_sources(root)
    for name in app_sources['paths']:
        add(name)
    # Discover all fill locations without following links or opening sealed data.
    for prefix in ('app', 'experiments/mvp/ui', 'experiments/mvp/video'):
        start = root / prefix
        if not start.is_dir():
            continue
        for here, dirs, files in os.walk(start, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in {'sealed', '__pycache__', '.git', '.keys'} and not (Path(here) / d).is_symlink())
            if 'fill-v6.json' not in files:
                continue
            path = Path(here) / 'fill-v6.json'
            name = path.relative_to(root).as_posix()
            add(name, required=True)
            if path.is_symlink():
                continue
            document = _read_json(path)
            if not isinstance(document, dict):
                errors.append('fill file must be a JSON object: ' + name)
                continue
            fills[name] = {}
            for placeholder, value in sorted(document.items()):
                sources = _paths_in(value)
                fills[name][placeholder] = sources
                if not sources:
                    errors.append(f'fill value has no source path: {name}: {placeholder}')
                for source in sources:
                    cited.add(source)
                    add(source, required=True)
    result = {'scope': scope, 'paths': sorted(names), 'symlink_paths': sorted(symlinks), 'link_paths': sorted(symlinks),
              'fill_sources': fills, 'cited_sources': sorted(cited), 'figure_sources': figure_sources,
              'unresolved_figures': unresolved_figures, 'app_result_sources': app_sources}
    errors.extend(validate_fill_sources(result, names | symlinks))
    result['errors'] = result['validation'] = sorted(set(errors))
    return result


def validate_fill_sources(selection, pinned):
    pinned = set(pinned)
    errors = []
    for fill, placeholders in selection.get('fill_sources', {}).items():
        for placeholder, sources in placeholders.items():
            for source in sources:
                if source not in pinned and not any(name.startswith(source.rstrip('/') + '/') for name in pinned):
                    errors.append(f'unpinned fill source: {fill}: {placeholder}: {source}')
    return sorted(errors)


def _product_owner(bundle):
    if '/package/' in bundle:
        return 'p111-package'
    if '/sealed/' in bundle:
        return 'p112-sealed'
    return 'p32-showcases'


def product_output_bindings(root, files=None, pipeline_sha256=None, *, only=None, pipeline_manifest=None):
    """Check product receipts against the independently frozen pipeline layer.

    A matching recorded ``pipeline_sha256`` binds sibling payloads in the same
    logical bundle. Every explicit null or conflicting field still fails. A
    receipt from one sealed run cannot supply another run's missing binding.
    Suite reports are film evidence, not pipeline-produced bundles.
    """
    from . import product_outputs
    root = Path(root).resolve()
    errors, candidates, bundles = [], set(), {}
    lattice = (lattice_provenance(root, files, pipeline_manifest)
               if pipeline_manifest is not None and files is not None and only is None else {})
    initial = product_outputs.film_paths(root) if files is None else files
    has_products = any(product_outputs.binding_required(name) for name in initial)
    if only is None and not has_products:
        return {'pipeline_sha256': pipeline_sha256, 'ready': True, 'bundles': {}, 'errors': [],
                'lattice_provenance': lattice}
    if not isinstance(pipeline_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', pipeline_sha256):
        return {'pipeline_sha256': pipeline_sha256, 'ready': False, 'bundles': {},
                'errors': ['invalid frozen pipeline sha256 (owner p21-freeze)']}
    requested = None
    if only is not None:
        requested = set()
        for name in ([only] if isinstance(only, str) else only):
            try:
                name = safe_path(root, name)
                bundle = product_outputs.bundle_name(name)
                if not bundle or not product_outputs.binding_required(name):
                    raise ValueError('not a pipeline product bundle: ' + name)
                requested.add(bundle)
            except (TypeError, ValueError) as error:
                errors.append('invalid product selection (owner p21-freeze): ' + str(error))
        for bundle in requested:
            bundles[bundle] = {'files': [], 'bindings': []}
    if files is None:
        starts = set(product_outputs.film_paths(root))
        if requested is not None:
            starts.update(requested)
        for start in sorted(starts):
            if not product_outputs.binding_required(start):
                continue
            bundle = product_outputs.bundle_name(start)
            if requested is not None and bundle not in requested and not any(start.startswith(name + '/') for name in requested):
                continue
            try:
                safe_path(root, start)
            except ValueError as error:
                errors.append(f'unsafe product (owner {_product_owner(start)}): {start}: {error}')
                continue
            path = root / start
            if path.is_file():
                candidates.add(start)
            elif path.is_dir():
                for here, dirs, names in os.walk(path, followlinks=False):
                    dirs[:] = sorted(name for name in dirs if name not in {'__pycache__', '.keys', '.git'}
                                     and not name.startswith(('unit-tmp', 'test-tmp')))
                    for name in names:
                        child = Path(here) / name
                        if child.suffix not in _VOLATILE and not private_path(child):
                            candidates.add(child.relative_to(root).as_posix())
                if not any(name.startswith(start + '/') for name in candidates) and bundle:
                    bundles.setdefault(bundle, {'files': [], 'bindings': []})
    else:
        candidates.update(files)
    for name in sorted(candidates):
        if not product_outputs.binding_required(name):
            continue
        bundle = product_outputs.bundle_name(name)
        if not bundle or requested is not None and bundle not in requested:
            continue
        record = bundles.setdefault(bundle, {'files': [], 'bindings': []})
        record['files'].append(name)
        if Path(name).suffix not in {'.json', '.jsonl'}:
            continue
        owner = _product_owner(bundle)
        try:
            safe_path(root, name)
            path = root / name
            # Large plans and instances usually contain no pipeline reference.
            # Stream that check before decoding a receipt or event stream.
            with path.open('rb') as stream:
                found, tail = False, b''
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    chunk = tail + chunk
                    if b'pipeline_sha256' in chunk:
                        found = True
                        break
                    tail = chunk[-32:]
            if not found:
                continue
            if path.suffix == '.jsonl':
                documents = []
                with path.open() as stream:
                    for line_number, line in enumerate(stream, 1):
                        if 'pipeline_sha256' in line:
                            documents.append((f'line-{line_number}', json.loads(line)))
            else:
                documents = [('', json.loads(path.read_text()))]
            for location, document in documents:
                for pointer, key, value in _walk_values(document, location):
                    if key != 'pipeline_sha256':
                        continue
                    matches = value == pipeline_sha256
                    record['bindings'].append({'path': name, 'field': pointer,
                                               'recorded': value, 'matches': matches})
                    if not matches:
                        errors.append(f'product pipeline_sha256 mismatch (owner {owner}): {name}#{pointer}: expected {pipeline_sha256}; recorded {value!r}')
        except (OSError, UnicodeError, ValueError) as error:
            errors.append(f'product binding unreadable (owner {owner}): {name}: {error}')
    if not bundles and only is not None:
        errors.append('product binding missing (owner p32-showcases): no product output bundles selected')
    for bundle, record in sorted(bundles.items()):
        record['owner'] = _product_owner(bundle)
        if not record['files']:
            errors.append(f'product output missing (owner {record["owner"]}): {bundle}')
        if not any(row['matches'] for row in record['bindings']):
            errors.append(f'product pipeline_sha256 binding missing (owner {record["owner"]}): {bundle}')
    return {'pipeline_sha256': pipeline_sha256, 'ready': not errors,
            'bundles': bundles, 'errors': sorted(set(errors)), 'lattice_provenance': lattice}


def validate_product_outputs(root, pipeline_sha256, *, only=None):
    """Return product binding errors; suitable for the post-hero handoff."""
    return product_output_bindings(root, pipeline_sha256=pipeline_sha256, only=only)['errors']


def _walk_values(value, pointer=''):
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(item, (dict, list)) and not (isinstance(item, str) and item.startswith('{')) and key not in {
                'source_tree_sha256', 'tree_sha256', 'pipeline_sha256', 'reported_model', 'model', 'model_name',
                'engine_manifest_sha256', 'manifest_sha256', 'binding_source_sha256', 'binding_source_after_sha256',
                'planning_origin_binding_source_sha256', 'current_binding_source_sha256', 'source_sha256', 'adapter_sha256'}:
                continue
            path = pointer + '/' + str(key)
            yield path, key, item
            yield from _walk_values(item, path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_values(item, pointer + '/' + str(index))
    elif isinstance(value, str) and value.startswith('{'):
        try:
            embedded = json.loads(value)
        except ValueError:
            return
        yield from _walk_values(embedded, pointer + '/<json>')


def _json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _binding_digest(root, files):
    # Use the canonical stored-plan runtime closure, without importing the engine.
    from experiments.mvp.pipeline.lattice.binding_sources import runtime_source_files
    prefix = PIPELINE + '/binding/'
    sources = {name: files[prefix + name] for name in runtime_source_files(root / prefix) if prefix + name in files}
    return _json_digest(sources) if sources else None


def _lattice_expected(root, pipeline_manifest):
    """Derive identities from the verified layer, never film-side metadata."""
    pipeline_manifest = pipeline_manifest or {}
    pinned = pipeline_manifest.get('files', {})
    engine = pipeline_manifest.get('engine', {})
    review_path = engine.get('review_manifest', 'experiments/mvp/frontier/engine/source-manifest-0.13.0-review-fixes.json')
    engine_hash = pinned.get(review_path) or engine.get('review_manifest_sha256') or engine.get('final_manifest_sha256')
    binding_hash = _binding_digest(Path(root), pinned) if pinned else None
    binding_hash = binding_hash or pipeline_manifest.get('evidence', {}).get('binding_source_sha256') or pipeline_manifest.get('binding_source_sha256')
    return engine_hash, binding_hash


def lattice_provenance(root, files, pipeline_manifest):
    """Label every stored entry, including nested coverage results with no IDs.

    Missing or old engine/binding identities are historical evidence, not a
    reason to pretend a lattice plan was recomputed after the pipeline freeze.
    Operational summaries and catalogues without stored entries are not plans.
    """
    from . import product_outputs
    root = Path(root).resolve()
    expected_engine, expected_binding = _lattice_expected(root, pipeline_manifest)
    result = {}

    def entries(value, pointer='', *, force=False):
        if isinstance(value, dict):
            status = value.get('status')
            entry = (force or 'plan' in value and (isinstance(value['plan'], (dict, list)) or any(key in value for key in ('key', 'instance_sha256', 'canonical_rules', 'binding_evidence', 'engine_manifest_sha256')))
                     or 'key' in value and (isinstance(status, str) and status in {'checked_plan', 'stored', 'checked', 'complete', 'proved_refusal', 'constraint_unavailable', 'relaxed_plan', 'stored_plan'}
                                            or any(key in value for key in ('canonical_rules', 'gate', 'lifts', 'instance'))))
            if entry:
                yield pointer, value
                return
            for key, child in value.items():
                if key not in {'instance', 'plan', 'base_plan', 'engine_manifest', 'binding_evidence'}:
                    yield from entries(child, pointer + '/' + str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from entries(child, pointer + '/' + str(index), force=force)
        elif force:
            yield pointer, {}

    for name in sorted(files or {}):
        if not product_outputs.lattice_output(name) or Path(name).suffix not in {'.json', '.jsonl'}:
            continue
        safe_path(root, name)
        path = root / name
        force = name.startswith(product_outputs.LATTICE_STORE + '/') and path.name != 'catalogue.json'
        try:
            if path.suffix == '.jsonl':
                documents = []
                with path.open() as stream:
                    for number, line in enumerate(stream, 1):
                        if line.strip():
                            documents.append((f'/line-{number}', json.loads(line)))
            else:
                documents = [('', json.loads(path.read_text()))]
        except (OSError, UnicodeError, ValueError) as error:
            result[name] = {'label': 'computed on pre-freeze tree unknown', 'checks': [], 'mismatches': [],
                            'missing_fields': ['engine_manifest_sha256', 'binding_source_sha256'],
                            'parse_error': str(error), 'expected_identity_source': 'verified pipeline manifest'}
            continue
        for base, document in documents:
            for pointer, entry in entries(document, base, force=force):
                checks = []
                fields = [('engine_manifest_sha256', 'engine_manifest', expected_engine),
                          ('binding_source_sha256', 'binding_source', expected_binding)]
                fields.extend((key, 'binding_source', expected_binding) for key in (
                    'binding_source_after_sha256', 'planning_origin_binding_source_sha256', 'current_binding_source_sha256') if key in entry)
                for field, kind, expected in fields:
                    value = entry.get(field)
                    checks.append({'field': pointer + '/' + field, 'kind': kind, 'recorded': value,
                                   'expected': [expected] if expected else [],
                                   'matches': bool(expected) and isinstance(value, str) and value == expected})
                mismatches = [check for check in checks if not check['matches']]
                missing = [field for field in ('engine_manifest_sha256', 'binding_source_sha256') if not entry.get(field)]
                tree = next((entry.get(key) for key in ('source_tree_sha256', 'tree_sha256')
                             if isinstance(entry.get(key), str) and entry[key]), None)
                previous = sorted({str(check['recorded']) for check in mismatches if check['recorded']})
                identifier = tree or (previous[0] if len(previous) == 1 else _json_digest(previous) if previous else 'unknown')
                key = name + ('#' + pointer if pointer else '')
                result[key] = {'label': 'computed on pre-freeze tree ' + identifier if mismatches else 'computed on the frozen code',
                               'checks': checks, 'mismatches': mismatches, 'missing_fields': missing,
                               'entry_pointer': pointer, 'expected_identity_source': 'verified pipeline manifest',
                               'tree_id_basis': 'recorded tree identity' if tree else 'recorded component identity; complete historical tree not recorded'}
    return result


def describe(root, files, context, *, pipeline_manifest=None):
    """Compare recorded identities, without upgrading old or absent provenance."""
    root = Path(root).resolve()
    expected_context = pipeline_manifest if pipeline_manifest is not None else context
    engine = expected_context.get('engine', {})
    adapters = expected_context.get('adapters', {})
    reader = adapters.get('reader', {}).get('identity', {})
    intake = adapters.get('intake', {})
    adapter_hashes = {value for value in (reader.get('adapter_sha256'), intake.get('adoption', {}).get('adapter_sha256')) if value}
    binding = (_lattice_expected(root, pipeline_manifest)[1] if pipeline_manifest is not None
               else context.get('binding_source_sha256') or _binding_digest(root, files))
    prefix = PIPELINE + '/binding/'
    implementation_files = {Path(name).name: digest for name, digest in files.items()
                            if name.startswith(prefix) and Path(name).parent.as_posix() == prefix.rstrip('/')
                            and (name.endswith('.py') or name.endswith('/provenance-pins.json'))}
    implementation_digest = _json_digest(implementation_files) if implementation_files else None
    snapshot_files = [prefix + name + '.py' for name in ('adapter', 'hooks', 'admission', 'runtime', 'allocation')]
    snapshot_digest = None
    if all(name in files for name in snapshot_files):
        digest = hashlib.sha256()
        for name in sorted(snapshot_files):
            safe_path(root, name)
            digest.update(Path(name).name.encode())
            digest.update((root / name).read_bytes())
        snapshot_digest = digest.hexdigest()
    expected_engine = engine.get('review_manifest_sha256') or engine.get('final_manifest_sha256')
    provenance, reports, requests = {}, {}, {}
    fixture_paths = [name for name in files if ('/fixtures/' in name or '/lattice/store/' in name or '/copilot/replays/' in name
                    or '/ui/cache/' in name or '/voice/runs/' in name or name.endswith('/lattice/hero.json')) and Path(name).suffix in {'.json', '.jsonl'}]
    markers = (b'engine_manifest', b'binding_source', b'binding_snapshot', b'implementation',
               b'adapter_sha256', b'reported_model')
    for name in sorted(fixture_paths):
        if private_path(name):
            continue
        safe_path(root, name)
        path = root / name
        # Most large instance/plan files have no provenance; scan before decoding.
        with path.open('rb') as stream:
            found = False
            tail = b''
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                chunk = tail + chunk
                found |= any(marker in chunk for marker in markers)
                tail = chunk[-32:]
            if not found:
                continue
        if path.suffix == '.jsonl':
            documents = []
            with path.open() as stream:
                for line in stream:
                    try:
                        documents.append(json.loads(line))
                    except ValueError:
                        continue
        else:
            documents = [_read_json(path)]
        checks, reported, models, tree_ids = [], set(), set(), set()
        for pointer, key, value in _walk_values(documents):
            if key in {'source_tree_sha256', 'tree_sha256', 'pipeline_sha256'} and isinstance(value, str) and value:
                tree_ids.add(value)
            if key == 'reported_model' and isinstance(value, str):
                reported.add(value)
            if key in {'model', 'model_name'} and isinstance(value, str) and value.startswith('muse-'):
                models.add(value)
            expected = None
            kind = None
            if key == 'engine_manifest_sha256' or key == 'manifest_sha256' and ('engine_manifest/' in pointer or '/engine/' in pointer):
                kind, expected = 'engine_manifest', [expected_engine] if expected_engine else []
            elif key in {'binding_source_sha256', 'binding_source_after_sha256', 'planning_origin_binding_source_sha256', 'current_binding_source_sha256'}:
                kind, expected = 'binding_source', [binding] if binding else []
            elif key == 'source_sha256' and ('binding_snapshot/' in pointer or '/implementation/' in pointer):
                digest = snapshot_digest if 'binding_snapshot/' in pointer else implementation_digest
                kind, expected = 'binding_source', [digest] if digest else []
            elif key == 'adapter_sha256' and value:
                kind, expected = 'adapter', sorted(adapter_hashes)
            if kind:
                checks.append({'field': pointer, 'kind': kind, 'recorded': value, 'expected': expected,
                               'matches': bool(expected) and value in expected})
        if reported or models:
            reports[name] = {'reported_model': sorted(reported), 'recorded_model_names': sorted(models)}
        if checks:
            mismatches = [check for check in checks if not check['matches']]
            previous = sorted(tree_ids or {str(check['recorded']) for check in mismatches if check['recorded']})
            identifier = previous[0] if len(previous) == 1 else _json_digest(previous) if previous else 'unknown'
            provenance[name] = {'label': 'computed on pre-freeze tree ' + identifier if mismatches else 'computed on the frozen code',
                                'checks': checks, 'mismatches': mismatches,
                                'tree_id_basis': 'recorded tree identity' if tree_ids else 'recorded component identity; complete historical tree not recorded'}
    for stage, name in (('Spark', 'src/llm/spark/client.py'), ('Voice', 'experiments/mvp/voice/asr.py')):
        if name not in files:
            continue
        safe_path(root, name)
        try:
            tree = ast.parse((root / name).read_text())
        except (ValueError, SyntaxError):
            continue
        models = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'MODEL' for target in node.targets) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                models.append(node.value.value)
        requests[stage] = {'names': sorted(set(models)), 'source': name}
    lattice_context = pipeline_manifest if pipeline_manifest is not None else context if context.get('scope', 'pipeline') == 'pipeline' else {}
    lattice = lattice_provenance(root, files, lattice_context)
    provenance.update(lattice)
    return {'fixture_provenance': provenance, 'lattice_provenance': lattice, 'api_models': requests, 'fixture_reported_models': reports,
            'binding_source_sha256': binding, 'public_keys': sorted(name for name in files if not private_path(name) and (name.endswith('.pub') or 'public' in Path(name).name and name.endswith('.pem'))),
            'provenance_policy': 'Only recorded identities are compared. Missing identities are unverified; a match does not invent missing provenance.'}


def main(argv=None):
    """Check a regenerated output against a guarded pipeline manifest."""
    import argparse
    from .verify import FreezeDrift, _read_manifest, require_frozen
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument('--pipeline-manifest', type=Path, required=True)
    parser.add_argument('--only', action='append', help='restrict to one product bundle; may be repeated')
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        identity = require_frozen(args.pipeline_manifest, root=root)
        raw = _read_manifest(args.pipeline_manifest, root)
        if hashlib.sha256(raw).hexdigest() != identity['manifest_sha256']:
            raise ValueError('pipeline manifest changed after launch guard')
        if json.loads(raw).get('scope', 'pipeline') != 'pipeline':
            raise ValueError('output validation requires a pipeline-scope manifest')
        result = product_output_bindings(root, pipeline_sha256=identity['manifest_sha256'], only=args.only)
    except FreezeDrift as error:
        result = {'ready': False, 'errors': [str(error)], 'drifts': error.drifts}
    except (OSError, ValueError, TypeError, KeyError) as error:
        result = {'ready': False, 'errors': [str(error)]}
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

"""D277 mutable products cite the pipeline; the film layer pins their bytes."""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

PIPELINE = 'experiments/mvp/pipeline'
HERO = PIPELINE + '/package/fixtures/hero-run'
EXPLAIN = PIPELINE + '/explain/fixtures/hero'
SUITES = PIPELINE + '/freeze/suites'
COVERAGE = PIPELINE + '/lattice/hero-coverage'
LATTICE_STORE = PIPELINE + '/lattice/store'
PRODUCER_SOURCES = PIPELINE + '/lattice/archive/producer-sources'
EXCLUDED_ROOTS = (PIPELINE + '/repo', PIPELINE + '/repo-health')
LATTICE_ROOTS = tuple(PIPELINE + '/lattice/' + name for name in
                      ('store', 'hero-coverage', 'backup-campaign', 'screening',
                       'film-observer', 'audits', 'releases', 'standard-plan-pool', 'runs',
                       'archive/producer-sources'))
LATTICE_FILES = tuple(PIPELINE + '/lattice/' + name for name in
                      ('progress.json', 'RULES.md', 'README.md', 'film-analysis.json',
                       'hero-instruction-candidates.json', 'LATEST_RELEASE.json',
                       'hero-impossible.json', 'hero-facts-close-bay-9.json'))
ROOTS = (
    HERO, PIPELINE + '/package/fixtures/history',
    PIPELINE + '/sealed/runs', PIPELINE + '/sealed/results', PIPELINE + '/sealed/measurements',
    EXPLAIN, PIPELINE + '/explain/budget', *LATTICE_ROOTS, SUITES,
)
ROOT_PATTERNS = (PIPELINE + '/package/fixtures/failed-hero-build-*',
                 PIPELINE + '/package/fixtures/.hero-build-*')
EXPLAIN_FILES = (PIPELINE + '/explain/fixtures/hero-inputs.json',
                 PIPELINE + '/explain/fixtures/readiness.json',
                 PIPELINE + '/explain/fixtures/spend-snapshot.json')
READER_FILES = tuple(PIPELINE + '/reader_final/readings/' + name for name in
                     ('readings.jsonl', 'queue.json', 'offline-timing.json'))
FILES = (PIPELINE + '/package/hero-run-anchor.json', *EXPLAIN_FILES, *READER_FILES,
         PIPELINE + '/radio/readings.json', PIPELINE + '/xray/fixtures/real-trace.json', *LATTICE_FILES)
BUNDLE_CONTAINERS = tuple(PIPELINE + '/sealed/' + name for name in ('runs', 'results', 'measurements'))
CODE_SUFFIXES = {'.py', '.pyi', '.sh', '.so', '.dylib', '.dll', '.c', '.cc', '.cpp',
                 '.h', '.hpp', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'}


def excluded_all(name):
    name = Path(name).as_posix()
    return any(name == prefix or name.startswith(prefix + '/') for prefix in EXCLUDED_ROOTS)


def lattice_output(name):
    name = Path(name).as_posix()
    return name in LATTICE_FILES or any(name == prefix or name.startswith(prefix + '/') for prefix in LATTICE_ROOTS)


def is_product(name):
    name = Path(name).as_posix()
    return (name in FILES or any(name == prefix or name.startswith(prefix + '/') for prefix in ROOTS)
            or any(fnmatchcase(name, pattern) for pattern in ROOT_PATTERNS))


def excluded_pipeline(name, *, import_closure=()):
    """Never omit imported modules or executable code while dropping outputs."""
    name = Path(name).as_posix()
    return (is_product(name) and name not in import_closure
            and (name == PRODUCER_SOURCES or name.startswith(PRODUCER_SOURCES + '/')
                 or Path(name).suffix.lower() not in CODE_SUFFIXES))


def binding_required(name):
    """Delivered products bind the current pipeline; diagnostics keep provenance."""
    name = Path(name).as_posix()
    history = PIPELINE + '/package/fixtures/history'
    if (name == history or name.startswith(history + '/')
            or name == SUITES or name.startswith(SUITES + '/')
            or lattice_output(name)
            or any(fnmatchcase(name, pattern) for pattern in ROOT_PATTERNS)):
        return False
    return is_product(name) or name == PIPELINE + '/reader_final/readings'


def film_paths(root):
    """Existing generated roots/files, explicitly overriding cache/run exclusions."""
    root = Path(root)
    candidates = {root / name for name in (*ROOTS, *FILES) if name != SUITES}
    candidates.update(path for pattern in ROOT_PATTERNS for path in root.glob(pattern))
    suites = root / SUITES
    if suites.is_dir():
        for path in suites.rglob('*'):
            if path.suffix not in {'.json', '.xml'} or not path.is_file():
                continue
            if any(part.startswith(('unit-tmp', 'test-tmp', 'pytest-', '.pytest-'))
                   for part in path.relative_to(suites).parts):
                continue
            candidates.add(path)
    return sorted(path.relative_to(root).as_posix() for path in candidates if path.exists() or path.is_symlink())


def bundle_name(name):
    name = Path(name).as_posix()
    if name == FILES[0] or name == HERO or name.startswith(HERO + '/'):
        return HERO
    if name in EXPLAIN_FILES or name == PIPELINE + '/explain/budget' or name.startswith(PIPELINE + '/explain/budget/'):
        return EXPLAIN
    if name in READER_FILES:
        return PIPELINE + '/reader_final/readings'
    if name in FILES:
        return name
    for prefix in BUNDLE_CONTAINERS:
        if name.startswith(prefix + '/'):
            return prefix + '/' + name[len(prefix) + 1:].split('/', 1)[0]
    for prefix in sorted(ROOTS, key=len, reverse=True):
        if name == prefix or name.startswith(prefix + '/'):
            return prefix
    for pattern in ROOT_PATTERNS:
        if fnmatchcase(name, pattern):
            # The first generated directory owns all descendants.
            parent = PIPELINE + '/package/fixtures/'
            return parent + name[len(parent):].split('/', 1)[0]
    return None


def bundle_roots(root):
    # Test reports are immutable film evidence, not pipeline-produced bundles.
    return sorted({bundle_name(name) for name in film_paths(root)
                   if name != SUITES and not name.startswith(SUITES + '/')} - {None})

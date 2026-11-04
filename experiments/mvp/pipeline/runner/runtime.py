"""Keep optional engine/checker cache writes inside this feature's fence."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
_configured = False


def configure_caches():
    global _configured
    if _configured:
        return
    from stowage.linear import cache
    from stowage.checker import stability_liquid, stability_liquid_tables, stability_sounding, stability_tables
    targets = [(cache, '_DEFAULT_ROOT', 'linear'),
               (stability_liquid, 'CACHE_DIRECTORY', 'stability'),
               (stability_liquid_tables, 'CACHE_DIRECTORY', 'stability'),
               (stability_sounding, 'CACHE_DIRECTORY', 'stability'),
               (stability_tables, 'CACHE_DIR', 'stability')]
    for module, name, suffix in targets:
        target = HERE / 'cache' / suffix
        target.mkdir(parents=True, exist_ok=True)
        setattr(module, name, target)
    # These public helpers capture CACHE_DIR at definition time.
    for name in ('load_table_geometry', 'cached_geometry', 'migrate_legacy'):
        function = getattr(stability_tables, name)
        defaults = function.__kwdefaults__ or {}
        if 'cache_dir' in defaults:
            function.__kwdefaults__ = dict(defaults, cache_dir=HERE / 'cache' / 'stability')
    _configured = True

from pathlib import Path
import importlib.util
import sys
import pytest
run = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('fixwindow_cache', 'experiments/mvp/showcase_tools/run_hero.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
helper.isolate_caches(run)
raise SystemExit(pytest.main([
    '-p', 'no:cacheprovider', '-q',
    'tests/checker/test_rules_heat_ids.py',
    'tests/vessel/test_fleet_manual_restore.py',
    'tests/vessel/test_fleet_lashing_design.py',
    'tests/vessel/test_fleet_d56.py',
    '--junitxml=' + str(run / 'manual-lashing-heat.xml'),
]))

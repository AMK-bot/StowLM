from .binding_sources import runtime_fingerprint, runtime_source_files


def test_active_review3_runtime_is_recursive_but_measurement_drivers_are_not(tmp_path):
    root = tmp_path/'binding'
    root.mkdir()
    for name in ('__init__.py', 'adapter.py', 'allocation.py', 'provenance.py'):
        (root/name).write_text('runtime')
    review = root/'review3'
    review.mkdir()
    for name in ('__init__.py', 'api.py', 'hatch.py'):
        (review/name).write_text('runtime')
    for name in ('measurement.py', 'campaign.py', 'operations.py', 'activation.py', 'recorded.py', 'watchdog.py'):
        (review/name).write_text('measurement driver')
    (root/'_logic3_activation.py').write_text('measurement discovery')
    baseline = runtime_fingerprint(root)
    assert {'review3/__init__.py', 'review3/api.py', 'review3/hatch.py'} <= set(runtime_source_files(root))
    (review/'measurement.py').write_text('changed report')
    (root/'_logic3_activation.py').write_text('changed discovery')
    assert runtime_fingerprint(root) == baseline
    (review/'api.py').write_text('changed public runtime')
    assert runtime_fingerprint(root) != baseline
    api = runtime_fingerprint(root)
    (review/'hatch.py').write_text('changed hatch runtime')
    assert runtime_fingerprint(root) != api
    hatch = runtime_fingerprint(root)
    (root/'identity.py').write_text('new unknown runtime')
    assert runtime_fingerprint(root) != hatch
    assert 'identity.py' in runtime_source_files(root)


def test_new_runtime_package_is_tracked_without_opening_archived_snapshots(tmp_path):
    (tmp_path/'__init__.py').write_text('')
    runtime = tmp_path/'review3'/'future'
    runtime.mkdir(parents=True)
    (runtime.parent/'__init__.py').write_text('')
    (runtime/'__init__.py').write_text('')
    (runtime/'identity.py').write_text('new policy runtime')
    archive = tmp_path/'measurements'/'snapshot'
    archive.mkdir(parents=True)
    (archive.parent/'__init__.py').write_text('')
    (archive/'__init__.py').write_text('')
    (archive/'adapter.py').write_text('historical')
    paths = runtime_source_files(tmp_path)
    assert 'review3/future/identity.py' in paths
    assert not any(path.startswith('measurements/') for path in paths)

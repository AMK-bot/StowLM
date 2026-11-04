"""Cache admission uses fake APIs; these tests never import MLX or Metal."""
import json
from types import SimpleNamespace

import pytest

from llm.exit import runtime


def test_core_cache_api_is_bounded_and_logged(capsys):
    events = []
    mx = SimpleNamespace(
        set_cache_limit=lambda value: events.append(('limit', value)) or (8 << 30),
        clear_cache=lambda: events.append(('clear',)),
    )
    result = runtime.configure_mlx_cache(mx)
    assert events == [('limit', 2 << 30), ('clear',)]
    assert result['limit_bytes'] == 2 << 30
    assert result['previous_limit_bytes'] == 8 << 30
    assert result['scope'] == 'reusable_buffers_only'
    assert json.loads(capsys.readouterr().out)['event'] == 'mlx_cache_configured'


def test_legacy_metal_namespace_is_supported():
    events = []
    mx = SimpleNamespace(metal=SimpleNamespace(
        set_cache_limit=lambda value: events.append(('limit', value)),
        clear_cache=lambda: events.append(('clear',)),
    ))
    result = runtime.configure_mlx_cache(mx, limit_bytes=0)
    assert result['setter'] == 'mlx.core.metal.set_cache_limit'
    assert events == [('limit', 0), ('clear',)]


@pytest.mark.parametrize('limit', [-1, 1.0, True, (2 << 30) + 1])
def test_invalid_or_unbounded_limit_is_refused_without_api_calls(limit):
    with pytest.raises(ValueError, match='between 0 and 2 GiB'):
        runtime.configure_mlx_cache(SimpleNamespace(), limit_bytes=limit)


def test_missing_cache_api_refuses_model_loading():
    with pytest.raises(RuntimeError, match='cache limit unavailable'):
        runtime.configure_mlx_cache(SimpleNamespace(clear_cache=lambda: None))


@pytest.mark.parametrize('failure', ['setter', 'clear'])
def test_failing_cache_api_is_fail_closed(failure):
    def fail(*args):
        raise RuntimeError('device error')
    mx = SimpleNamespace(
        set_cache_limit=fail if failure == 'setter' else lambda value: None,
        clear_cache=fail if failure == 'clear' else lambda: None,
    )
    with pytest.raises(RuntimeError, match='configuration failed'):
        runtime.configure_mlx_cache(mx)


def test_resident_initialization_sets_limit_before_model_load(tmp_path, monkeypatch):
    from stowage.model import Provenance
    (tmp_path / 'config.json').write_text('{}')
    events = []
    model = SimpleNamespace(eval=lambda: events.append('eval'))
    planner = SimpleNamespace(
        model_path=str(tmp_path), adapter_path='parent',
        _load=lambda: events.append('load') or model,
        _loaded_content_identity={'model_content_sha256': 'base'},
    )
    monkeypatch.setattr(runtime, '_RESIDENT', None)
    monkeypatch.setattr(runtime, '_PLANNER', None)
    monkeypatch.setattr(runtime, '_clean_parent', lambda p: Provenance())
    monkeypatch.setattr(runtime, '_architecture', lambda p: {})
    monkeypatch.setattr(runtime, '_unfreeze_lora', lambda m: None)
    monkeypatch.setattr(runtime, '_load_parent_weights', lambda m, p: None)
    monkeypatch.setattr(runtime, 'preflight_mlx_device',
        lambda: events.append('cache') or {'metal_available':True,
            'memory_cache':{'limit_bytes': 2 << 30}})
    assert runtime._resident_for(planner) is model
    assert events == ['cache', 'load', 'eval']
    assert runtime._RESIDENT['mlx_cache']['limit_bytes'] == 2 << 30


def test_cache_failure_prevents_first_model_allocation(tmp_path, monkeypatch):
    from stowage.model import Provenance
    planner = SimpleNamespace(adapter_path='parent',
        _load=lambda: pytest.fail('must not load before cache is bounded'))
    monkeypatch.setattr(runtime, '_RESIDENT', None)
    monkeypatch.setattr(runtime, '_clean_parent', lambda p: Provenance())
    monkeypatch.setattr(runtime, '_architecture', lambda p: {})
    def fail():
        raise RuntimeError('cache configuration failed')
    monkeypatch.setattr(runtime, 'preflight_mlx_device', fail)
    with pytest.raises(RuntimeError, match='cache configuration failed'):
        runtime._resident_for(planner)


def test_device_preflight_checks_metal_before_cache_and_returns_metadata():
    events=[]
    mx=SimpleNamespace(metal=SimpleNamespace(
        is_available=lambda:events.append('metal') or True),
        set_cache_limit=lambda value:events.append(('limit',value)) or (8<<30),
        clear_cache=lambda:events.append('clear'))
    metadata=runtime.preflight_mlx_device(mx)
    assert events==['metal',('limit',2<<30),'clear']
    assert metadata['metal_available'] is True
    assert metadata['memory_cache']['limit_bytes']==2<<30


@pytest.mark.parametrize('available',[False,None,1])
def test_device_preflight_refuses_unavailable_or_ambiguous_device_before_cache(available):
    mx=SimpleNamespace(metal=SimpleNamespace(is_available=lambda:available),
        set_cache_limit=lambda value:pytest.fail('cache configured without a Metal device'))
    with pytest.raises(RuntimeError,match='No Metal device available'):
        runtime.preflight_mlx_device(mx)


def test_device_preflight_refuses_missing_availability_api():
    with pytest.raises(RuntimeError,match='availability API unavailable'):
        runtime.preflight_mlx_device(SimpleNamespace())


def test_device_preflight_wraps_driver_initialization_failure():
    def unavailable():
        raise ImportError('[metal::load_device] No Metal device available')
    with pytest.raises(RuntimeError,match='MLX device preflight failed') as failure:
        runtime.preflight_mlx_device(SimpleNamespace(metal=SimpleNamespace(is_available=unavailable)))
    assert isinstance(failure.value.__cause__,ImportError)


def test_device_preflight_wraps_mlx_import_failure_without_importing_mlx(monkeypatch):
    import builtins
    original=builtins.__import__
    def blocked_import(name,*args,**kwargs):
        if name=='mlx.core':
            raise ImportError('[metal::load_device] No Metal device available')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',blocked_import)
    with pytest.raises(RuntimeError,match='No Metal device available') as failure:
        runtime.preflight_mlx_device()
    assert isinstance(failure.value.__cause__,ImportError)


def test_device_preflight_refuses_missing_cache_api_even_with_device():
    mx=SimpleNamespace(metal=SimpleNamespace(is_available=lambda:True))
    with pytest.raises(RuntimeError,match='cache limit unavailable'):
        runtime.preflight_mlx_device(mx)

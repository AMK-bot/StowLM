"""Synthetic CPU fixtures only; no model, sealed, or family-test files."""
import copy
import json

import pytest

from experiments.mvp.reader.v3 import train, gpu_run, freeze
from experiments.mvp.reader.v3.gates import gate_report


def metrics(pct=95, count=120, loose=40, accepted=0):
    return {'count': count, 'exact_match_pct': pct, 'loosening_count': loose,
            'false_acceptance_count': accepted, 'data_sha256': 'synthetic-cases-hash',
            'missing_count': 0, 'schema_constrained': True,
            'answer_language': 'reader-schema-v3',
            'decoder_sha256': 'synthetic-current-mask',
            'domain_words_sha256': 'synthetic-current-vocabulary',
            'details': [{'id': f'fixture-{i}'} for i in range(count)]}


@pytest.mark.parametrize('count', [1, 3, 4, 5, 9, 2225])
def test_exactly_one_pass_with_actual_size_tail(count):
    groups = train.epoch_groups(count, 4, 199)
    assert sorted(index for group in groups for index in group) == list(range(count))
    assert len(groups[-1]) == (count - 1) % 4 + 1
    assert len(groups) == (count + 3) // 4


def test_v2_weight_warm_start_fresh_adam(tmp_path):
    cfg = json.loads((train.HERE / 'config.json').read_text())
    source = tmp_path / 'adapter-v2'
    source.mkdir()
    (source / 'adapters.safetensors').write_bytes(b'synthetic weights')
    (source / 'adapter_config.json').write_text(json.dumps({
        'fine_tune_type': 'lora', 'num_layers': cfg['num_layers'],
        'lora_parameters': {key: cfg[key] for key in ('rank', 'scale', 'dropout')}}))
    report = train.validate_recipe(cfg, source, tmp_path / 'adapter-v3')
    assert report['source_adapter_sha256'] == train.sha256(source / 'adapters.safetensors')
    assert report['optimizer_policy'] == 'fresh Adam; weight warm-start, no optimizer/scheduler resume'
    with pytest.raises(ValueError, match='immutable'):
        train.validate_recipe(cfg, source, source)


def test_source_cannot_be_frozen_before_stop_or_after_weight_mutation(tmp_path):
    source = tmp_path / 'adapter-v2'
    source.mkdir()
    weights = source / 'adapters.safetensors'
    weights.write_bytes(b'final fixture weights')
    with pytest.raises(ValueError, match='must be stopped'):
        train.require_stopped_source(source)
    (source / 'STOPPED.json').write_text(json.dumps({'adapter_sha256': train.sha256(weights)}))
    train.require_stopped_source(source)
    weights.write_bytes(b'changed while training fixture')
    with pytest.raises(ValueError, match='finalized STOPPED.json hash'):
        train.require_stopped_source(source)


@pytest.mark.parametrize('updates', [dict(t=880), dict(t=1001), dict(mask=127), dict(load1=45.01)])
def test_cpu_admission_rejects_stale_future_or_insufficient_state(tmp_path, updates):
    state = dict(t=999, mask=255, load1=45)
    state.update(updates)
    path = tmp_path / 'cores.json'
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match='admission denied'):
        train.check_admission(path, now=1000)


def test_cpu_admission_accepts_mask_of_eight_cores(tmp_path):
    path = tmp_path / 'cores.json'
    path.write_text(json.dumps(dict(t=999, mask=255, load1=45)))
    assert train.check_admission(path, now=1000)['mask'] == 255


def test_v3_gates_and_comparability():
    result = gate_report(metrics(95), metrics(94), metrics(98))
    assert result['all_pass']
    assert result['gain_over_untrained_pp'] == 1
    assert not gate_report(metrics(94.99), metrics(94), metrics(98))['R3']
    assert not gate_report(metrics(89), metrics(40), metrics(90))['R1']
    assert not gate_report(metrics(95, accepted=1), metrics(40), metrics(95))['R2']


@pytest.mark.parametrize('count,loose', [(119, 40), (120, 39)])
def test_minimum_120_and_40(count, loose):
    with pytest.raises(ValueError, match='at least'):
        gate_report(*(metrics(count=count, loose=loose) for _ in range(3)))


@pytest.mark.parametrize('change', ['ids', 'hash', 'language', 'mask', 'missing'])
def test_gate_rejects_noncomparable_or_incomplete_arm(change):
    tuned, untrained, spark = metrics(), metrics(), metrics()
    if change == 'ids':
        spark['details'][0]['id'] = 'different'
    elif change == 'hash':
        spark['data_sha256'] = 'different'
    elif change == 'language':
        spark['answer_language'] = 'different'
    elif change == 'mask':
        spark['schema_constrained'] = False
    else:
        spark['missing_count'] = 1
    with pytest.raises(ValueError):
        gate_report(tuned, untrained, spark)


def test_deadline_prevents_validation_before_io(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_run, 'HERE', tmp_path)
    monkeypatch.setattr(gpu_run.time, 'monotonic', lambda: 11)
    monkeypatch.setattr(gpu_run.subprocess, 'run', lambda *_a, **_k: pytest.fail('deadline'))
    assert 'hard stop' in gpu_run.Checker()(model=None, iteration=400, val_loss=1, deadline=10)
    assert not (tmp_path / 'checks').exists()


def test_cannot_train_after_sealing(tmp_path, monkeypatch):
    results = tmp_path / 'results'
    results.mkdir()
    (results / 'final-freeze.json').write_text('{}')
    monkeypatch.setattr(gpu_run, 'RESULTS', results)
    monkeypatch.setattr(train, 'check_admission', lambda: None)
    monkeypatch.setattr(gpu_run, 'freeze', lambda *_a, **_k: None)
    with pytest.raises(ValueError, match='retraining is forbidden'):
        gpu_run.train_only()


def test_existing_partial_training_never_silently_restarts(tmp_path, monkeypatch):
    adapter = tmp_path / 'adapter-v3'
    adapter.mkdir()
    (adapter / 'run.json').write_text('{}')
    monkeypatch.setattr(gpu_run, 'RESULTS', tmp_path / 'results')
    monkeypatch.setattr(gpu_run, 'ADAPTER', adapter)
    monkeypatch.setattr(train, 'check_admission', lambda: None)
    monkeypatch.setattr(gpu_run, 'freeze', lambda *_a, **_k: None)
    with pytest.raises(ValueError, match='no second run'):
        gpu_run.train_only()


def test_immutable_hash_freeze_detects_mutation(tmp_path):
    path = tmp_path / 'fixture.json'
    path.write_text('{}')
    frozen = {'files_sha256': freeze.record([path], tmp_path)}
    freeze.verify(frozen, tmp_path)
    path.write_text('{"edited":true}')
    with pytest.raises(ValueError, match='changed or missing'):
        freeze.verify(frozen, tmp_path)


def test_spark_validation_requires_schema_and_complete_population(tmp_path):
    data = tmp_path / 'fixture.jsonl'
    data.write_text('synthetic bytes only, never parsed')
    spark = dict(metrics(), backend='spark', recorded_prediction_count=120,
                 data_sha256=train.sha256(data))
    gpu_run.validate_spark(spark, data)
    for key, value in [('schema_constrained', False), ('recorded_prediction_count', 119),
                       ('loosening_count', 39), ('missing_count', 1)]:
        broken = copy.deepcopy(spark)
        broken[key] = value
        with pytest.raises(ValueError, match='complete historical schema-constrained Spark'):
            gpu_run.validate_spark(broken, data)


def test_each_local_arm_masks_each_row_and_preserves_truncation(monkeypatch, tmp_path):
    """Real evaluator orchestration with CPU doubles, no MLX initialization."""
    import sys
    import types
    from experiments.mvp.reader.v3 import evaluate, decoder
    masks, loaded, calls = [], [], []
    tokenizer = types.SimpleNamespace(eos_token_ids={99},
                                     encode=lambda _prompt, **_kwargs: [11, 12])

    def module(name, **attrs):
        value = types.ModuleType(name)
        value.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, value)
        return value

    mx = module('mlx.core', random=types.SimpleNamespace(seed=lambda _: None))
    module('mlx', core=mx)

    def load(path, adapter_path=None):
        loaded.append(adapter_path)
        return object(), tokenizer

    def make_processor(tokenizer_arg, context, prompt_len, stop):
        assert tokenizer_arg is tokenizer and prompt_len == 2 and stop == 99
        def validate(answer):
            calls.append(answer)
            raise ValueError('truncated synthetic output')
        value = types.SimpleNamespace(validate_output=validate)
        masks.append(value)
        return value

    def generate(_model, _tokenizer, **kwargs):
        assert kwargs['prompt'] == [11, 12]
        assert kwargs['logits_processors'] == [masks[-1]]
        assert kwargs['max_tokens'] == 256
        return '{'

    module('mlx_lm', load=load, generate=generate)
    module('mlx_lm.sample_utils', make_sampler=lambda **kwargs: kwargs)
    monkeypatch.setattr(evaluate, 'check_model', lambda _: None)
    monkeypatch.setattr(evaluate, 'render_prompt', lambda *_: 'synthetic prompt')
    monkeypatch.setattr(evaluate, 'prompt_messages', lambda _: [])
    monkeypatch.setattr(decoder, 'make_logits_processor', make_processor)
    rows = [{'id': 'synthetic-row', 'context': {}}]
    for adapter in (None, tmp_path / 'adapter-v3'):
        predictions = evaluate.run_glimmer(rows, tmp_path / 'model', adapter)
        assert predictions == [{'id': 'synthetic-row', 'prediction': '{',
                                'decoding_error': 'truncated synthetic output'}]
    assert len(masks) == 2 and calls == ['{', '{']
    assert loaded == [None, str(tmp_path / 'adapter-v3')]


def stopped_fixture(root, version, *, finalized=True):
    """Synthetic bytes only; never touches actual adapter directories."""
    source = root / f'adapter-{version}'
    source.mkdir()
    (source / 'adapters.safetensors').write_bytes(f'synthetic-{version}-weights'.encode())
    (source / 'adapter_config.json').write_text('{}')
    if finalized:
        stopped = ({'reason': 'fixture stop', 'at': 'fixture timestamp'} if version == 'v1'
                   else {'adapter_sha256': train.sha256(source / 'adapters.safetensors')})
        (source / 'STOPPED.json').write_text(json.dumps(stopped))
    return source


def test_source_selection_prefers_finalized_hash_bound_v2(tmp_path):
    stopped_fixture(tmp_path, 'v1')
    v2 = stopped_fixture(tmp_path, 'v2')
    path = tmp_path / 'results/source-selection.json'
    selected = train.select_source(path, tmp_path)
    assert selected['source_adapter'] == str(v2.resolve())
    assert selected['stop_binding'] == 'finalized-stop-hash'
    original = path.read_bytes()
    assert train.select_source(path, tmp_path) == selected
    assert path.read_bytes() == original


@pytest.mark.parametrize('v2_state', ['running', 'hash-mismatch', 'unhashed-stop'])
def test_source_selection_falls_back_once_to_legacy_stopped_v1(tmp_path, v2_state):
    v1 = stopped_fixture(tmp_path, 'v1')
    v2 = stopped_fixture(tmp_path, 'v2', finalized=False)
    if v2_state == 'hash-mismatch':
        (v2 / 'STOPPED.json').write_text(json.dumps({'adapter_sha256': 'wrong'}))
    elif v2_state == 'unhashed-stop':
        (v2 / 'STOPPED.json').write_text(json.dumps({'reason': 'not finalized', 'at': 'fixture'}))
    path = tmp_path / 'results/source-selection.json'
    selected = train.select_source(path, tmp_path)
    assert selected['source_adapter'] == str(v1.resolve())
    assert selected['source_adapter_sha256'] == train.sha256(v1 / 'adapters.safetensors')
    assert selected['stop_binding'] == 'legacy-v1-computed-hash'
    original = path.read_bytes()
    # V2 finishing later cannot change the immutable warm-start decision.
    (v2 / 'STOPPED.json').write_text(json.dumps({'adapter_sha256': train.sha256(v2 / 'adapters.safetensors')}))
    assert train.select_source(path, tmp_path) == selected
    assert path.read_bytes() == original


@pytest.mark.parametrize('filename', ['adapters.safetensors', 'adapter_config.json', 'STOPPED.json'])
def test_selected_v1_mutation_fails_without_rerouting_to_newly_ready_v2(tmp_path, filename):
    v1 = stopped_fixture(tmp_path, 'v1')
    v2 = stopped_fixture(tmp_path, 'v2', finalized=False)
    path = tmp_path / 'results/source-selection.json'
    train.select_source(path, tmp_path)
    (v2 / 'STOPPED.json').write_text(json.dumps({'adapter_sha256': train.sha256(v2 / 'adapters.safetensors')}))
    (v1 / filename).write_bytes(b'changed synthetic file')
    with pytest.raises((ValueError, json.JSONDecodeError)):
        train.select_source(path, tmp_path)
    assert json.loads(path.read_text())['source_adapter'] == str(v1.resolve())


def test_no_actual_selection_from_data_preparation_defaults():
    assert train.DATA == train.HERE / 'data-domain'
    assert train.data_preflight.__defaults__[0] == train.DATA
    cfg = json.loads((train.HERE / 'config.json').read_text())
    assert cfg['data_dir'].endswith('/data-domain')
    assert 'Always run v3' in cfg['run_policy']
    assert 'source-selection.json' in cfg['selection']


def test_training_requires_frozen_selection_and_cannot_reroute(tmp_path, monkeypatch):
    v1 = stopped_fixture(tmp_path, 'v1')
    selection = tmp_path / 'results/source-selection.json'
    selected = train.select_source(selection, tmp_path)
    recipe = tmp_path / 'results/recipe-freeze.json'
    monkeypatch.setattr(train, 'ROOT', tmp_path)
    with pytest.raises(FileNotFoundError):
        train.frozen_source(selection, recipe)
    files = [selection, *[v1 / name for name in ('adapters.safetensors', 'adapter_config.json', 'STOPPED.json')]]
    recipe.write_text(json.dumps({'files_sha256': freeze.record(files, tmp_path),
                                 'source_selection_sha256': train.sha256(selection),
                                 'source_adapter_sha256': selected['source_adapter_sha256']}))
    assert train.frozen_source(selection, recipe)[0] == selected
    v2 = stopped_fixture(tmp_path, 'v2')
    with pytest.raises(ValueError, match='override differs'):
        train.preflight(source=v2, selection_path=selection, recipe_freeze=recipe)
    # No freeze or selection gets created by a failed/read-only training check.
    assert train.load_source_selection(selection)['source_adapter'] == str(v1.resolve())


def test_freeze_binds_selected_source_and_domain_data_only(tmp_path):
    selected = {'source_adapter': str(tmp_path / 'adapter-v1')}
    files = freeze.inputs('recipe', selected)
    assert train.HERE / 'results/source-selection.json' in files
    assert train.HERE / 'data-domain/train.jsonl' in files
    assert train.HERE / 'data-domain/valid.jsonl' in files
    assert train.HERE / 'data-domain/manifest.json' in files
    assert train.HERE / 'domain_words.py' in files
    assert train.HERE / 'build_domain.py' in files
    assert tmp_path / 'adapter-v1/adapters.safetensors' in files
    assert not any('adapter-v2' in str(path) for path in files)
    assert not any(path.name == 'test.jsonl' for path in files)


def test_v2_reference_readiness_is_nonblocking_and_hash_bound(tmp_path):
    source = stopped_fixture(tmp_path, 'v2', finalized=False)
    assert gpu_run.stopped_v2_reference(source) is None
    (source / 'STOPPED.json').write_text(json.dumps({'adapter_sha256': 'not-final'}))
    assert gpu_run.stopped_v2_reference(source) is None
    (source / 'STOPPED.json').write_text(json.dumps({'adapter_sha256': train.sha256(source / 'adapters.safetensors')}))
    assert gpu_run.stopped_v2_reference(source) == source


def test_v2_reference_freeze_binds_source_before_any_scoring(tmp_path, monkeypatch):
    source = stopped_fixture(tmp_path, 'v2')
    here = tmp_path / 'v3'
    here.mkdir()
    results = here / 'results'
    results.mkdir()
    for path in (here / 'decoder.py', here / 'domain_words.py', here / 'evaluate.py', results / 'final-freeze.json'):
        path.write_text('synthetic frozen bytes')
    monkeypatch.setattr(gpu_run, 'HERE', here)
    monkeypatch.setattr(gpu_run, 'ROOT', tmp_path)
    monkeypatch.setattr(gpu_run, 'RESULTS', results)
    frozen = gpu_run.freeze_v2_reference(source)
    assert frozen['source_adapter_sha256'] == train.sha256(source / 'adapters.safetensors')
    assert gpu_run.freeze_v2_reference(source) == frozen
    path = results / 'v2-reference-freeze.json'
    original = path.read_bytes()
    (source / 'adapters.safetensors').write_bytes(b'changed fixture weights')
    with pytest.raises(ValueError):
        gpu_run.freeze_v2_reference(source)
    assert path.read_bytes() == original


def test_runner_passes_frozen_source_instead_of_hardcoded_v2(tmp_path, monkeypatch):
    source = tmp_path / 'adapter-v1'
    monkeypatch.setattr(gpu_run, 'RESULTS', tmp_path / 'results')
    monkeypatch.setattr(gpu_run, 'ADAPTER', tmp_path / 'adapter-v3')
    monkeypatch.setattr(gpu_run, 'SAMPLE', tmp_path / 'sample.jsonl')
    monkeypatch.setattr(train, 'check_admission', lambda: None)
    monkeypatch.setattr(gpu_run, 'freeze', lambda *_a, **_k: None)
    monkeypatch.setattr(gpu_run, 'fixed_sample', lambda *_a, **_k: [{'id': 'synthetic'}])
    monkeypatch.setattr(gpu_run, 'log', lambda **_k: None)
    monkeypatch.setattr(train, 'preflight', lambda: ({}, tmp_path / 'model', [], [], {'source_adapter': str(source)}))
    calls = []
    def run_train(_rows, _valid, _model, actual_source, *_args, **_kwargs):
        calls.append(actual_source)
        return {'reason': 'synthetic stop'}
    monkeypatch.setattr(train, 'run_train', run_train)
    gpu_run.train_only()
    assert calls == [source]


def test_d224_hard_stop_is_four_hours_at_most(tmp_path):
    import json
    import pytest
    from experiments.mvp.reader.v3 import train as v3
    cfg = json.loads((v3.HERE/'config.json').read_text())
    assert cfg['hard_stop_seconds'] == 14400
    with pytest.raises(ValueError, match='four hours'):
        v3.validate_recipe({**cfg, 'hard_stop_seconds': 14401}, tmp_path/'a', tmp_path/'b')
    assert v3._v2.validate_recipe is v3.validate_recipe

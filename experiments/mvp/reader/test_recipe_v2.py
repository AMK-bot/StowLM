"""CPU-only D219 recipe and gates. No model, sealed data, or MLX imports."""
import copy
import json

import pytest

from experiments.mvp.reader import train_v2 as train
from experiments.mvp.reader import gpu_run_v2 as run
from experiments.mvp.reader.gates_v2 import gate_report_v2


@pytest.mark.parametrize('count', [1, 3, 4, 5, 7, 8, 9, 4401])
def test_one_epoch_never_drops_or_repeats_rows_and_flushes_tail(count):
    groups = train.epoch_groups(count, 4, 199)
    indices = [index for group in groups for index in group]
    assert sorted(indices) == list(range(count))
    assert len(groups) == (count + 3) // 4
    assert len(groups[-1]) == (count - 1) % 4 + 1
    assert all(len(group) == 4 for group in groups[:-1])
    assert groups == train.epoch_groups(count, 4, 199)
    # Every group receives one optimizer update of its mean gradient, including
    # the last single row: no dilution by the nominal accumulation count.
    averaged = [sum(index + 1 for index in group) / len(group) for group in groups]
    assert len(averaged) == len(groups)


@pytest.mark.parametrize('count,accumulation', [(0, 4), (4, 0), (-1, 4)])
def test_empty_or_invalid_epoch_rejected(count, accumulation):
    with pytest.raises(ValueError):
        train.epoch_groups(count, accumulation, 199)


def test_config_preserves_original_lora_and_one_epoch():
    cfg = json.loads((train.HERE / 'config-v2.json').read_text())
    old = json.loads((train.HERE / 'config-d203.json').read_text())
    for field in ['rank', 'scale', 'dropout', 'num_layers', 'learning_rate',
                  'batch_size', 'grad_accumulation_steps', 'val_batches', 'steps_per_eval']:
        assert cfg[field] == old[field]
    assert cfg['epochs'] == 1 and cfg['hard_stop_seconds'] == 10800


def make_source(tmp_path):
    source = tmp_path / 'v1'
    source.mkdir()
    cfg = json.loads((train.HERE / 'config-v2.json').read_text())
    (source / 'adapter_config.json').write_text(json.dumps({
        'fine_tune_type': 'lora', 'num_layers': cfg['num_layers'],
        'lora_parameters': {key: cfg[key] for key in ['rank', 'scale', 'dropout']}}))
    (source / 'adapters.safetensors').write_bytes(b'fake CPU fixture adapter weights')
    return source, cfg


def test_warm_start_has_weight_hash_and_fresh_optimizer_provenance(tmp_path):
    source, cfg = make_source(tmp_path)
    report = train.validate_recipe(cfg, source, tmp_path / 'v2')
    assert report['source_adapter_sha256'] == train.sha256(source / 'adapters.safetensors')
    assert report['optimizer_policy'].startswith('fresh Adam')
    assert 'no optimizer/scheduler resume' in report['optimizer_policy']
    assert (source / 'adapters.safetensors').read_bytes() == b'fake CPU fixture adapter weights'


@pytest.mark.parametrize('field,value', [('epochs', 2), ('batch_size', 2),
                                       ('hard_stop_seconds', 10801), ('hard_stop_seconds', 0),
                                       ('grad_accumulation_steps', 0), ('rank', 16)])
def test_unsafe_or_incompatible_recipe_rejected(tmp_path, field, value):
    source, cfg = make_source(tmp_path)
    cfg[field] = value
    with pytest.raises(ValueError):
        train.validate_recipe(cfg, source, tmp_path / 'v2')


def test_warm_start_cannot_overwrite_source(tmp_path):
    source, cfg = make_source(tmp_path)
    with pytest.raises(ValueError, match='immutable'):
        train.validate_recipe(cfg, source, source)


def metric(pct=95, count=120, loose=30, accepted=0):
    return {'count': count, 'exact_match_pct': pct, 'loosening_count': loose,
            'false_acceptance_count': accepted, 'data_sha256': 'same-sealed-v2-bytes',
            'details': [{'id': f'new-{i}'} for i in range(count)]}


def test_r3_new_bar_does_not_require_fifteen_point_untrained_gain():
    result = gate_report_v2(metric(95), metric(94), metric(98))
    assert result['R1'] and result['R2'] and result['R3']
    assert result['gain_over_untrained_pp'] == 1
    assert not gate_report_v2(metric(94.99), metric(80), metric(98))['R3']


def test_r1_and_r2_are_independent_hard_gates():
    assert not gate_report_v2(metric(89), metric(40), metric(90))['R1']
    assert not gate_report_v2(metric(95, accepted=1), metric(40), metric(98))['R2']


@pytest.mark.parametrize('change', ['ids', 'hash', 'count', 'duplicate'])
def test_gates_reject_incomparable_reports(change):
    tuned, untrained, spark = metric(), metric(), metric()
    if change == 'ids':
        spark['details'][0]['id'] = 'unrelated'
    elif change == 'hash':
        spark['data_sha256'] = 'different'
    elif change == 'count':
        spark['count'] = 119
    else:
        spark['details'][0] = copy.deepcopy(spark['details'][1])
    with pytest.raises(ValueError):
        gate_report_v2(tuned, untrained, spark)


@pytest.mark.parametrize('count,loose', [(119, 30), (120, 29)])
def test_gates_require_new_sample_minimums(count, loose):
    with pytest.raises(ValueError, match='at least'):
        gate_report_v2(metric(count=count, loose=loose), metric(count=count, loose=loose),
                       metric(count=count, loose=loose))


def test_gates_reject_all_missing_hashes_or_inconsistent_loosening_counts():
    reports = [metric(), metric(), metric()]
    for report in reports:
        report.pop('data_sha256')
    with pytest.raises(ValueError, match='hash'):
        gate_report_v2(*reports)
    tuned, untrained, spark = metric(), metric(), metric()
    spark['loosening_count'] = 31
    with pytest.raises(ValueError, match='loosening counts'):
        gate_report_v2(tuned, untrained, spark)


def test_incomplete_spark_blocks_final_gpu_inference(tmp_path, monkeypatch):
    monkeypatch.setattr(run, 'HERE', tmp_path)
    monkeypatch.setattr(run, 'RESULTS', tmp_path / 'results-v2')
    monkeypatch.setattr(run, 'freeze', lambda *_a, **_k: None)
    sealed = tmp_path / 'sealed-v2'
    sealed.mkdir()
    (sealed / 'cases.jsonl').write_text('synthetic sealed fixture, never parsed')
    summary = metric()
    summary.update(backend='spark', missing_count=1, recorded_prediction_count=119,
                   data_sha256=train.sha256(sealed / 'cases.jsonl'))
    path = tmp_path / 'spark-summary.json'
    path.write_text(json.dumps(summary))
    monkeypatch.setattr(run, 'evaluate_final', lambda *_a, **_k: pytest.fail('incomplete Spark cannot start final inference'))
    with pytest.raises(ValueError, match='complete Spark'):
        run.score_final(path)


def test_checker_hard_stop_never_starts_inference(tmp_path, monkeypatch):
    monkeypatch.setattr(run, 'HERE', tmp_path)
    monkeypatch.setattr(run, 'ADAPTER', tmp_path / 'adapter')
    run.ADAPTER.mkdir()
    (run.ADAPTER / 'adapter_config.json').write_text('{}')
    monkeypatch.setattr(train, 'save_weights', lambda *_: None)
    monkeypatch.setattr(run.time, 'monotonic', lambda: 11)
    monkeypatch.setattr(run.subprocess, 'run', lambda *_a, **_k: pytest.fail('deadline must prevent model inference'))
    assert 'hard stop' in run.Checker()(model=None, iteration=400, val_loss=1, deadline=10)


def test_training_loop_warm_starts_and_updates_short_tail_with_actual_divisor(tmp_path, monkeypatch):
    """Exercise the real orchestration with scalar CPU doubles, never MLX."""
    import sys
    import types

    updated, loaded = [], []

    class Scalar(float):
        def item(self):
            return float(self)

    class Model:
        state = {}
        def freeze(self):
            pass
        def named_modules(self):
            return []
        def trainable_parameters(self):
            return {'lora_a': 1}
        def train(self):
            pass

    class Adam:
        def __init__(self, learning_rate):
            self.state = {}
        def update(self, model, gradient):
            updated.append(gradient['lora_a'])

    class Dataset:
        def __init__(self, rows, *_args):
            self.items = [([row['gradient']], 0) for row in rows]
        def __len__(self):
            return len(self.items)
        def __getitem__(self, index):
            return self.items[index]

    def module(name, **attrs):
        value = types.ModuleType(name)
        value.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, value)
        return value

    mx = module('mlx.core', random=types.SimpleNamespace(seed=lambda _: None, state={}),
                set_cache_limit=lambda _: None, eval=lambda *_: None,
                load=lambda _: {'lora_a': 1},
                save_safetensors=lambda path, weights: train.Path(path).write_text(json.dumps(weights)),
                compile=lambda fn, **_kwargs: fn)
    nn = module('mlx.nn', value_and_grad=lambda *_: lambda _model, batch, _lengths: (
        (Scalar(batch), 1), {'lora_a': batch}))
    optim = module('mlx.optimizers', Adam=Adam)
    module('mlx', core=mx, nn=nn, optimizers=optim)
    module('mlx.utils', tree_flatten=lambda value: list(value.items()),
           tree_map=lambda fn, *trees: {key: fn(*(tree[key] for tree in trees)) for key in trees[0]})
    def load(model_path, adapter_path):
        loaded.append((model_path, adapter_path))
        return Model(), object()
    module('mlx_lm', load=load)
    module('mlx_lm.tuner')
    module('mlx_lm.tuner.datasets', CacheDataset=lambda value: value)
    module('mlx_lm.tuner.trainer', evaluate=lambda *_a, **_k: 1.0,
           grad_checkpoint=lambda _: pytest.fail('fixture disables checkpoint'),
           iterate_batches=lambda dataset, *_: iter([(dataset[0][0][0], None)]))
    module('llm.stowlm.train', completion_loss=object())
    monkeypatch.setattr(train, 'TokenDataset', Dataset)
    cfg = json.loads((train.HERE / 'config-v2.json').read_text())
    cfg['grad_checkpoint'] = False
    cfg['steps_per_eval'] = 4
    rows = [{'gradient': i + 1} for i in range(5)]
    destination = tmp_path / 'adapter-v2'
    report = train.run_train(rows, rows[:1], tmp_path / 'base', tmp_path / 'adapter-v1',
                             destination, cfg, {'fixture': True})
    groups = train.epoch_groups(5, 4, 199)
    expected = [sum(index + 1 for index in group) / len(group) for group in groups]
    assert updated == expected
    assert loaded == [(str(tmp_path / 'base'), str(tmp_path / 'adapter-v1'))]
    assert report['full_epoch_completed'] and report['rows_completed'] == 5
    assert report['optimizer_updates'] == 2
    assert (destination / 'DONE.json').exists()

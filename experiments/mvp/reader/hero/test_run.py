import pytest
from experiments.mvp.reader.hero.run import guard_input


def test_only_validation_train_map_paths_allowed():
    for path in ('experiments/mvp/corpus/val/small/a.json','experiments/mvp/corpus/train/tiny/a.json'):
        assert str(guard_input(path))==path
    for path in ('experiments/mvp/corpus/test/a.json','experiments/mvp/corpus/test-v3/a.json',
                 'experiments/mvp/corpus/test_v3/a.json','experiments/mvp/corpus/val/../test/a.json','/tmp/val/a.json'):
        with pytest.raises(ValueError):guard_input(path)

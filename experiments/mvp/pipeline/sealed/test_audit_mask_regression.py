from copy import deepcopy

from stowage.conventions import make_container_id

from .common import HERE, ROOT, read_json, sha256
from . import worker


def test_real_development_plan_uses_checker_and_physical_replay_not_e9_mask():
    fixture = read_json(HERE/'fixtures/audit-mask-regression.json')
    source = ROOT/fixture['map_path']
    assert sha256(source) == fixture['map_sha256']
    instance = worker._load(dict(path=str(source), sha256=fixture['map_sha256'],
                                 map_id='public-audit-regression', arm='pipeline', mode='dry-run'))
    result = worker._audit(instance, fixture['plan'], None)
    assert result['full_checker_pass'] is True
    assert result['replay_legal'] is True
    assert result['all_loaded'] is True
    assert result['level'] == 0
    assert result['mask_ok'] is False
    assert result['mask_diagnostic_only'] is True
    assert result['passed'] is True  # Failed before the diagnostic-mask correction.
    broken = deepcopy(fixture['plan'])
    next(move for move in broken['moves'] if 'container_id' in move)['container_id'] = make_container_id('ZZZU', 999999)
    invalid = worker._audit(instance, broken, None)
    assert invalid['passed'] is False
    assert invalid['replay_legal'] is False

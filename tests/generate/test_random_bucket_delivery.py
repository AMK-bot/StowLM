"""Selected D62 requests may never emit an unconditioned stress instance."""
import pytest

from stowage.generate import random_crossing, random_yard as random
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet_slices import load_fleet_preset


@pytest.mark.parametrize('applied,observed', [(False, False), (True, False), (True, True)])
def test_selected_unobserved_bucket_redraws_and_counts(monkeypatch, applied, observed):
    geometry = load_fleet_preset('feeder_500', 'full', with_hull=False)
    monkeypatch.setattr(random, '_full_vessel', lambda name: geometry)
    monkeypatch.setattr(random, 'accept_background_candidate', lambda instance, audit: instance)
    monkeypatch.setattr(random, 'completion_failures', lambda instance: ())
    monkeypatch.setattr(random_crossing, 'condition_crossing_bucket',
                        lambda instance: (instance, {'applied': applied, 'reason': 'forced_delivery_gap'}))
    monkeypatch.setattr(random_crossing, 'crossing_probe', lambda instance: {'observed': observed})
    audit = random.RandomGenerationReport()
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError, match='screening exhausted'):
        random.generate_random_instance('tiny', seed=13, d62_crossing_share=1,
                                        max_attempts=2, report=audit)
    assert audit.attempts == 2 and not audit.accepted
    # Every attempt that survived the earlier screens reached the bucket and
    # was redrawn; none was emitted. Earlier screens (e.g. the D64 reserve's
    # displacement band) may take some attempts first.
    earlier = sum(n for key, n in audit.rejections.items() if key != 'd62.bucket_not_delivered')
    assert audit.rejections.get('d62.bucket_not_delivered', 0) == 2-earlier >= 1, audit.rejections

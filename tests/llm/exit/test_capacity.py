import pytest

from llm.exit.capacity import CapacitySizer, gpu_busy_observation, observation, recommend_workers


def measure(workers, rate, background_workers=8, gpu_saturated=True):
    return observation(workers=workers, completed_maps=10, wall_s=10 / rate,
                       background_workers=background_workers, gpu_saturated=gpu_saturated)


def test_starts_at_six_without_treating_idle_cores_as_capacity():
    result = recommend_workers([], max_workers=10, background_workers=8)
    assert result['workers'] == 6
    assert result['effective_cpu_cores'] == 12
    assert result['hard_fleet_cpu_cores'] == 16
    assert result['requires_separate_fleet_admission']


def test_similar_measured_throughput_prefers_fewer_workers():
    result = recommend_workers([measure(6, 1), measure(5, .98)], background_workers=8)
    assert result['workers'] == 5


def test_clear_measured_throughput_gain_keeps_six():
    assert recommend_workers([measure(6, 1), measure(5, .8)],
                             background_workers=8)['workers'] == 6


def test_extra_workers_need_same_load_six_worker_baseline_and_gain():
    assert recommend_workers([measure(8, 2)], max_workers=10,
                             background_workers=8)['workers'] == 6
    assert recommend_workers([measure(6, 1), measure(8, 1.04)], max_workers=10,
                             background_workers=8)['workers'] == 6
    assert recommend_workers([measure(6, 1), measure(8, 1.2)], max_workers=10,
                             background_workers=8)['workers'] == 8


def test_background_and_gpu_load_changes_do_not_reuse_old_capacity():
    rows = [measure(6, 1), measure(6, 1, background_workers=6),
            measure(4, 2, background_workers=6), measure(6, 1, gpu_saturated=False),
            measure(3, 3, gpu_saturated=False)]
    assert recommend_workers(rows, background_workers=8)['workers'] == 6
    assert recommend_workers(rows, background_workers=6)['workers'] == 4
    assert recommend_workers(rows, background_workers=8, gpu_saturated=False)['workers'] == 3


def test_one_probe_without_current_baseline_cannot_justify_change():
    assert recommend_workers([measure(4, 2)], background_workers=8)['workers'] == 6


def test_uses_median_against_one_fast_outlier():
    rows = [measure(6, 1), measure(5, .7), measure(5, 5), measure(5, .7)]
    assert recommend_workers(rows, background_workers=8)['workers'] == 6


def test_journal_restarts_with_same_measurements(tmp_path):
    path = tmp_path / 'capacity.json'
    controller = CapacitySizer(path)
    controller.observe(workers=6, completed_maps=10, wall_s=10, background_workers=8)
    controller.observe(workers=5, completed_maps=10, wall_s=10, background_workers=8)
    restored = CapacitySizer(path)
    assert restored.observations == controller.observations
    assert restored.recommend(background_workers=8)['workers'] == 5


@pytest.mark.parametrize('overrides', [dict(workers=0), dict(workers=True),
    dict(completed_maps=0), dict(wall_s=0), dict(wall_s=float('nan')),
    dict(wall_s=float('inf')), dict(background_workers=-1), dict(gpu_saturated='yes')])
def test_bad_measurement_is_refused(overrides):
    kwargs = dict(workers=6, completed_maps=10, wall_s=10)
    kwargs.update(overrides)
    with pytest.raises(ValueError):
        observation(**kwargs)


def interval(start, finish):
    return dict(verification_started_monotonic=start, verification_finished_monotonic=finish)


def test_gpu_busy_measurement_excludes_feed_gaps_and_cpu_only_tail():
    rows = [interval(0, 3), interval(1, 3), interval(7, 10), interval(8, 10),
            interval(10, 30), interval(10, 30)]
    result = gpu_busy_observation(rows, workers=2, decode_started=0,
                                  decode_finished=12, background_workers=8)
    # Full occupancy during [1,3], [8,10], [10,12]. Four completed
    # maps; the tail's later completions cannot count while GPU is decoding.
    assert result['wall_s'] == 6
    assert result['completed_maps'] == 4
    assert result['maps_per_s'] == pytest.approx(4 / 6)
    assert result['gpu_saturated']
    assert result['background_workers'] == 8


def test_gpu_busy_measurement_counts_all_tied_finishes_before_new_starts():
    rows = [interval(0, 3), interval(0, 3), interval(3, 5), interval(3, 5)]
    result = gpu_busy_observation(rows, workers=2, decode_finished=5)
    assert result['completed_maps'] == 4
    assert result['wall_s'] == 5


def test_start_at_completion_cannot_create_false_busy_preceding_interval():
    rows = [interval(0, 5), interval(5, 10), interval(5, 10)]
    result = gpu_busy_observation(rows, workers=2, decode_finished=10)
    assert result['completed_maps'] == 2
    assert result['wall_s'] == 5


def test_underfilled_pool_and_insufficient_completions_are_not_measurements():
    assert gpu_busy_observation([interval(0, 10)], workers=2, decode_finished=10) is None
    assert gpu_busy_observation([interval(0, 2), interval(0, 20)], workers=2,
                                 decode_finished=10) is None
    assert gpu_busy_observation([interval(0, .1), interval(0, .1)], workers=2,
                                 decode_finished=10) is None


def test_current_decode_window_excludes_cached_previous_run_intervals():
    rows = [interval(0, 5), interval(0, 5), interval(20, 22), interval(20, 22)]
    result = gpu_busy_observation(rows, workers=2, decode_started=20, decode_finished=22)
    assert result['wall_s'] == 2
    assert result['completed_maps'] == 2


def test_missing_timestamps_and_jobs_starting_after_decode_are_ignored():
    assert gpu_busy_observation([{}, interval(12, 20)], workers=1, decode_finished=10) is None


@pytest.mark.parametrize('row', [interval(3, 2), interval(float('nan'), 3),
                              interval(0, float('inf')), interval(False, 2)])
def test_bad_job_interval_refused(row):
    with pytest.raises(ValueError):
        gpu_busy_observation([row], workers=1, decode_finished=10)

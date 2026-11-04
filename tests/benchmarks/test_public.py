"""Small published checks by default; complete Zhu coverage is opt-in.

A published optimum certifies only its stated variant/height. Both the free-order
engine bound and ordered LB1 are safe against restricted optima; such an optimum
must never be advertised as a lower bound on unrestricted plans.
"""
from itertools import islice
from hashlib import sha256
import json
from zipfile import ZipFile
from pathlib import Path

import pytest

from stowage.bench.loaders import iter_zhu_instances, parse_brp_text

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "data/raw/move-benchmarks-tanaka/Zhu_dataset.zip"


def _archive():
    if not ARCHIVE.exists():
        pytest.skip("local research archive unavailable; see docs/data-sources.md §7")
    return ARCHIVE


EVIDENCE = ROOT / "experiments/benchmarks/evidence.json"


def _targets():
    evidence = json.loads(EVIDENCE.read_text())
    targets = {r["name"]: r for r in evidence["instances"]}
    assert len(targets) == len(evidence["instances"])
    assert len(targets) == evidence["coverage"]["certified_rows"]
    return targets


@pytest.mark.benchmark
def test_quick_published_optima():
    from stowage.bench.adapter import benchmark_bounds

    with ZipFile(_archive()) as zipped:
        for name, target in list(_targets().items())[:3]:
            data = zipped.read(name)
            assert sha256(data).hexdigest() == target["instance_sha256"]
            assert target["optimum"] == target["published_lower_bound"]
            item = parse_brp_text(data.decode("ascii"), name=name,
                height_limit=target["height_limit"], variant=target["variant"])
            bounds = benchmark_bounds(item.stacks, item.height_limit)
            assert bounds.engine_yard <= target["optimum"]
            assert bounds.ordered_lb1 <= target["optimum"]


@pytest.mark.benchmark
def test_quick_public_sample():
    from stowage.bench.adapter import benchmark_bounds

    # This sample checks the mapping, not optimality: no published targets are
    # inferred from the archive. H+2 is an explicit scan protocol only.
    sample = list(islice(iter_zhu_instances(
        _archive(), height_limit=lambda name: int(name.split('-')[0]) + 2,
        variant="unrestricted",
    ), 3))
    assert len(sample) == 3
    for instance in sample:
        bounds = benchmark_bounds(instance.stacks, instance.height_limit)
        assert bounds.engine_yard == 0
        assert 0 <= bounds.ordered_lb1 <= instance.container_count


@pytest.mark.slow
@pytest.mark.benchmark
def test_complete_public_archive():
    from stowage.bench.adapter import benchmark_bounds

    targets = _targets()
    matched = 0
    with ZipFile(_archive()) as zipped:
        for name, target in targets.items():
            assert sha256(zipped.read(name)).hexdigest() == target["instance_sha256"]
            assert target["published_lower_bound"] == target["optimum"]
            assert target["variant"] == "restricted"
    count = 0
    for instance in iter_zhu_instances(
        _archive(), height_limit=lambda name: int(name.split('-')[0]) + 2,
        variant="unrestricted",
    ):
        target = targets.get(instance.name)
        height = target["height_limit"] if target else instance.height_limit
        bounds = benchmark_bounds(instance.stacks, height)
        if target:
            assert len(instance.stacks) == target["stacks"]
            assert instance.container_count == target["blocks"]
            assert bounds.engine_yard <= target["optimum"], instance.name
            assert bounds.ordered_lb1 <= target["optimum"], instance.name
            matched += 1
        assert bounds.engine_yard == 0, instance.name
        assert 0 <= bounds.ordered_lb1 <= instance.container_count, instance.name
        count += 1
    assert count == 12_500

    assert matched == len(targets)


@pytest.mark.benchmark
def test_published_figure_solution_reproduces_move_count():
    from stowage.bench.adapter import replay_relocations
    from stowage.conventions import make_container_id

    record = json.loads((ROOT / "experiments/benchmarks/published-solution.json").read_text())
    image = ROOT / record["local_image"]
    if not image.exists():
        pytest.skip("local public Figure 1 unavailable; evidence workflow requires it")
    assert sha256(image.read_bytes()).hexdigest() == record["image_sha256"]
    result = replay_relocations(record["stacks"], record["height_limit"], record["relocations"],
        expected_relocations=record["expected_relocations"], restricted=record["restricted"])
    assert result.count.relocations == 3
    assert result.count.gate_moves == 6
    assert result.count.weighted_nonproductive == 3
    assert [m.container_id for m in result.plan.moves] == [
        make_container_id("QZXU", p) for p in record["published_moved_priorities"]]
    assert not result.final_state.yard.stacks

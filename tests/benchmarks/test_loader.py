"""Check malformed benchmark data is rejected before model adaptation."""
from pathlib import Path
from zipfile import ZipFile

import pytest

from stowage.bench.loaders import BRPInstance, iter_zhu_instances, parse_brp_text


def parse(text: str, **kwargs: object) -> BRPInstance:
    return parse_brp_text(text, name="sample", height_limit=3, variant="unrestricted", **kwargs)


def test_integer_rows_preserve_bottom_to_top_and_priority_order() -> None:
    instance = parse("3 4\n2 4 1\n0\n2 3 2\n")
    assert instance.stacks == ((4, 1), (), (3, 2))
    assert instance.retrieval_order == (1, 2, 3, 4)
    assert instance.container_count == 4
    assert instance.height_limit == 3


@pytest.mark.parametrize("text", [
    "", "2 1\n1 1", "1 2\n1 1", "1 1\n2 1", "1 2\n2 1 1",
    "1 1\n1 0", "1 1\n1 -1", "1 1\n1 1\n0", "1 1\n1 1; import os",
    "1 4\n4 1 2 3 4", "Label : BF\nWidth : 2", "1 1 9\n1 1",
])
def test_malformed_or_non_brp_text_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        parse(text)


def test_operational_height_and_variant_must_be_explicit_and_valid() -> None:
    with pytest.raises(ValueError):
        BRPInstance("sample", ((1,),), True, "restricted")
    with pytest.raises(ValueError):
        BRPInstance("sample", ((1,),), 2, "cpmp")  # type: ignore[arg-type]


def test_zip_members_are_sorted_without_extracting(tmp_path: Path) -> None:
    archive = tmp_path / "instances.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.writestr("2-2-3/z.txt", "2 3\n2 3 1\n1 2\n")
        zipped.writestr("2-2-3/a.txt", "2 3\n2 2 1\n1 3\n")
    instances = list(iter_zhu_instances(archive, height_limit=lambda _: 4, variant="restricted"))
    assert [instance.name for instance in instances] == ["2-2-3/a.txt", "2-2-3/z.txt"]
    assert all(instance.height_limit == 4 for instance in instances)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["instances.zip"]


@pytest.mark.parametrize("member,text", [
    ("../2-2-3/a.txt", "2 3\n2 3 1\n1 2\n"),
    ("2-2-4/a.txt", "2 3\n2 3 1\n1 2\n"),
    ("1-2-3/a.txt", "2 3\n2 3 1\n1 2\n"),
    ("2-2-3/a.py", "2 3\n2 3 1\n1 2\n"),
])
def test_zip_metadata_rejected(tmp_path: Path, member: str, text: str) -> None:
    archive = tmp_path / "instances.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.writestr(member, text)
    with pytest.raises(ValueError):
        list(iter_zhu_instances(archive, height_limit=4, variant="unrestricted"))

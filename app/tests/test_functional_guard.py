"""Keep the live Functional app independent of replay-only data sources."""

from pathlib import Path
import re


APP = Path(__file__).resolve().parents[1]
FORBIDDEN = {
    "RecordedClient": re.compile(r"RecordedClient"),
    "RecordedParser": re.compile(r"RecordedParser"),
    "stored=": re.compile(r"\bstored\s*="),
    "earlier=": re.compile(r"\bearlier\s*="),
    "lattice/store": re.compile(r"lattice/store"),
    "catalogue.json": re.compile(r"catalogue\.json"),
    "radio/readings": re.compile(r"radio/readings"),
    "v6_hero": re.compile(r"v6_hero"),
    "ui/cache": re.compile(r"ui/cache"),
    "hero(": re.compile(r"hero\s*\("),
}


def test_functional_sources_do_not_reference_recorded_or_stored_sources():
    sources = sorted(APP.glob("functional*.py"))
    sources += sorted(path for path in (APP / "static").glob("functional*") if path.is_file())
    assert sources, "The Functional app source scan must not be empty."
    violations = []
    for path in sources:
        source = path.read_text(encoding="utf-8")
        for marker, pattern in FORBIDDEN.items():
            for match in pattern.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                violations.append(f"{path.relative_to(APP)}:{line}: {marker}")
    assert not violations, "Forbidden replay source references:\n" + "\n".join(violations)


def test_replay_keyword_guard_checks_whole_names_and_preserves_whitespace_detection():
    for keyword, identifier in (("stored=", "restored"), ("earlier=", "not_earlier")):
        pattern = FORBIDDEN[keyword]
        name = keyword[:-1]
        assert pattern.search(f"run(packet, {name} = receipt)")
        assert pattern.search(f"run(packet, {name}\n = receipt)")
        assert not pattern.search(f"{identifier} = transformed")

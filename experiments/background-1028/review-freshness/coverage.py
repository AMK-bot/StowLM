"""Prove that completed vessel batches cover every currently selected test."""
from pathlib import Path
import json
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
expected = set()
for line in (HERE / "vessel-collected.txt").read_text().splitlines():
    if not line.startswith("tests/vessel/") or "::" not in line:
        continue
    parts = line.split("::")
    classname = parts[0][:-3].replace("/", ".")
    if len(parts) > 2:
        classname += "." + ".".join(parts[1:-1])
    expected.add((classname, parts[-1]))

covered, skipped = set(), set()
sources = ["vessel-final-current.xml", "manual-restore-red.xml", "vessel-rest.xml"]
for name in sources:
    for case in ET.parse(HERE / name).iter("testcase"):
        key = (case.attrib["classname"], case.attrib["name"])
        if case.find("failure") is None and case.find("error") is None:
            covered.add(key)
            if case.find("skipped") is not None:
                skipped.add(key)
result = dict(expected=len(expected), covered=len(covered & expected),
    passed=len((covered & expected) - skipped), skipped=len(skipped & expected),
    missing=sorted(expected - covered), source_xml=sources)
(HERE / "vessel-batch-coverage.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
assert expected and not result["missing"]

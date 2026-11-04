"""Summarize the recorded pytest equivalence evidence without rerunning physics."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import sys

HERE = Path(__file__).resolve().parent
manifest = {row['id']: row for row in json.loads((HERE/'corpus-manifest.json').read_text())['cases']}
source = Path(sys.argv[1]) if len(sys.argv)>1 else HERE/'corpus-equivalence.xml'
destination = Path(sys.argv[2]) if len(sys.argv)>2 else HERE/'corpus-equivalence-summary.json'
root = ET.parse(source).getroot()
summary = dict(tests=0, failures=0, errors=0, cases={}, non_bitwise_numeric_changes=[])
for test in root.iter('testcase'):
    if 'test_perf_equivalence_1028' not in test.attrib.get('classname', ''):
        continue
    summary['tests'] += 1
    summary['failures'] += len(test.findall('failure'))
    summary['errors'] += len(test.findall('error'))
    name = test.attrib['name']
    if '[' not in name:
        continue
    case_id = name.split('[',1)[1].removesuffix(']')
    row = manifest[case_id]
    kind = ('current_saved' if row.get('current_generated') else 'archived_saved') if row['kind']=='saved' else row['kind']
    group = summary['cases'].setdefault(kind, dict(total=0, passed_plan=0, failed_plan=0, sampled_parts={}))
    group['total'] += 1
    properties = {item.attrib['name']: item.attrib['value'] for item in test.findall('./properties/property')}
    if properties.get('reference_passed') == 'True':
        group['passed_plan'] += 1
    elif properties.get('reference_passed') == 'False':
        group['failed_plan'] += 1
    for key, ran in json.loads(properties.get('sampled_part_runs','{}')).items():
        part = group['sampled_parts'].setdefault(key.rsplit(':',1)[-1], dict(total=0, ran=0))
        part['total'] += 1
        part['ran'] += bool(ran)
    changes = json.loads(properties.get('non_bitwise_numeric_changes','[]'))
    if changes:
        summary['non_bitwise_numeric_changes'].append(dict(id=case_id, changes=changes))
destination.write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))

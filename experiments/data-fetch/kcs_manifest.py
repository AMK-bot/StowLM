"""Write checksummed provenance for the KCS inert source bundle."""
from pathlib import Path
import hashlib
import json

raw = Path(__file__).resolve().parents[2] / 'data/raw/kcs'
tokyo = 'https://t2015.nmri.go.jp/'
old = 'https://www.nmri.go.jp/study/research_organization/fluid_performance/cfd/cfdws05/gothenburg2000/'
items = {
 'kcs_gc.html': (tokyo+'kcs_gc.html',None),
 'instruction_KCS.html': (tokyo+'Instructions_KCS/instruction_KCS.html',None),
 'KCS_hull_SVA.zip': (tokyo+'file/Geometry_IGES_files/kcs/KCS_hull_SVA.zip',None),
 'KCS_hull_SVA.igs': (tokyo+'file/Geometry_IGES_files/kcs/KCS_hull_SVA.zip','KCS_hull_SVA.zip:KCS_hull_SVA/KCS_hull_SVA.igs'),
 'geometry_memo.txt': (tokyo+'file/Geometry_IGES_files/kcs/KCS_hull_SVA.zip','KCS_hull_SVA.zip:KCS_hull_SVA/memo.txt'),
 'KCS-Hull-SIMMAN2020.zip': ('https://simman2020.kr/download/KCS-Hull.zip',None),
 'kcs_geometry_gothenburg2000.html': (old+'KCS/kcs_g%26c.htm',None),
 'kcs_offsets.ZIP': (old+'data/KCS_data/kcs_offsets.ZIP',None),
}
for name in ('readhull.txt','kcs.fix','kcs.prf'):
    items[name] = (old+'data/KCS_data/kcs_offsets.ZIP',f'kcs_offsets.ZIP:{name}')
items['offsets_published.csv']=(old+'data/KCS_data/kcs_offsets.ZIP','kcs.fix; lossless transcription with experiments/data-fetch/kcs_offsets.py')
items['profile_published.csv']=(old+'data/KCS_data/kcs_offsets.ZIP','kcs.prf; lossless transcription with experiments/data-fetch/kcs_offsets.py')
manifest=[dict(file=name,url=url,retrieved='2026-10-27',licence='unstated',sha256=hashlib.sha256((raw/name).read_bytes()).hexdigest(),derived_from=derived) for name,(url,derived) in items.items()]
(raw/'MANIFEST').write_text(json.dumps(manifest,indent=2)+'\n')
print(f'{len(manifest)} payloads manifested')

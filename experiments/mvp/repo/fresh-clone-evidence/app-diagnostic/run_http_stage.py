import pathlib,hashlib,json,shutil
base=pathlib.Path('/private/tmp/stowlm-judge-99-3-xzmzsouq');source=(base/'run_app_probes.py').read_text();exec(source.split("run('origins'")[0])
COPY=base/'diagnostic-code-only';LOGS=base/'logs/app-diagnostic';LOGS.mkdir(exist_ok=True)
assets=['stage-director.css','stage-shots.js','stage-director.js','stage-bays.js','stage.js','stage-instruct.css','stage-instruct.js']
def probe(phase):
 code=origin+"""
import io,json,sys
from http.client import HTTPResponse
from stowage.io import load_instance
from app.backend import build_replay,make_demo_plan
from app.flow import DemoService
from app.server import make_handler
i=load_instance('data/fixtures/empty_ship.json');p=make_demo_plan(i);payload=build_replay(i,p)
service=DemoService(i,p,initial_replay=payload,discover=False)
Handler=make_handler(payload,service,test_authority='localhost',stage_options={'live_physics':False,'live_baseline':False})
class MemorySocket:
 def __init__(self,request=b''):self.incoming=io.BytesIO(request);self.outgoing=bytearray()
 def makefile(self,mode,*args):return self.incoming
 def sendall(self,data):self.outgoing.extend(data)
rows=[]
"""+f'phase={phase!r}\npaths={["/api/stage","/stage"]+["/"+a for a in assets]!r}\n'+"""
for path in paths:
 transport=MemorySocket(f'GET {path} HTTP/1.0\\r\\nHost: localhost\\r\\n\\r\\n'.encode());Handler(transport,('127.0.0.1',12345),object())
 response=HTTPResponse(MemorySocket(bytes(transport.outgoing)));response.begin();raw=response.read()
 pathlib.Path('../logs/app-diagnostic/'+phase+path.replace('/','_')+'.body').write_bytes(raw)
 row={'path':path,'status':response.status,'bytes':len(raw),'content_type':response.headers.get('content-type')}
 if path=='/api/stage' and response.status==200:
  data=json.loads(raw);row.update(top_keys=sorted(data),instance_id=data.get('instance_id'),checker=data.get('checker'),physics_status=(data.get('physics') or {}).get('status'))
 if response.status!=200:row['error_body']=raw.decode(errors='replace')
 rows.append(row)
origins={}
for name,module in list(sys.modules.items()):
 if name.split('.')[0] in ('app','stowage','llm','eval') and getattr(module,'__file__',None):
  path=pathlib.Path(module.__file__).resolve();assert path.is_relative_to(root),(name,str(path));origins[name]=str(path)
out={'phase':phase,'classification':'in-process production handler; no real bound server/browser','responses':rows,'project_module_origins':origins,'live_physics':False,'live_baseline':False}
pathlib.Path('../logs/app-diagnostic/'+phase+'-summary.json').write_text(json.dumps(out,indent=2)+'\\n');print(json.dumps(out),flush=True)
"""
 run(phase,['uv','run','--offline','--no-sync','python','-c',code])
probe('stage-before-static-repair')
rows=[];repo=pathlib.Path('.')
for name in assets:
 relative='app/static/'+name;src=repo/relative;dst=COPY/relative;assert not dst.exists();shutil.copy2(src,dst)
 rows.append({'path':relative,'bytes':dst.stat().st_size,'sha256':hashlib.sha256(dst.read_bytes()).hexdigest()})
(LOGS/'static-additions.json').write_text(json.dumps(rows,indent=2)+'\n')
probe('stage-after-static-repair')

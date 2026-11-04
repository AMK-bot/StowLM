import pathlib
base=pathlib.Path('/private/tmp/stowlm-judge-99-3-xzmzsouq')
source=(base/'run_app_probes.py').read_text()
exec(source.split("run('origins'")[0])
COPY=base/'diagnostic-code-only';LOGS=base/'logs/app-diagnostic';LOGS.mkdir(exist_ok=True)
code=origin+"""
import io,json,sys
from http.client import HTTPResponse
from stowage.io import load_instance
from app.backend import build_replay,make_demo_plan
from app.flow import DemoService
from app.server import make_handler
i=load_instance('data/fixtures/empty_ship.json');p=make_demo_plan(i)
payload=build_replay(i,p)
service=DemoService(i,p,initial_replay=payload,discover=False)
Handler=make_handler(payload,service,test_authority='localhost',stage_options={'live_physics':False,'live_baseline':False})
Handler.instruct_live=False
class MemorySocket:
 def __init__(self,request=b''):self.incoming=io.BytesIO(request);self.outgoing=bytearray()
 def makefile(self,mode,*args):return self.incoming
 def sendall(self,data):self.outgoing.extend(data)
responses=[]
for path in ['/api/health','/api/demo','/api/stage/explain','/api/stage/explain?live=1','/api/instruct/examples','/']:
 transport=MemorySocket(f'GET {path} HTTP/1.0\\r\\nHost: localhost\\r\\n\\r\\n'.encode())
 Handler(transport,('127.0.0.1',12345),object())
 response=HTTPResponse(MemorySocket(bytes(transport.outgoing)));response.begin();raw=response.read()
 pathlib.Path('../logs/app-diagnostic/'+path.replace('/','_').replace('?','_').replace('=','_')+'.body').write_bytes(raw)
 item={'path':path,'status':response.status,'bytes':len(raw),'content_type':response.headers.get('content-type')}
 assert response.status==200,item
 if path=='/api/health':assert json.loads(raw)=={'status':'ok'}
 if path=='/api/demo':
  data=json.loads(raw);item.update(checker_passed=data['checker']['passed'],validation_status=data['metadata']['validation_status'],shippable=data['metadata']['shippable'],explanation_source=data.get('explanation',{}).get('source'))
  assert data['checker']['passed'] is False and data['metadata']['shippable'] is False
  assert data.get('explanation',{}).get('source')=='template'
 if path.startswith('/api/stage/explain'):
  data=json.loads(raw);item.update(source=data.get('source'),live_used=data.get('live_used'),live_reason=data.get('live_reason'))
  assert data['source']=='template' and data['live_used'] is False
  assert data['text']
 responses.append(item)
origins={}
for name,module in list(sys.modules.items()):
 if name.split('.')[0] in ('app','stowage','llm','eval') and getattr(module,'__file__',None):
  path=pathlib.Path(module.__file__).resolve();assert path.is_relative_to(root),(name,str(path));origins[name]=str(path)
summary={'classification':'in-process production HTTP handler fallback; real socket bind denied by environment','responses':responses,'project_module_origins':origins,'test_v21_loaded':False,'spark_live_enabled':False}
pathlib.Path('../logs/app-diagnostic/http-fallback-summary.json').write_text(json.dumps(summary,indent=2)+'\\n')
print(json.dumps(summary),flush=True)
"""
run('http-inprocess',['uv','run','--offline','--no-sync','python','-c',code])

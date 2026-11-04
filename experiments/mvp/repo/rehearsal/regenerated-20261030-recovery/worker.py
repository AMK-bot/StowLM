
import io, json, os, pathlib, runpy, sys
root=pathlib.Path.cwd().resolve()
# A worker outside the copy needs the same cwd search entry as ``python -m``.
sys.path.insert(0,str(root))
out=pathlib.Path(os.environ['REHEARSAL_OUT'])
pathlib.Path(os.environ['REHEARSAL_OWNED_PID']).write_text(json.dumps({'pid':os.getpid()}))
def save(name,obj): (out/(name+'.json')).write_text(json.dumps(obj,indent=2,default=str)+'\n')
def origins():
    result={}
    for name,module in list(sys.modules.items()):
        if name.split('.')[0] in ('stowage','llm','app','eval') and getattr(module,'__file__',None):
            p=pathlib.Path(module.__file__).resolve()
            assert p.is_relative_to(root),(name,str(p))
            result[name]=str(p)
    save(os.environ['REHEARSAL_LABEL']+'-origins',result)
def fixture():
    from stowage.io import load_instance
    return load_instance('data/fixtures/empty_ship.json')
def main():
    mode=sys.argv[1]
    if mode=='origins':
        import stowage,llm,app
        from stowage.params import repo_root
        assert repo_root().resolve()==root
    elif mode=='data-setup':
        from stowage.data_setup import main
        return main(['--archive',sys.argv[2],'--root',str(root)])
    elif mode=='tests':
        import pytest
        return pytest.main(sys.argv[2:])
    elif mode in ('app-help','eval-help'):
        sys.argv=['app' if mode=='app-help' else 'eval.harness','--help']
        runpy.run_module(sys.argv[0],run_name='__main__')
    elif mode=='checker':
        from stowage.model import PlannerResult
        from stowage.checker.full import check,report_matches_inputs
        instance=fixture()
        result=PlannerResult.model_validate(json.loads(pathlib.Path('app/tests/fixtures/model_run/instances/fixture_empty_ship/result.json').read_text())['result'])
        report=check(instance,result.plan)
        binding=report_matches_inputs(report,instance,result.plan)
        assert binding
        save('checker',{'passed':report.passed,'binding':binding,'classification':'diagnostic synthetic fixture, not an approved demo plan','report':report.model_dump(mode='json')})
    elif mode=='securing':
        from stowage.actions import get_action_space
        from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
        from llm.securing import SecuringEngine
        instance=fixture().model_copy(deep=True)
        instance.vessel.hull.hull_offsets_ref='params/fleet/feeder_500.toml#hull_form'
        # Exercise real pinned fleet asset loading without cold-building every
        # fleet tank. This hybrid unit fixture is not a real fleet plan.
        inner=make_feasibility_engine(instance,get_action_space('boxslot'),config=FeasibilityConfig(extra={'global_screen':0}))
        secured=SecuringEngine(inner)
        result={'constructed':True,'hull_reference':'params/fleet/feeder_500.toml#hull_form','bounds_available':secured.screen.bounds_available,'unavailable_reason':secured.screen.unavailable_reason,'metrics':secured.securing_metrics(),'classification':'synthetic unit fixture with real checksum-pinned fleet hull reference; no full fleet vessel or approved plan claim'}
        save('securing',result)
        assert result['bounds_available'],result
    elif mode=='app-http':
        from http.client import HTTPResponse
        from app.backend import build_replay,make_demo_plan
        from app.flow import DemoService
        from app.server import make_handler
        from app.instruct import InstructService
        instance=fixture(); plan=make_demo_plan(instance)
        payload=build_replay(instance,plan)
        service=DemoService(instance,plan,initial_replay=payload,discover=False)
        handler=make_handler(payload,service,test_authority='localhost',stage_options={'live_physics':False,'live_baseline':False})
        handler.instruct_live=False
        class MemorySocket:
            def __init__(self,request=b''): self.incoming=io.BytesIO(request);self.outgoing=bytearray()
            def makefile(self,mode,*args): return self.incoming
            def sendall(self,data): self.outgoing.extend(data)
        paths=['/api/health','/api/demo','/api/stage','/api/stage/explain','/api/stage/explain?live=1','/api/instruct/examples','/','/stage','/stage-director.css','/stage-shots.js','/stage-director.js','/stage-bays.js','/stage.js','/stage-instruct.css','/stage-instruct.js']
        responses=[]
        for path in paths:
            transport=MemorySocket(f'GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n'.encode())
            handler(transport,('127.0.0.1',12345),object())
            response=HTTPResponse(MemorySocket(bytes(transport.outgoing)));response.begin();raw=response.read()
            item={'path':path,'status':response.status,'bytes':len(raw),'content_type':response.headers.get('content-type')}
            (out/('http-'+path.replace('/','_').replace('?','_').replace('=','_')+'.body')).write_bytes(raw)
            if path=='/api/demo' and response.status==200:
                data=json.loads(raw);item.update(checker_passed=data['checker']['passed'],shippable=data['metadata']['shippable'],validation_status=data['metadata']['validation_status'],explanation_source=data.get('explanation',{}).get('source'))
                assert not item['checker_passed'] and not item['shippable']
                assert item['explanation_source']=='template'
            if path.startswith('/api/stage/explain') and response.status==200:
                data=json.loads(raw);item.update(source=data.get('source'),live_used=data.get('live_used'))
                assert item['source']=='template' and item['live_used'] is False
            responses.append(item)
        instructions=InstructService(None,live=False)
        refusal=instructions.parse('Judge rehearsal unrecorded instruction. Do not call Spark.')
        assert refusal['cost_usd']=='0' and refusal['refusal']['code']=='no_recorded_response'
        save('app-http',{'classification':'production HTTP handler with in-memory socket; no bound TCP/browser/visual claim','responses':responses,'keyless_instruction_refusal':refusal,'live_physics':False,'live_baseline':False,'discovery':False})
        assert all(item['status']==200 for item in responses),responses
    else: raise ValueError(mode)
    return 0
try:
    raise SystemExit(main())
finally:
    origins()

"""Read-only same-process producer telemetry; no algorithm or decisions changed."""
import dataclasses,json,os,sys,threading,time
from pathlib import Path
def install(directory):
    directory=Path(directory)
    def sample():
        while True:
            frames=sys._current_frames();rows=[]
            for ident,frame in frames.items():
                while frame:
                    if frame.f_code.co_name=='generate_random_instance' and frame.f_code.co_filename.endswith('/generate/random_yard.py'):
                        audit=frame.f_locals.get('audit');values={}
                        if audit is not None:
                            for key in ('attempts','rejections','completion_checks','generation_attempts','background_checks'):
                                value=getattr(audit,key,None)
                                if isinstance(value,(int,float,str,bool,dict,list,type(None))):values[key]=value
                        rows.append(dict(thread=ident,line=frame.f_lineno,audit=values))
                    frame=frame.f_back
            if rows:
                path=directory/f'producer-progress-{os.getpid()}.json';temp=path.with_suffix('.tmp')
                temp.write_text(json.dumps(dict(pid=os.getpid(),timestamp=time.time(),producer_frames=rows),indent=2));temp.replace(path)
            time.sleep(5)
    threading.Thread(target=sample,daemon=True).start()

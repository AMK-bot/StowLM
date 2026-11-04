"""Own-child watchdog for XL preparation, never signals an unrelated PID."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,indent=2));temp.replace(path)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--hard-s',type=float,default=600)
    parser.add_argument('--log',type=Path,required=True);parser.add_argument('arguments',nargs=argparse.REMAINDER)
    args=parser.parse_args();env=dict(os.environ,C6_XL_HARD_S=str(args.hard_s))
    command=['nice','-n','5',sys.executable,'-B',str(HERE.parent/'build_ships_maps.py'),*args.arguments]
    started=time.monotonic();status='running'
    with args.log.open('w') as log:
        proc=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT)
        write(args.log.with_suffix('.ownership.json'),dict(parent_pid=os.getpid(),owned_child_pid=proc.pid,command=command,hard_s=args.hard_s))
        def stop(reason):
            nonlocal status
            status=reason;write(HERE/f'stop-{proc.pid}.json',dict(reason=reason))
            if proc.poll() is None:
                try:proc.terminate()
                except PermissionError:pass
                try:proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:proc.kill()
                    except PermissionError:pass
        try:
            while proc.poll() is None:
                if time.monotonic()-started>=args.hard_s+2:stop('timeout')
                time.sleep(.2)
        except KeyboardInterrupt:
            stop('interrupted');proc.wait()
        monitor=HERE/f'monitor-{proc.pid}.json'
        write(args.log.with_suffix('.execution.json'),dict(pid=proc.pid,returncode=proc.returncode,wall_s=time.monotonic()-started,
             status=status if status!='running' else ('completed' if proc.returncode==0 else 'failed'),
             monitor=json.loads(monitor.read_text()) if monitor.exists() else None))
    return proc.returncode
if __name__=='__main__':sys.exit(main())

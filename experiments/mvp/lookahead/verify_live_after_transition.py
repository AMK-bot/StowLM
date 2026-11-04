"""Separate live-wrapper compatibility evidence after foreign source changes."""
import hashlib,json,os,resource,time
from pathlib import Path
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','RAYON_NUM_THREADS'):
    os.environ[key]='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
import pytest
from verify_tests import Throttle
root=Path(__file__).resolve().parent
paths=[*Path('src/llm/lookahead').glob('*.py'),*Path('tests/llm/lookahead').glob('*.py'),*Path('tests/llm/securing').glob('*.py'),Path('tests/llm/instructions/test_instructions_mask_filter.py'),Path('src/llm/securing/mask_filter.py'),Path('src/llm/stowlm/mask.py'),Path('src/llm/stowlm/mask_mlx.py')]
def snapshot():return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
before=snapshot(); start,cpu=time.perf_counter(),time.process_time();throttle=Throttle()
(root/'tests-live-transition-running.json').write_text(json.dumps(dict(pid=os.getpid(),source_before=before),indent=2)+'\n')
args=['--import-mode=importlib','-q','-m','not slow and not gpu and not net','tests/llm/lookahead','tests/llm/securing','tests/llm/instructions/test_instructions_mask_filter.py','--junitxml='+str(root/'tests-live-transition.xml')]
code=pytest.main(args,plugins=[throttle]);after=snapshot()
record=dict(pid=os.getpid(),args=args,exit_code=int(code),source_before=before,source_after=after,source_valid=before==after,wall_s=time.perf_counter()-start,cpu_s=time.process_time()-cpu,sleep_s=throttle.slept,peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,native_threads=1,gpu=False,scope='Live compatibility with foreign securing/MLX edits; primary campaign remains archived old epoch.')
(root/'tests-live-transition-meta.json').write_text(json.dumps(record,indent=2)+'\n')
raise SystemExit(code or (0 if before==after else 1))

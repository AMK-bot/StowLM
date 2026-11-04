from pathlib import Path
from datetime import datetime,timezone
h=Path(__file__).resolve().parent;r=h.parents[4];p=r/'experiments/fleet/ledger.md'
marker='| mvp-exit/d137-validation |'
s=p.read_text()
if marker not in s:
    s+='\n| mvp-exit/d137-validation | D137 | 1 | 2 | no | 480s CPU-only private full-suite; native1 | '+datetime.now(timezone.utc).isoformat()+' |\n'
    p.write_text(s)
(h/'validation-reservation.json').write_text(__import__('json').dumps(dict(cores=1,ram_gb=2,gpu=False,estimate_s=480,recorded_utc=datetime.now(timezone.utc).isoformat(),already_running=True,reason='suite grew beyond initial short-test expectation; recorded on recognition',load1=9.45,memory_free_percent=83,ps='denied'),indent=2)+'\n')

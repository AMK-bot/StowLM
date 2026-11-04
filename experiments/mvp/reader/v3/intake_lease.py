"""GPU_HANDOFF step: the reader chain releases the GPU to intake with a six-hour lease."""
import json
from pathlib import Path
import sys
import time

issued = time.time()
Path(sys.argv[1]).write_text(json.dumps({'task': 'intake_lora', 'approved': True, 'authorized_by': 'claude',
                                         'issued_at': issued, 'expires_at': issued + 21600}) + '\n')

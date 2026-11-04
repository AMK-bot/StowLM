"""Queue ledger parsing does not count comments or this supervisor twice."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_d84


def test_ledger_numeric_cpu_column_and_owned_exemption():
    content = '''| executive/job | task | cores | RAM | GPU |
| mvp-gold/tiny | 6.1 | 5 | 10 | no |
<!-- queued reservation5CPU -->
| mvp-corpus/d84-evaluation | 5.3 | 1 | 2 | no |
| mvp-other/study | 4.7 | 2 | 6 | no |
'''
    assert run_d84.ledger_cores(content) == 7

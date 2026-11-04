"""Host-owned pause for paid recordings; replay and native/offline tests work."""
from decimal import Decimal
import json
from pathlib import Path
import re

CAP = Decimal('12')
POLICY = Path(__file__).with_name('spending-policy.json')


class SpendingPaused(RuntimeError):
    pass


def assert_paid_allowed(purpose):
    try:
        value = json.loads(POLICY.read_text())
        verified = value['state'] == 'verified_hero_authorized'
        authorized = (value['state'] in ('hero_authorized', 'verified_hero_authorized')
                      and Decimal(value['cap_usd']) == CAP
                      and isinstance(value.get('authorization_source'), str)
                      and bool(value['authorization_source'].strip())
                      and all(isinstance(value.get(key), str)
                              and re.fullmatch(r'[0-9a-f]{64}', value[key])
                              for key in ('hero_instance_sha256', 'lattice_catalogue_sha256')))
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
        authorized = False
    scoped = isinstance(purpose, str) and purpose.startswith(
        ('pipeline-t1.6-copilot/hero-final/verified/', 'pipeline-t1.6-copilot/shot10/verified/')
        if authorized and verified else
        ('pipeline-t1.6-copilot/hero-final/', 'pipeline-t1.6-copilot/shot10/'))
    if not authorized or not scoped:
        raise SpendingPaused('Paid copilot calls are paused until the leader announces the checked lattice hero. '
                             'The $12 cap is reserved for its final freeze and shot 10.')

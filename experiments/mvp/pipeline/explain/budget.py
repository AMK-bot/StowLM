"""One durable $5 allocation across retired evidence, language QA and the hero."""
from pathlib import Path
import json

from .cache import _BudgetClient, _lock, _write

DIRECTORY = Path(__file__).with_name('budget')


def task_client(client):
    DIRECTORY.mkdir(exist_ok=True)
    with _lock(DIRECTORY / '.seed.lock'):
        if not (DIRECTORY / '.spend.json').exists():
            old = Path(__file__).with_name('fixtures') / 'development' / 'retired-cache' / '.spend.json'
            previous = json.loads(old.read_text()) if old.exists() else {'used_usd': '0', 'calls': []}
            _write(DIRECTORY / '.spend.json', previous)
    return _BudgetClient(client, DIRECTORY, 5.)

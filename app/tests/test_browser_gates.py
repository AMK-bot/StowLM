"""Driver-level negative controls: no browser launch or claimed visual evidence."""
import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace as NS

import pytest


@pytest.mark.parametrize('fault', ['clean', 'console', 'pageerror', 'requestfailed', 'response',
                                  'route', 'app_error', 'unshippable', 'source_drift'])
def test_browser_verification_rejects_every_error_channel(monkeypatch, tmp_path, fault):
    from app import browser_verify as driver
    listeners = {}
    class Locator:
        @property
        def first(self): return self
        async def select_option(self, value): pass
        async def click(self): pass
        async def is_visible(self): return fault == 'app_error'
        async def inner_text(self): return 'Comparison ready' if self.selector == '#solve-progress' else 'Synthetic visible error'
        def __init__(self, selector): self.selector = selector
    class Page:
        def on(self, name, callback): listeners[name] = callback
        def locator(self, selector): return Locator(selector)
        async def route(self, pattern, callback): self.router = callback
        async def goto(self, url):
            if fault == 'console' and 'console' in listeners:
                listeners['console'](NS(type='error', text='Synthetic console error'))
            if fault == 'pageerror': listeners['pageerror'](RuntimeError('Synthetic JS error'))
            if fault == 'requestfailed' and 'requestfailed' in listeners:
                listeners['requestfailed'](NS(url=url + 'app.js', failure='Synthetic connection failure'))
            if fault == 'response' and 'response' in listeners:
                listeners['response'](NS(url=url + 'app.js', status=500))
            if fault == 'route':
                class Route:
                    request = NS(url=url, method='GET', post_data_buffer=None, headers={})
                    async def abort(self): pass
                    async def fulfill(self, **kwargs): pass
                await self.router(Route())
        async def wait_for_selector(self, *a, **k): pass
        async def wait_for_function(self, *a, **k): pass
        async def screenshot(self, *a, **k): pass
        async def set_viewport_size(self, *a, **k): pass
    class Browser:
        async def new_page(self, **kwargs): return Page()
        async def close(self): pass
    class Playwright:
        chromium = NS()
        async def __aenter__(self):
            async def launch(**kwargs): return Browser()
            self.chromium.launch = launch
            return self
        async def __aexit__(self, *args): pass
    module = ModuleType('playwright.async_api')
    module.async_playwright = Playwright
    monkeypatch.setitem(sys.modules, 'playwright', ModuleType('playwright'))
    monkeypatch.setitem(sys.modules, 'playwright.async_api', module)
    instance = NS(id='test.fixture', schema_version='1.9')
    monkeypatch.setattr(driver, 'load_inputs', lambda **kw: NS(instance=instance, plan=object()))
    monkeypatch.setattr(driver, 'checked_replay', lambda i: {})
    replay = {'metadata': {'shippable': True}, 'checker': {'bound': True,
              'conditions': [{'phase': 'departure'}, {'phase': 'mid_voyage'}, {'phase': 'arrival'}]}}
    class Service:
        def __init__(self, *a, **k): pass
        def catalog(self): return {'plans': [{'id': 'left', 'planner': 'solver.scalable'},
                                            {'id': 'right', 'planner': 'baseline.twostep'}]}
        def comparison(self, *a): return {'comparable': fault != 'unshippable'}
        def replay(self, *a): return replay
    monkeypatch.setattr(driver, 'DemoService', Service)
    class Transport:
        def __init__(self, *a): pass
        def request(self, *a): raise RuntimeError('Synthetic routed request error')
    monkeypatch.setattr(driver, 'InProcessHTTP', Transport)
    calls = []
    def pins():
        calls.append(None)
        return {'test-source': 'changed' if fault == 'source_drift' and len(calls) > 1 else 'original'}
    monkeypatch.setattr('app.smoke._source_pins', pins)
    result = asyncio.run(driver.verify(NS(output=tmp_path, instance=tmp_path / 'instance.json',
                primary='solver.scalable', chrome='/unused', timeout_s=1)))
    record = json.loads((tmp_path / 'browser-verification.json').read_text())
    assert (result == 0) is (fault == 'clean')
    assert record['browser_verified'] is (fault == 'clean')

"""Browser verification using the real HTTP handler without a listening socket.

Playwright is an optional verification dependency, never a runtime dependency.
The browser receives normal production HTTP responses. API requests execute the
same handler, registry and live planning jobs as the localhost application; no
planner, checker, world, count, approval or JavaScript implementation is mocked.
"""
from __future__ import annotations

import argparse
import asyncio
from http.client import HTTPResponse
import io
import json
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

from app.capture import checked_replay
from app.flow import DemoService
from app.inputs import load_inputs
from app.server import make_handler, json_bytes


class _MemorySocket:
    def __init__(self, request=b''):
        self.incoming = io.BytesIO(request)
        self.outgoing = bytearray()

    def makefile(self, mode, *args):
        return self.incoming

    def sendall(self, data):
        self.outgoing.extend(data)


class InProcessHTTP:
    """Run the application's actual HTTP protocol, replacing only socket I/O."""
    def __init__(self, payload, service):
        self.handler = make_handler(payload, service=service, test_authority='stowlm.invalid')

    def request(self, method, target, body=b'', headers=None):
        fields = {'host': 'stowlm.invalid', **{key.lower(): value for key, value in (headers or {}).items()}}
        if body:
            fields['content-length'] = str(len(body))
        request = '\r\n'.join([f'{method} {target} HTTP/1.0',
                               *(f'{key}: {value}' for key, value in fields.items())])
        socket = _MemorySocket(request.encode() + b'\r\n\r\n' + body)
        self.handler(socket, ('127.0.0.1', 1), object())
        response = HTTPResponse(_MemorySocket(bytes(socket.outgoing)))
        response.begin()
        return response.status, dict(response.getheaders()), response.read()


async def verify(args):
    from playwright.async_api import async_playwright
    from app.smoke import _source_pins

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    before = _source_pins()
    inputs = load_inputs(instance_path=args.instance)
    payload = checked_replay(inputs)
    service = DemoService(inputs.instance, inputs.plan, initial_replay=payload, discover=False)
    transport = InProcessHTTP(payload, service)
    started = monotonic()
    result = dict(browser_verified=False, instance_id=inputs.instance.id,
        schema=inputs.instance.schema_version, primary=args.primary, screenshots=[],
        transport='production HTTP handler over in-process socket adapter', console_errors=[],
        failed_requests=[], failed_responses=[], route_errors=[], application_errors=[])

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=args.chrome,
                                                   chromium_sandbox=True)
        page = await browser.new_page(viewport={'width': 1920, 'height': 1080}, device_scale_factor=1)
        page.on('pageerror', lambda error: result['console_errors'].append(str(error)))
        page.on('console', lambda message: result['console_errors'].append(message.text)
                if message.type == 'error' else None)
        page.on('requestfailed', lambda request: result['failed_requests'].append(
            dict(url=request.url, failure=request.failure)))
        page.on('response', lambda response: result['failed_responses'].append(
            dict(url=response.url, status=response.status)) if response.status >= 400 else None)

        async def route_request(route):
            request = route.request
            url = urlsplit(request.url)
            try:
                if url.hostname != 'stowlm.invalid':
                    raise ValueError('Unexpected external browser request')
                target = url.path + ('?' + url.query if url.query else '')
                status, headers, body = await asyncio.to_thread(transport.request,
                    request.method, target, request.post_data_buffer or b'', request.headers)
                headers.pop('Content-Length', None)
                await route.fulfill(status=status, headers=headers, body=body)
            except Exception as error:
                result['route_errors'].append(dict(url=request.url, error=f'{type(error).__name__}: {error}'))
                await route.abort()

        await page.route('**/*', route_request)

        async def screenshot(name, *, full_page=False):
            visible_error = page.locator('#error')
            if await visible_error.is_visible():
                result['application_errors'].append(await visible_error.inner_text())
            path = output / name
            await page.screenshot(path=str(path), full_page=full_page)
            result['screenshots'].append(name)

        try:
            await page.goto('http://stowlm.invalid/')
            await page.wait_for_selector('#instance-select[data-populated="true"]')
            await page.locator('#instance-select').select_option(inputs.instance.id)
            await page.locator('#planner-select').select_option(args.primary)
            await screenshot('01-pick-instance.png')
            await page.locator('#plan-compare').click()
            await screenshot('02-live-planning.png')
            await page.wait_for_function("""() => {
                const text=document.getElementById('solve-progress').textContent;
                return text.startsWith('Comparison ready') || text.startsWith('Planning stopped');
            }""", timeout=args.timeout_s * 1000)
            status_text = await page.locator('#solve-progress').inner_text()
            result['status_text'] = status_text
            if not status_text.startswith('Comparison ready'):
                await screenshot('03-planning-failure.png', full_page=True)
                raise RuntimeError(status_text)
            await screenshot('03-comparison.png')
            plans = service.catalog()['plans'][-2:]
            comparison = service.comparison(plans[0]['id'], plans[1]['id'])
            (output / 'comparison.json').write_bytes(json_bytes(comparison))
            result['comparable'] = comparison['comparable']
            result['plans'] = []
            for entry in plans:
                replay = service.replay(entry['id'])
                (output / f"{entry['planner']}.replay.json").write_bytes(json_bytes(replay))
                result['plans'].append(dict(planner=entry['planner'],
                    shippable=replay['metadata']['shippable'], bound=replay['checker']['bound'],
                    conditions=[c['phase'] for c in replay['checker']['conditions']]))
            if not comparison['comparable']:
                raise RuntimeError('Real plans are diagnostic; both canonical shipping gates must pass')
            await page.locator('[data-replay="left"]').click()
            await page.wait_for_selector('#operations-view:visible')
            await page.locator('#last').click()
            await screenshot('04-approved-replay.png')
            await page.locator('#checker-tab').click()
            for panel in ('stability', 'strength', 'dg', 'violations'):
                await page.locator(f'[data-checker="{panel}"]').click()
                await screenshot(f'05-checker-{panel}.png', full_page=True)
            await page.locator('#compare-tab').click()
            await page.locator('.template-explanation summary').first.click()
            await screenshot('06-offline-explanation.png', full_page=True)
            await page.set_viewport_size({'width': 390, 'height': 844})
            await screenshot('07-mobile-comparison.png', full_page=True)
            after = _source_pins()
            result['source_drift'] = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
            result['browser_verified'] = not any(result[key] for key in (
                'console_errors', 'failed_requests', 'failed_responses', 'route_errors',
                'application_errors', 'source_drift'))
            if not result['browser_verified']:
                raise RuntimeError('Browser errors or source drift prevent a clean verification')
        except Exception as error:
            result['error'] = f'{type(error).__name__}: {error}'
        finally:
            result['elapsed_s'] = monotonic() - started
            (output / 'browser-verification.json').write_bytes(json_bytes(result))
            await browser.close()
    print(json.dumps(result, indent=2))
    return 0 if result['browser_verified'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--primary', choices=('solver.scalable', 'solver.exact', 'baseline.twostep'), default='solver.scalable')
    parser.add_argument('--chrome', default='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    parser.add_argument('--timeout-s', type=int, default=180)
    args = parser.parse_args()
    try:
        return asyncio.run(verify(args))
    except Exception as error:
        args.output.mkdir(parents=True, exist_ok=True)
        failure = dict(browser_verified=False, error=f'{type(error).__name__}: {error}')
        (args.output / 'browser-verification.json').write_bytes(json_bytes(failure))
        print(json.dumps(failure, indent=2))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

"""Screenshot-free native Chrome open/print check. No downloaded browser or Python dependencies.

Run: uv run --offline --no-sync python -m experiments.mvp.pipeline.package.print_check PATH
Requires installed Google Chrome and Node with built-in WebSocket (Node 22+).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time


_DRIVER = r'''
import fs from 'node:fs/promises';
const [port, url, pdf] = process.argv.slice(1);
const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const target = pages.find(p => p.type === 'page');
if (!target) throw Error('Chrome did not expose a page target');
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {socket.onopen = resolve; socket.onerror = reject});
let id = 0;
const pending = new Map();
const errors = [], remote = [];
let loaded;
socket.onmessage = event => {
  const value = JSON.parse(event.data);
  if (value.id) {
    const waiter = pending.get(value.id);
    pending.delete(value.id);
    if (value.error) waiter.reject(Error(JSON.stringify(value.error))); else waiter.resolve(value.result);
  }
  if (value.method === 'Page.loadEventFired') loaded?.();
  if (value.method === 'Runtime.exceptionThrown') errors.push(value.params.exceptionDetails.text);
  if (value.method === 'Network.requestWillBeSent' && /^https?:/.test(value.params.request.url))
    remote.push(value.params.request.url);
};
function call(method, params={}) {
  const current = ++id;
  return new Promise((resolve, reject) => {
    pending.set(current, {resolve, reject});
    socket.send(JSON.stringify({id:current, method, params}));
  });
}
try {
  await call('Page.enable'); await call('Runtime.enable'); await call('Network.enable');
  await call('Network.setBlockedURLs', {urls:['http://*','https://*']});
  const complete = new Promise(resolve => {loaded=resolve});
  const navigation = await call('Page.navigate', {url});
  if (navigation.errorText) throw Error(navigation.errorText);
  let loadTimer;
  try {
    await Promise.race([complete, new Promise((_,reject) => {
      loadTimer=setTimeout(() => reject(Error('HTML load timeout')), 10000);
    })]);
  } finally {clearTimeout(loadTimer);}
  await call('Emulation.setEmulatedMedia', {media:'print'});
  const result = await call('Runtime.evaluate', {awaitPromise:true, returnByValue:true, expression:`
    document.fonts.ready.then(() => ({
      title: document.title, ready_state: document.readyState,
      section_count: document.querySelectorAll('.bay-page').length,
      overflow: [...document.querySelectorAll('.bay-page')].flatMap(p => {
        const diagram = p.querySelector('.diagram'), table = p.querySelector('table');
        const issues = [];
        if (p.scrollHeight > p.clientHeight+1 || p.scrollWidth > p.clientWidth+1) issues.push('page overflow');
        if (table.getBoundingClientRect().bottom > diagram.getBoundingClientRect().bottom+1) issues.push('table overflow');
        return issues.map(issue => ({bay:p.dataset.bay, issue}));
      }),
      boxes: document.querySelectorAll('[data-box]').length,
      banners: [...document.querySelectorAll('.banner')].map(p => p.textContent),
      print_stylesheet: [...document.styleSheets].some(s => [...s.cssRules].some(r => r.cssText.includes('@page')))
    }))`});
  if (result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails));
  const printed = await call('Page.printToPDF', {printBackground:true, preferCSSPageSize:true,
    displayHeaderFooter:false, landscape:true});
  await fs.writeFile(pdf, Buffer.from(printed.data, 'base64'));
  console.log(JSON.stringify({...result.result.value, console_errors:errors, remote_requests:remote}));
} finally {socket.close();}
'''


def check_print(html_path, *, pdf_path=None, no_sandbox=False) -> dict:
    """Open a local bay plan, inspect print layout, and count the resulting PDF pages."""
    html = Path(html_path).resolve(strict=True)
    chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    if not chrome.is_file() or shutil.which('node') is None:
        raise RuntimeError('Print check requires installed Google Chrome and Node 22+')
    with tempfile.TemporaryDirectory(prefix='.print-check-', dir=Path(__file__).parent) as temporary:
        work = Path(temporary)
        pdf = Path(pdf_path).resolve() if pdf_path else work / 'bay-plan.pdf'
        command = [str(chrome), '--headless', '--disable-gpu', '--no-first-run',
                   '--no-default-browser-check', '--disable-background-networking', '--disable-sync',
                   '--disable-extensions', '--disable-component-update', '--disable-breakpad',
                   '--disable-crash-reporter', '--disable-crashpad',
                   '--host-resolver-rules=MAP * ~NOTFOUND', '--remote-debugging-port=0',
                   '--disable-features=Translate,MediaRouter', f'--user-data-dir={work / "profile"}', 'about:blank']
        if no_sandbox:
            command.insert(1, '--no-sandbox')
        with (work / 'chrome.log').open('wb') as log:
            process = subprocess.Popen(command, stdout=log, stderr=log)
            try:
                port_file = work / 'profile' / 'DevToolsActivePort'
                deadline = time.monotonic() + 15
                while not port_file.exists():
                    if process.poll() is not None or time.monotonic() > deadline:
                        detail = (work / 'chrome.log').read_text(errors='replace')[-2000:]
                        raise RuntimeError(f'Chrome startup failed; exit={process.poll()}: {detail}')
                    time.sleep(.1)
                port = port_file.read_text().splitlines()[0]
                driver = subprocess.run(['node', '--input-type=module', '-e', _DRIVER,
                                         port, html.as_uri(), str(pdf)], capture_output=True,
                                        text=True, timeout=45, check=True)
                result = json.loads(driver.stdout)
                data = pdf.read_bytes()
                result['pdf_bytes'] = len(data)
                # Chrome emits page dictionaries outside compressed content streams. Count actual page objects,
                # excluding /Pages tree nodes; this does not need a general-purpose PDF dependency.
                result['pdf_pages'] = len(re.findall(rb'/Type\s*/Page\b', data))
                result['ok'] = bool(data.startswith(b'%PDF-') and result['section_count'] > 0
                                    and result['section_count'] == result['pdf_pages']
                                    and result['ready_state'] == 'complete' and not result['overflow']
                                    and not result['console_errors'] and not result['remote_requests'])
                if pdf_path:
                    result['pdf_path'] = str(pdf)
                return result
            finally:
                # Stop only the exact process this invocation started; never search other sessions' PIDs.
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html')
    parser.add_argument('--pdf')
    parser.add_argument('--no-sandbox', action='store_true', help='Disable Chrome internal sandbox for an isolated local-file check')
    arguments = parser.parse_args()
    result = check_print(arguments.html, pdf_path=arguments.pdf, no_sandbox=arguments.no_sandbox)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['ok'] else 1)

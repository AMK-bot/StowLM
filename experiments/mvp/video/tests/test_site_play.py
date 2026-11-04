"""site/check.py play/ policy (D319): JS allowed under play/ only, same-origin, no analytics, no eval."""
import importlib.util
import unittest
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "site"
spec = importlib.util.spec_from_file_location("site_check_play", SITE / "check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

GOOD_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Play</title>
<link rel="stylesheet" href="play.css"><script type="module" src="./app.js"></script></head>
<body><button onclick="go()">Go</button><a href="https://github.com/x/y">source</a>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"></svg><script>const n = 1;</script></body></html>"""
GOOD_JS = """import { plan } from './engine.js';
const data = await fetch('data/hero.json').then(r => r.json());
const more = await fetch(`./data/${name}.json`);
const w = new Worker('worker.js'); setTimeout(() => draw(data), 10);"""


def errs(files):
    return check.play_errors(files)


class Play(unittest.TestCase):
    def test_good_page_passes(self):
        self.assertEqual(errs({"index.html": GOOD_HTML, "app.js": GOOD_JS, "play.css": "body{color:#fff}"}), [])

    def test_external_fetch(self):
        for js in ["fetch('https://x.test/a.json')", 'fetch("//x.test/a")', "fetch(`http://x.test/${a}`)",
                   "importScripts('https://x.test/w.js')", "import('https://cdn.x.test/m.js')",
                   "const u = 'https://x.test/log'; fetch(u)", "new Image().src = 'https://x.test/p.gif'"]:
            self.assertTrue(errs({"app.js": js}), js)

    def test_external_script_and_css(self):
        self.assertTrue(errs({"index.html": '<html><head><script src="https://cdn.x.test/a.js"></script></head><body></body></html>'}))
        self.assertTrue(errs({"index.html": '<html><head><link rel="stylesheet" href="//fonts.x.test/a.css"></head><body></body></html>'}))
        self.assertTrue(errs({"play.css": "@import url('https://x.test/a.css');"}))
        self.assertTrue(errs({"index.html": '<html><body><img src="https://x.test/a.png"></body></html>'}))

    def test_analytics(self):
        for js in ["gtag('config','G-1')", "window.posthog.capture('x')", "navigator.sendBeacon('/log', d)",
                   "s.src='/js/analytics.js'", "mixpanel.track('x')"]:
            self.assertTrue(errs({"app.js": js}), js)

    def test_eval_of_code(self):
        for js in ["eval(code)", "new Function('return 1')()", "setTimeout('draw()', 10)",
                   "new WebSocket('/ws')", "document.write('<script src=a.js></script>')",
                   "new Worker('data:text/javascript,1')"]:
            self.assertTrue(errs({"app.js": js}), js)

    def test_index_and_replay_stay_no_js(self):
        self.assertTrue(check.markup_errors('<html><body><script src="play/app.js"></script></body></html>'))
        self.assertTrue(check.markup_errors('<html><body><button onclick="x()">b</button></body></html>'))

    def test_empty_favicon_allowed_other_data_urls_not(self):
        self.assertEqual(errs({"index.html": '<html><head><link rel="icon" href="data:,"></head><body></body></html>'}), [])
        self.assertTrue(errs({"index.html": '<html><head><link rel="icon" href="data:image/png;base64,AAAA"></head><body></body></html>'}))

    def test_javascript_url_still_rejected_in_play(self):
        self.assertTrue(errs({"index.html": '<html><body><a href="javascript:x()">x</a></body></html>'}))


class SubPath(unittest.TestCase):
    """code14 P2-3: hosted at /StowLM/, a single-slash URL leaves the project."""
    def test_root_relative_html(self):
        for h in ['<script src="/play/app.js"></script>', '<link rel="stylesheet" href="/play/play.css">',
                  '<img src="/media/a.png">', '<a href="/replay/">r</a>']:
            self.assertTrue(errs({"index.html": f"<html><head></head><body>{h}</body></html>"}), h)

    def test_root_relative_js(self):
        for js in ["fetch('/api/state')", 'fetch("/api/v6/read", {method: "POST"})', "fetch(`/api/${x}`)",
                   "import('/play/mod.js')", "new Worker('/w.js')", "new Audio('/a.mp3')",
                   "el.innerHTML = `<img src=\"/api/v6/asset/a.png\">`"]:
            self.assertTrue(errs({"app.js": js}), js)

    def test_root_relative_css(self):
        self.assertTrue(errs({"play.css": "body{background:url('/media/bg.png')}"}))

    def test_relative_still_fine(self):
        self.assertEqual(errs({"app.js": "fetch('api/state'); fetch('./rec/index.json'); el.innerHTML = '<img src=\"api/v6/asset/a.png\">';",
                               "play.css": "body{background:url('media/bg.png')}"}), [])


class Spellings(unittest.TestCase):
    """code14 P2-4: ordinary spellings of eval and of an external URL."""
    def test_function_without_new(self):
        for js in ["Function('return 1')()", "const f = Function ('a', 'return a')", "(()=>{}).constructor('return 1')()",
                   "window['eval'](s)", "globalThis[\"Function\"]('x')"]:
            self.assertTrue(errs({"app.js": js}), js)

    def test_escaped_urls(self):
        for js in [r"fetch('\x68ttps://example.com/collect')", r"const u = '\u0068ttps://x.test/a'; fetch(u)",
                   r"fetch('\u{68}ttp://x.test')", r"const u = '\x2f\x2fx.test/a'",
                   "fetch(String.fromCharCode(104,116,116,112,115,58,47,47))", "fetch(atob('aHR0cHM6Ly94LnRlc3Q='))"]:
            self.assertTrue(errs({"app.js": js}), js)

    def test_ordinary_escapes_and_column_letters_pass(self):
        self.assertEqual(errs({"app.js": "const dot = '\u00b7'; const col = String.fromCharCode(65 + i); const MyFunction = 1; obj.Function = 2;"}), [])


class RecordedRun(unittest.TestCase):
    """The recorded Functional app run (static_play/build.py): an SVG namespace name and the app's own recorded data files."""
    def test_svg_namespace_for_create_element_ns(self):
        self.assertEqual(errs({"app.js": "var NS = 'http://www.w3.org/2000/svg'; document.createElementNS(NS, 'svg');"}), [])
        self.assertTrue(errs({"app.js": "fetch('http://www.w3.org/2000/svg')"}))
        self.assertTrue(errs({"app.js": "const u = 'http://www.w3.org/2000/svg/../x.js'"}))

    def test_recorded_json_under_api_is_data(self):
        self.assertTrue(check.play_data("api/functional/calls/c1/file/runs/1/outcome.json"))
        self.assertFalse(check.play_data("api/functional/calls/c1/file/runs/1/package/bay-plan.html"))
        self.assertFalse(check.play_data("rec/index.json") or check.play_data("build.json") or check.play_data("app.js"))

    def test_html_under_api_is_still_a_page(self):
        bad = '<html><head><script src="https://x.test/a.js"></script></head><body></body></html>'
        self.assertTrue(errs({"api/functional/calls/c1/file/runs/1/package/bay-plan.html": bad}))


if __name__ == "__main__":
    unittest.main()

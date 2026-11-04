"""build.py's own guards: a failed app start stops the app it started (code14 P2-1), and every built page carries the CSP (P2-4).

    uv run --offline --no-sync pytest experiments/mvp/ui/static_play/test_build_guards.py -q

The launch tests use a fake Popen, clock, urlopen and killpg: no process, port or file outside tmp_path.
"""
from __future__ import annotations

import importlib.util
import re
import signal
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # build.py imports walk from here
if "static_play_build" not in sys.modules:  # by path, under its own name: experiments/mvp/ui/replay/build.py is also "build"
    _spec = importlib.util.spec_from_file_location("static_play_build", HERE / "build.py")
    sys.modules["static_play_build"] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules["static_play_build"])
build = sys.modules["static_play_build"]

CSP = ('<meta http-equiv="Content-Security-Policy" content="default-src \'self\'; script-src \'self\'; style-src \'self\'; '
       'style-src-attr \'unsafe-inline\'; img-src \'self\' data:; media-src \'self\'; font-src \'self\'; connect-src \'self\'; '
       'worker-src \'none\'; object-src \'none\'; base-uri \'self\'; form-action \'none\'">')
CHARSET = '<meta charset="utf-8">'


class FakeProc:
    """Started, never warm, never exits by itself."""
    pid = 424242

    def __init__(self, *a, **kw):
        self.kw, self.waited, self.returncode = kw, [], None
        FakeProc.last = self

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.waited.append(timeout)
        self.returncode = -15
        return self.returncode


@pytest.fixture
def fake(monkeypatch, tmp_path):
    kills = []
    monkeypatch.setattr(build, "HERE", tmp_path)
    monkeypatch.setattr(build.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(build.os, "killpg", lambda pid, sig: kills.append((pid, sig)))
    def never(*a, **kw):
        raise OSError("connection refused")
    monkeypatch.setattr(build.urllib.request, "urlopen", never)
    return kills


def _assert_stopped(kills, tmp_path):
    proc = FakeProc.last
    assert proc.kw.get("start_new_session") is True  # its own process group, so killpg reaches only what it started
    assert kills and kills[0] == (FakeProc.pid, signal.SIGTERM) and {pid for pid, _ in kills} == {FakeProc.pid}
    assert proc.waited, "the started app was not waited for"
    assert not (tmp_path / "app.pid").exists()


def test_launch_stops_its_app_when_it_never_answers(fake, monkeypatch, tmp_path):
    clock = iter([0.0, 10.0, 301.0, 302.0])
    monkeypatch.setattr(build.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(build.time, "sleep", lambda s: None)
    with pytest.raises(SystemExit, match="did not start"):
        build.launch(9051, None, log=lambda m: None)
    _assert_stopped(fake, tmp_path)


def test_launch_stops_its_app_when_the_wait_raises(fake, monkeypatch, tmp_path):
    def boom(s):
        raise OSError("log unreadable")
    monkeypatch.setattr(build.time, "sleep", boom)
    with pytest.raises(OSError):
        build.launch(9051, None, log=lambda m: None)
    _assert_stopped(fake, tmp_path)


def test_launch_stops_its_app_on_interrupt(fake, monkeypatch, tmp_path):
    def interrupt(s):
        raise KeyboardInterrupt
    monkeypatch.setattr(build.time, "sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        build.launch(9051, None, log=lambda m: None)
    _assert_stopped(fake, tmp_path)


def test_launch_returns_the_ready_app_running(fake, monkeypatch, tmp_path):
    class Ok:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *a, **kw: Ok())
    proc = build.launch(9051, None, log=lambda m: None)
    assert proc is FakeProc.last and not fake and not proc.waited and (tmp_path / "app.pid").read_text() == f"{FakeProc.pid}\n"


def test_harden_html_puts_the_csp_after_the_charset_and_moves_inline_styles_out():
    page = '<!doctype html><html><head><meta charset="utf-8"><title>x</title><style>b{color:red}</style></head><body style="margin:0">x</body></html>'
    out = build.harden_html("api/v6/asset/doc.html", page)
    html = out["api/v6/asset/doc.html"]
    assert html.count(CSP) == 1 and html.index(CHARSET) + len(CHARSET) == html.index(CSP)
    assert "<style" not in html and '<link rel="stylesheet" href="doc.css">' in html and out["api/v6/asset/doc.css"] == "b{color:red}"
    assert 'style="margin:0"' in html  # attributes are allowed by style-src-attr
    assert html.count('<link rel="icon" href="data:,">') == 1  # the browser asks no /favicon.ico outside the folder
    with pytest.raises(SystemExit):
        build.harden_html("x.html", "<html><head><title>no charset</title></head></html>")


@pytest.mark.skipif(not (build.OUT / "rec/index.json").is_file(), reason="playback build not made (run build.py)")
def test_every_built_page_carries_the_csp_once_right_after_the_charset():
    pages = sorted(build.OUT.rglob("*.htm*"))
    assert build.OUT / "index.html" in pages and len(pages) >= 2
    for p in pages:
        html = p.read_text()
        assert html.count("Content-Security-Policy") == 1 and html.count(CSP) == 1, p.name
        assert html.count(CHARSET) == 1 and html.index(CHARSET) + len(CHARSET) == html.index(CSP), p.name
        assert not re.search(r"<style\b", html, re.I) and not re.search(r"<script\b(?![^>]*\bsrc=)", html, re.I), p.name

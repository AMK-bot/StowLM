"""site/check.py markup policy: each automatic execution or network bypass is rejected (final-code P2-2)."""
import importlib.util
import unittest
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "site"
spec = importlib.util.spec_from_file_location("site_check", SITE / "check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

OK = '<!doctype html><html><head><meta charset="utf-8"><title>t</title></head><body>{}</body></html>'


def errs(body):
    return check.markup_errors(OK.format(body))


class Policy(unittest.TestCase):
    def assertRejected(self, body):
        self.assertTrue(errs(body), f"bypass passed: {body}")

    def test_clean_page_passes(self):
        self.assertEqual(errs('<p><a href="replay/index.html">r</a> <a href="https://github.com/x">g</a></p>'
                              '<video controls poster="media/p.jpg"><source src="media/l.mp4"></video>'), [])

    def test_event_handlers(self):
        for body in ['<p onclick="fetch(1)">x</p>', '<img src="a.png" onerror="fetch(1)">',
                     '<p ONMOUSEOVER="x()">x</p>', '<svg onload="x()"></svg>', '<video src="a.mp4" onplay="x()"></video>']:
            self.assertRejected(body)
        self.assertTrue(check.markup_errors('<html><body onload="fetch(\'//x\',{method:\'POST\'})"></body></html>'))

    def test_meta_refresh(self):
        self.assertTrue(check.markup_errors('<html><head><meta http-equiv="Refresh" content="0;url=https://x.test/"></head><body></body></html>'))

    def test_anchor_ping(self):
        self.assertRejected('<a href="replay/index.html" ping="https://x.test/log">r</a>')

    def test_javascript_urls(self):
        for body in ['<a href="javascript:fetch(1)">x</a>', '<a href=" JaVaScRiPt:x()">x</a>',
                     '<a href="jav&#x09;ascript:x()">x</a>', '<img src="vbscript:x">',
                     '<a href="data:text/html,<script>x()</script>">x</a>', '<a xlink:href="javascript:x()">x</a>']:
            self.assertRejected(body)

    def test_srcdoc_and_frames(self):
        self.assertRejected('<iframe srcdoc="<script>x()</script>"></iframe>')
        self.assertRejected('<div srcdoc="x"></div>')

    def test_forms(self):
        self.assertRejected('<form action="https://x.test/"><input name="a"></form>')
        self.assertRejected('<button formaction="https://x.test/">b</button>')
        self.assertRejected('<base href="https://x.test/">')

    def test_scripts_and_remote_urls_still_rejected(self):
        self.assertRejected('<script>fetch(1)</script>')
        self.assertRejected('<script src="https://x.test/a.js"></script>')
        self.assertRejected('<img src="https://x.test/a.png">')
        self.assertRejected('<img src="//x.test/a.png">')


class Pairing(unittest.TestCase):
    def test_86_needs_6_of_34(self):
        self.assertTrue(check.pairing_errors("86 of 100 read exactly; 61 of 66 rules."))
        self.assertEqual(check.pairing_errors("86 of 100 read exactly; 61 of 66 rules; a rule from 6 of 34 it should have refused."), [])


if __name__ == "__main__":
    unittest.main()

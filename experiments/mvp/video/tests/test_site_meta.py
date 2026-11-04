"""site/check.py: social-card meta tags and Pages sub-path rules."""
import importlib.util
import unittest
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "site"
spec = importlib.util.spec_from_file_location("site_check_meta", SITE / "check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

HEAD = ('<!doctype html><html><head><meta charset="utf-8"><title>t</title>{}</head>'
        '<body><a href="replay/index.html">r</a></body></html>')
DRAFT = ('<meta property="og:url" content="{{pages_url}}/">'
         '<meta property="og:image" content="{{pages_url}}/media/launch-poster.jpg">'
         '<meta name="twitter:card" content="summary_large_image">'
         '<meta name="twitter:image" content="{{pages_url}}/media/launch-poster.jpg">')


class Meta(unittest.TestCase):
    def test_placeholder_meta_is_not_a_network_fetch(self):
        self.assertEqual(check.markup_errors(HEAD.format(DRAFT)), [])

    def test_absolute_https_meta_passes(self):
        filled = DRAFT.replace("{{pages_url}}", "https://user.github.io/stowlm")
        self.assertEqual(check.markup_errors(HEAD.format(filled)), [])

    def test_release_flags_pages_placeholder(self):
        found = check.placeholders(HEAD.format(DRAFT))
        self.assertTrue(any("{{pages_url}}" in f for f in found), found)

    def test_relative_or_http_social_image_rejected(self):
        for bad in ['<meta property="og:image" content="media/launch-poster.jpg">',
                    '<meta name="twitter:image" content="http://x.test/a.jpg">',
                    '<meta property="og:image" content="javascript:x()">']:
            self.assertTrue(check.markup_errors(HEAD.format(bad)), bad)

    def test_root_relative_urls_rejected(self):
        for body in ['<img src="/media/a.png">', '<a href="/replay/index.html">r</a>',
                     '<video poster="/media/p.jpg"></video>']:
            self.assertTrue(check.markup_errors(HEAD.format("").replace("<body>", "<body>" + body)), body)


if __name__ == "__main__":
    unittest.main()

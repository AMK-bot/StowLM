"""Offline and honesty check for the StowLM landing page (stdlib only).

Run from the repo root:
  UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-project --python 3.12 \
      python experiments/mvp/video/site/check.py

Fails (exit 1) if index.html or styles.css reach the network, if index.html uses
wording banned by docs/headline.md, if "sealed" appears outside
"not the pre-registered sealed claim", if the music credit is missing, if any
"24 of 24" lacks the held-out label nearby, if the HTML is malformed, or if the
replay copy no longer matches the sha256 recorded in README.md.
The replay copy (replay/index.html) is excluded from the wording checks.

  --release   also fail on any placeholder still in the page (the {{repo_url}} and
              {{demo_video_url}} links, data-todo / *-pending hrefs, class "todo",
              "link pending" text, the {{pages_url}} social-card URLs) and on missing
              media/launch.mp4 or launch-poster.jpg.
Also fails on root-relative URLs ("/media/x"), which break a project Pages site served
under /<repo>/. og:/twitter: image and URL meta content is never fetched by the page; it must
be {{pages_url}}/... (draft) or an absolute https URL.
              Drafts are checked without the flag and may keep placeholders (code12 P3).
"""

from __future__ import annotations

import hashlib
import html
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

SITE = Path(__file__).resolve().parent
INDEX = SITE / "index.html"
CSS_FILES = sorted(SITE.glob("*.css"))
REPLAY = SITE / "replay" / "index.html"
README = SITE / "README.md"

ALLOWED_HOSTS = {"ende.app", "creativecommons.org", "github.com", "huggingface.co"}
BANNED = [
    "industry level", "industry-level", "industry grade", "industry-grade",
    "production ready", "production-ready", "real-time", "real time", "instant",
    "certified", "class-approved", "whole ship", "full ship", "empty ship",
    "beats spark", "beats navis", "62 rules", "wi-fi off", "wifi off",
    "solves real ships", "fewer crane moves", "never guessed", "anyone can verify",
    "learned sight", "learned physics", "beyond simple rules", "32 of 32",
    "58 of 72", "stowlm planned", "the replacement", "run once", "frozen once",
    "proves the loop", "stays on the laptop", "wrong bays", "no plan exists",
    "no plan is possible",
]
SEALED_OK = "not the pre-registered sealed claim"
MUSIC = "Music: Sascha Ende, ende.app (CC BY 4.0)"
HELD_OUT_LABEL = ("held-out maps, frozen pipeline, run after two interrupted attempts; "
                  "not the pre-registered sealed claim")
EXT1_LABEL = "fresh held-out extension, run after the freeze; not the pre-registered sealed claim"  # D333
LABEL_WINDOW = 600  # characters of page text either side of "24 of 24"
# Automatic execution or network: event handlers (on*), anchor ping, srcdoc, forms, base, refresh.
BANNED_TAGS = {"iframe", "object", "embed", "form", "base", "frame", "frameset", "portal", "applet"}
BANNED_ATTRS = {"ping", "srcdoc", "formaction", "action"}
URL_ATTRS = ("src", "href", "poster", "srcset", "data", "background", "cite", "longdesc", "manifest",
             "icon", "xlink:href")
EXEC_URL = re.compile(r"(?i)(javascript|vbscript|livescript):|data:(text/html|image/svg|application/xhtml)")
SOCIAL_URL_META = {"og:url", "og:image", "og:image:url", "og:image:secure_url", "og:video", "twitter:image"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "source", "track", "wbr"}


class Page(HTMLParser):
    def __init__(self, allow_scripts: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.allow_scripts = allow_scripts
        self.stack: list[tuple[str, int]] = []
        self.errors: list[str] = []
        self.urls: list[tuple[str, str, str]] = []  # (tag, attr, value)
        self.remote_scripts: list[str] = []
        self.text: list[str] = []
        self.skip = 0
        self.social_images: list[tuple[str, str]] = []  # (og:/twitter: name, content)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        line = self.getpos()[0]
        for k, v in attrs:  # restricted attribute policy (final-code P2-2)
            k = k.lower()
            if k.startswith("on"):
                self.errors.append(f"line {line}: event handler <{tag} {k}> is not allowed")
            elif k in BANNED_ATTRS:
                self.errors.append(f"line {line}: <{tag} {k}> is not allowed")
            if v and EXEC_URL.match(re.sub(r"[\x00-\x20]+", "", html.unescape(v))):
                self.errors.append(f"line {line}: executable URL in <{tag} {k}>")
        if tag == "meta" and (a.get("property") or a.get("name") or "").lower() in SOCIAL_URL_META:
            self.social_images.append(((a.get("property") or a.get("name")).lower(), a.get("content") or ""))
        if tag == "meta" and (a.get("http-equiv") or "").strip().lower() == "refresh":
            self.errors.append(f"line {line}: <meta http-equiv=refresh> is not allowed")
        for k in URL_ATTRS:
            if a.get(k):
                self.urls.append((tag, k, a[k]))
        if tag == "script" and not self.allow_scripts:
            self.remote_scripts.append(a.get("src") or "<inline script>")
        if tag in BANNED_TAGS:
            self.errors.append(f"<{tag}> is not allowed")
        if tag in ("style", "script"):
            self.skip += 1
        if tag not in VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1][0] != tag:
            want = self.stack[-1] if self.stack else None
            self.errors.append(f"line {self.getpos()[0]}: </{tag}> does not close {want}")
            return
        self.stack.pop()
        if tag in ("style", "script"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)


def norm(s: str) -> str:
    s = s.replace("’", "'").replace("–", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", s).lower()


def pairing_errors(text: str) -> list[str]:
    """logic13: the 86 and 61 figures (new-wording set) never appear without the 6 of 34 beside them."""
    fails = []
    for m in re.finditer(r"(?<!\d)(86 of 100|61 of 66)", text):
        if "6 of 34" not in text[max(0, m.start() - 400):m.end() + 400]:
            fails.append(f'"{m.group(1)}" without "6 of 34" beside it (logic13)')
    return fails


def placeholders(raw: str) -> list[str]:
    found = []
    for pat, what in [(r"\{\{[^}]*\}\}", "template placeholder"), (r"data-todo=", "data-todo attribute"),
                      (r'href="#[^"]*-pending"', "pending link"), (r'class="[^"]*\btodo\b', "todo class"),
                      (r"link pending|\[placeholder\]", "pending text")]:
        for m in re.finditer(pat, raw, re.I):
            line = raw.count("\n", 0, m.start()) + 1
            found.append(f"release: {what} at index.html:{line}: {m.group(0)[:60]!r}")
    for f in ["launch.mp4", "launch-poster.jpg"]:
        if not (SITE / "media" / f).is_file():
            found.append(f"release: media/{f} missing")
    return found


def markup_errors(raw: str) -> list[str]:
    """Structure, the attribute policy, scripts and URLs of one HTML string. Tested in tests/."""
    fails: list[str] = []
    p = Page()
    p.feed(raw)
    p.close()
    fails += [f"html: {e}" for e in p.errors]
    if p.stack:
        fails.append(f"html: unclosed tags {p.stack}")

    # Network: only the allowed external hosts; everything else relative.
    for tag, attr, val in p.urls:
        u = urlparse(val.strip())
        if u.scheme in ("http", "https") or val.strip().startswith("//"):
            host = (u.hostname or "").lower()
            if tag != "a" or attr != "href" or not any(
                    host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
                fails.append(f"remote URL not allowed: <{tag} {attr}={val!r}>")
        elif u.scheme and u.scheme not in ("mailto",):
            fails.append(f"URL scheme not allowed: <{tag} {attr}={val!r}>")
        elif val.strip().startswith("/"):
            fails.append(f"root-relative URL breaks a project Pages site (/<repo>/): <{tag} {attr}={val!r}>")
    # Social-card meta content is read by link unfurlers, never fetched by the page: allowed as a
    # {{pages_url}} placeholder (draft; --release flags it) or an absolute https URL.
    for name, content in p.social_images:
        c = content.strip()
        if not (c.startswith("{{pages_url}}/") or urlparse(c).scheme == "https"):
            fails.append(f"<meta {name}> must be {{{{pages_url}}}}/... or an absolute https URL: {c!r}")
    for s in p.remote_scripts:
        fails.append(f"script not allowed (page must work without JS): {s}")
    return fails


PLAY = SITE / "play"
PLAY_TEXT = {".html", ".htm", ".js", ".mjs", ".css", ".json", ".svg"}  # code and markup the page loads; docs (.md, .txt) are not executed
# Absolute or protocol-relative URLs (any scheme that reaches another origin) inside play/ code.
ABS_URL = re.compile(r"""(?i)\b(?:https?|wss?|ftp)://|["'`]\s*//[a-z0-9.-]+\.[a-z]{2,}""")
NET_CALL = re.compile(r"\b(fetch|importScripts|import|(?:new\s+)?(?:Shared)?Worker|new\s+Audio|new\s+EventSource|open)\s*\(\s*([^)]{0,200})", re.I)
# A resource URL that starts with a single slash resolves to the domain root and breaks project Pages hosting (code14 P2-3).
ROOT_REL = re.compile(r"""^["'`]\s*/(?!/)""")
# src/href/action written inside JS strings (innerHTML templates) or CSS url()
JS_ATTR_ROOT = re.compile(r"""(?i)\b(?:src|href|poster|action|data)\s*=\s*\\?["'`]?\s*/(?!/)[\w.~-]""")
CSS_URL_ROOT = re.compile(r"""(?i)url\(\s*["']?\s*/(?!/)""")
STR_LIT = re.compile(r"""'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`""")
ESCAPE = re.compile(r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|u\{[0-9a-fA-F]+\})")


def decode_js_escapes(lit: str) -> str:
    """Decode \\xNN, \\uNNNN and \\u{N} in a JS string literal body (code14 P2-4); other escapes are left as they are."""
    def one(m):
        e = m.group(0)
        return chr(int(e[2:4], 16)) if e[1] == "x" else chr(int(e[3:-1] if e[2] == "{" else e[2:], 16))
    return ESCAPE.sub(one, lit)
FORBIDDEN_JS = [
    (r"\beval\s*\(", "eval()"), (r"(?<![\w$.])Function\s*\(", "Function() (with or without new)"),
    (r"(?<![\w$])(?:window|self|globalThis)\s*\[\s*[\"'`](?:eval|Function)", "indirect eval or Function"),
    (r"\.constructor\s*\(\s*[\"'`]", "constructor() of a string (Function)"),
    (r"\b(?:atob)\s*\(", "atob() (decoded strings can hide URLs or code)"),
    (r"\bString\.fromCharCode\s*\(\s*\d+\s*(?:,\s*\d+\s*){2,}\)", "String.fromCharCode() of a literal list (spelled-out string)"),
    (r"\bset(?:Timeout|Interval)\s*\(\s*[\"'`]", "string timer (eval)"),
    (r"\bnavigator\.sendBeacon\b", "sendBeacon"), (r"\bnew\s+(?:WebSocket|EventSource)\b", "WebSocket/EventSource"),
    (r"\bnew\s+(?:Shared)?Worker\s*\(\s*[\"'`](?:https?:|//|data:|blob:)", "remote or data: worker"),
    (r"document\.write\s*\([^)]*<script", "document.write of a script"),
    (r"(?i)google-analytics|googletagmanager|\bgtag\s*\(|plausible\.io|\bposthog\b|\bmixpanel\b|"
     r"\bamplitude\b|\bhotjar\b|\bsegment\.(?:io|com)\b|\bmatomo\b|\bumami\b|clarity\.ms|\banalytics\.js\b",
     "analytics"),
]


def play_errors(files: dict) -> list:
    """play/ may run JavaScript, under its own policy (D319): same-origin URLs only, no fetch or import of
    absolute or external URLs, no analytics, no eval of code. index.html and replay/ stay no-JS.
    files maps a play/-relative name to its text. Tested in tests/test_site_play.py."""
    fails = []
    hosts = "|".join(re.escape(h) for h in sorted(ALLOWED_HOSTS))
    for name, text in sorted(files.items()):
        where = f"play/{name}"
        # Not network: XML namespace names, and plain <a href> links to the allowed hosts (a user's click).
        scan = re.sub(r"""xmlns(?::\w+)?\s*=\s*["']https?://www\.w3\.org/[^"']*["']""", "", text)
        scan = re.sub(r"""(["'`])http://www\.w3\.org/(?:2000/svg|1999/xhtml|1999/xlink)\1""", "", scan)  # a namespace name passed to createElementNS
        scan = re.sub(rf"""(<a\b[^>]*\bhref\s*=\s*["'])https://(?:[\w-]+\.)*(?:{hosts})(?:/[^"']*)?(["'])""", r"\1#\2", scan, flags=re.I)
        if ABS_URL.search(scan):
            fails.append(f"{where}: absolute or external URL ({ABS_URL.search(scan).group(0)!r}); play/ uses same-origin URLs only")
        for lit in STR_LIT.findall(text):  # escaped spellings: '\x68ttps://…', '\u002f\u002f…'
            if ESCAPE.search(lit):
                dec = decode_js_escapes(lit)
                if ABS_URL.search(dec) or re.search(r"(?i)javascript:|data:text/html", dec):
                    fails.append(f"{where}: escaped string decodes to a URL or code: {lit[:60]!r}")
        for m in NET_CALL.finditer(text):
            arg = decode_js_escapes(m.group(2).strip())
            what = re.sub(r"\s+", " ", m.group(1))
            if re.match(r"""(?i)["'`]\s*(?:[a-z][a-z0-9+.-]*:|//)""", arg):
                fails.append(f"{where}: {what}() of a non-relative URL: {arg[:60]!r}")
            elif ROOT_REL.match(arg):
                fails.append(f"{where}: {what}() of a root-relative URL {arg[:60]!r}; use a relative path so it works under a Pages sub-path")
        if name.lower().endswith((".js", ".mjs", ".html", ".htm")):
            for m in JS_ATTR_ROOT.finditer(text):
                fails.append(f"{where}: root-relative resource URL {m.group(0)[:60]!r}; use a relative path")
        if name.lower().endswith((".css", ".html", ".htm")):
            for m in CSS_URL_ROOT.finditer(text):
                fails.append(f"{where}: root-relative CSS url() {m.group(0)!r}")
        for pat, what in FORBIDDEN_JS:
            if re.search(pat, text):
                fails.append(f"{where}: {what} is not allowed")
        if name.lower().endswith((".html", ".htm")):
            p = Page(allow_scripts=True)
            p.feed(text)
            p.close()
            fails += [f"{where}: html: {e}" for e in p.errors if "event handler" not in e]
            for tag, attr, val in p.urls:
                v = val.strip()
                if v == "data:,":  # empty favicon: stops the browser's /favicon.ico request
                    continue
                u = urlparse(v)
                if v.startswith("/") and not v.startswith("//"):
                    fails.append(f"{where}: <{tag} {attr}={v[:60]!r}> is root-relative; use a relative path for Pages sub-paths")
                    continue
                if (u.scheme or v.startswith("//")) and not (tag == "a" and attr == "href" and u.scheme in ("http", "https")
                        and any((u.hostname or "").lower().endswith(h) for h in ALLOWED_HOSTS)):
                    if u.scheme not in ("mailto",):
                        fails.append(f"{where}: <{tag} {attr}={v[:60]!r}> is not same-origin")
    return fails


def play_data(rel: str) -> bool:
    """A recorded data file of the app's own (play/api/...): JSON the page reads, or a document it links (a receipt, an outcome
    that cites its public sources). Parsed or downloaded, never executed, so it isn't held to the code policy; HTML under
    api/ is a page and still is."""
    return rel.startswith("api/") and rel.lower().endswith(".json")


def load_play() -> dict:
    return {str(f.relative_to(PLAY)): f.read_text(encoding="utf-8", errors="replace")
            for f in sorted(PLAY.rglob("*")) if f.is_file() and f.suffix.lower() in PLAY_TEXT
            and not play_data(str(f.relative_to(PLAY)))}


def main() -> int:
    release = "--release" in sys.argv[1:]
    fails: list[str] = []
    raw = INDEX.read_text(encoding="utf-8")
    if release:
        fails += placeholders(raw)
    fails += markup_errors(raw)
    p = Page()
    p.feed(raw)
    p.close()
    for m in re.finditer(r"<link\b[^>]*>", raw, re.I):
        if re.search(r"""href\s*=\s*["']?\s*(https?:)?//""", m.group(0), re.I):
            fails.append(f"remote <link>: {m.group(0)}")
    for css in CSS_FILES + [INDEX]:
        body = css.read_text(encoding="utf-8")
        if css.suffix == ".html":
            body = " ".join(re.findall(r"<style\b[^>]*>(.*?)</style>", body, re.S | re.I)
                            + re.findall(r"""style\s*=\s*"([^"]*)\"""", body, re.I))
        if re.search(r"@import", body, re.I):
            fails.append(f"{css.name}: @import not allowed")
        if re.search(r"url\(\s*['\"]?\s*(https?:)?//", body, re.I):
            fails.append(f"{css.name}: remote url() not allowed")
        if re.search(r"@font-face", body, re.I):
            fails.append(f"{css.name}: @font-face not allowed (system fonts only)")

    # Wording: visible text plus attribute values and comments of index.html.
    whole = norm(html.unescape(raw))
    for phrase in BANNED:
        if re.search(r"(?<![a-z])" + re.escape(phrase), whole):
            fails.append(f"banned phrase: {phrase!r}")
    without_ok = whole.replace(SEALED_OK, "")
    # File paths such as .../pipeline/sealed/runs/... name a folder, not a claim.
    without_ok = re.sub(r"[\w./-]*/sealed/[\w./-]*", "", without_ok)
    if "sealed" in without_ok:
        fails.append('"sealed" appears outside "not the pre-registered sealed claim"')

    text = norm(html.unescape("".join(p.text)))
    if norm(MUSIC) not in text:
        fails.append(f"music credit missing: {MUSIC!r}")
    fails += pairing_errors(text)
    label = norm(HELD_OUT_LABEL)
    for m in re.finditer(r"(?<!\d)24 of 24", text):
        lo, hi = max(0, m.start() - LABEL_WINDOW), m.end() + LABEL_WINDOW
        if label not in text[lo:hi] and EXT1_LABEL not in text[lo:hi]:
            fails.append(f'"24 of 24" at text offset {m.start()} lacks the held-out label')

    # play/ (built by p22): JavaScript allowed under its own policy; absent is fine for a draft.
    if PLAY.is_dir():
        fails += play_errors(load_play())
    elif release:
        fails.append("release: play/ is missing (index.html links to play/index.html)")

    # Replay copy must match the sha256 recorded in README.md.
    digest = hashlib.sha256(REPLAY.read_bytes()).hexdigest()
    if digest not in README.read_text(encoding="utf-8"):
        fails.append(f"replay/index.html sha256 {digest} is not the one in README.md")
    if re.search(r"<script\b|https?://", REPLAY.read_text(encoding="utf-8"), re.I):
        fails.append("replay/index.html has a script or remote URL")

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print(f"OK{' (release)' if release else ' (draft: placeholders allowed)'}: {INDEX.relative_to(SITE.parents[3])} well-formed, offline, wording clean; "
          f"replay sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

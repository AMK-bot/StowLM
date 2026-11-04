# GitHub Pages sub-path dry run

**Date:** 2 Nov 2026 (about 22:10 EST). **Result:** the page works under a project-site sub-path. First pass: the only broken link was `play/index.html`, because `site/play/` didn't exist yet. `play/` appeared at 22:08 (p22, still in progress); a second pass with it is below.

## Method

1. Copied `site/` as it stood (index.html, styles.css, replay/, media/ including charts/, the PDF and favicon.svg) to a scratch folder, `.../scratchpad/pages/stowlm/`, so it is served at `/stowlm/`, as a project Pages site serves `https://<user>.github.io/<repo>/`.
2. Served the scratch parent with `python3 -m http.server <free port> --bind 127.0.0.1` (one process; stopped by its own PID afterwards).
3. Static crawl (Node fetch): every `href`, `src`, `poster` and `srcset` in `index.html` and `replay/index.html`, plus every CSS `url()` in `styles.css` and inline `<style>`, resolved against the page URL and requested. `play/` was absent, so none of its pages were crawled.
4. Headless Chrome (puppeteer-core, installed Chrome, no visible window) loaded `index.html` and `replay/index.html` at 390 px and 1280 px wide. Every request not to `127.0.0.1:<port>` was aborted. It recorded console errors and warnings, page errors, failed requests, responses of 400 or more, broken images (after scrolling to load lazy images) and horizontal scroll (`scrollWidth > clientWidth`).

## Findings

| Check | Result |
|---|---|
| Local references crawled | 19 (14 distinct): favicon, stylesheet, launch video and poster (twice), x-ray video and poster, pipeline diagram, four charts, the bay-plan PDF, replay link (three times), play link (twice) |
| Status 200 under `/stowlm/` | All except `play/index.html` (404: folder not built yet) |
| Root-relative URLs (`/media/...`) | None. `check.py` now fails on any root-relative URL |
| External references | Three plain links only (ende.app, creativecommons.org, github.com/latent-spaces/brag), clicked by a user, never fetched by the page |
| CSS `url()` | None in `styles.css` |
| `index.html`, 390 px and 1280 px | No console errors, no failed or blocked requests, no broken images, no horizontal scroll |
| `replay/index.html`, 1280 px | Clean |
| `replay/index.html`, 390 px | One console error: Chrome's automatic `/favicon.ico` request at the server root returned 404. The replay copy is byte-for-byte and has no icon link, so it isn't edited here. On GitHub Pages the root favicon request goes to `<user>.github.io/favicon.ico` and is harmless |

An earlier pass showed the same root `/favicon.ico` 404 on `index.html`; the page now links `media/favicon.svg`, and the re-run is clean.

## Notes for publishing

- Everything in `site/` is published if the folder is pushed as is, including `README.md`, `check.py`, this file and the 3 MB bay-plan PDF. Leave them or drop them from the Pages branch; nothing on the page depends on the first three.
- Re-run this dry run after `play/` lands: its pages, scripts and data files need the same crawl.
- Social-card tags use `{{pages_url}}`; fill it with the Pages URL (no trailing slash). Unfurlers need absolute URLs, so a relative path won't do.

## Second pass, with `play/` (2 Nov 2026, about 22:12 EST; play/ still being built by p22)

Same method; the crawler also followed every `.html` page under `play/` and the CSS files they link.

| Check | Result |
|---|---|
| Pages and stylesheets fetched | 18, all 200: `index.html`, `replay/index.html`, `play/index.html`, `play/api/v6/asset/{bay-plan,package-not-included,replay}.html`, `styles.css` and 11 `play/*.css` |
| Local references crawled | 53, all 200 under `/stowlm/` (including `play/index.html` from the landing page) |
| Root-relative URLs | None |
| Console errors, failed or blocked requests | None on any `play/` page at 390 or 1280 px |
| Horizontal scroll | `play/index.html` at 390 px (page 1280 px wide; header KPI strip, a 16:9 desktop app). `play/api/v6/asset/bay-plan.html` at both widths (A3 print layout, 1111 and 1512 px; expected for a print sheet). Not fixed here: `play/` belongs to p22 |
| `check.py` (draft) | Fails on `play/README.md` line 16, a local-preview instruction `http://127.0.0.1:8875/...` in prose, which the `play/` absolute-URL rule flags. For p22: reword it or exclude `.md` from the play scan |

`play/`'s JavaScript fetches (`api/...` recordings) ran inside these page loads with external requests blocked and produced no failures, but only the first screen of each page was exercised; clicks through the stage weren't driven.


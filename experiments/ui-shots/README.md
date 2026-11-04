# UI capture status

Screenshots could not be produced in the executive sandbox: Python localhost
`socket.bind` raises `PermissionError: [Errno 1] Operation not permitted`, and the
installed Google Chrome headless executable exits with status 134 even for
`about:blank`. The computer-use browser provider also reports Chrome unavailable.
No PNGs are claimed as verified.

On a host that permits launching Chrome, from the repository root run:

```sh
uv run python -m app.capture
```

This writes `yard-small.png`, `bay-small.png`, `mid-playback-small.png` and four
`checker-{stability,strength,dg,violations}-small.png` images at 1920×1080, using
the v2 small instance and the real renderer. The capture
harness embeds the backend JSON response in a temporary offline page, so it
doesn't require a listening socket. It adds no dependencies and makes no network
requests. Inspect the resulting images before using them in the demo video.

The capture CLI also accepts `--instance`, `--plan`, `--run-folder` and `--entry`
with the same artifact selection as the main server. Pass `--compare-plan` or
`--compare-run-folder`/`--compare-entry` to also capture `comparison-small.png`.
For example:

```sh
uv run python -m app.capture --instance /path/to/instance.json --plan /path/to/solver.json --compare-plan /path/to/baseline.json
```

The offline page embeds only selected checked replays, a small catalogue and
the actual server comparison; it cannot start live solves. The checker panes have
been checked against real server data and renderer smoke tests; screenshots of
v2/v3 have not been verified in this sandbox.

"""Send one planner action to a running Functional app through its API, with the same body the planner's button sends, and
keep the server's answer for the recording (static-play/extra-events.json in the call's git-ignored folder).

    uv run python experiments/mvp/ui/static_play/planner_action.py --base http://127.0.0.1:PORT --call <id> \\
        --when needs_answers --post papers --body '{"use_generated_load_list": true}'

Used when the recorded walk waits on a choice only the planner can make (here: Muse Spark's intake asked questions that no
cargo answer can close, and the planner set its reading aside for the generated load list). The page that is recording sees
the change on its next poll and walks on; build.py emit merges this answer into the recording as that action, and the copy's
button sends the identical body.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("static_play_build", HERE / "build.py")
build = importlib.util.module_from_spec(_spec)
sys.modules["static_play_build"] = build
_spec.loader.exec_module(build)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--call", required=True)
    ap.add_argument("--when", help="wait until the call view's state is this")
    ap.add_argument("--post", required=True, help="the call route: papers, answers, ...")
    ap.add_argument("--body", required=True)
    args = ap.parse_args()
    if not build.CALL_ID.match(args.call):
        raise SystemExit("not a call id")
    api = build.Api(args.base)
    view = f"{build.API}calls/{args.call}"
    while args.when:
        state = api.json(view).get("state")
        if state == args.when:
            break
        print(f"state {state}; waiting for {args.when}", flush=True)
        time.sleep(5)
    time.sleep(3)  # as a planner would: read the questions, then choose
    path = f"{view}/{args.post}"
    body = json.loads(args.body)
    t_req = time.time()
    status, ctype, data = api.call("POST", path, body)
    t_resp = time.time()
    event = {"t_req": round(t_req * 1000, 1), "t_resp": round(t_resp * 1000, 1), "method": "POST", "url": path,
             "key": build.key_of("POST", path, json.dumps(body)), "body": build.canon_body(json.dumps(body)),
             "status": status, "ctype": ctype, "text": data.decode("utf-8", "replace"), "via": "api"}
    out = build.CALLS / args.call / "static-play/extra-events.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    events = json.loads(out.read_text()) if out.is_file() else []
    out.write_text(json.dumps(events + [event], ensure_ascii=False))
    print(f"POST {args.post}: {status}; kept in {out.relative_to(build.ROOT)}", flush=True)


if __name__ == "__main__":
    main()

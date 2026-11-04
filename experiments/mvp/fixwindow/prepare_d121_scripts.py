"""Extract and pin the approved runbook's three Python checks without running them."""
import ast
from hashlib import sha256
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent


def main():
    source = HERE / "RUNBOOK.md"
    raw = source.read_bytes()
    bodies = re.findall(r"uv run --offline --no-sync python - [^\n]* <<'PY'\n(.*?)\nPY", raw.decode(), re.S)
    wanted = {"manual_checks.py": "raise SystemExit(pytest.main([",
              "design_gm.py": "prior = {'feeder_500'",
              "securing_replay.py": "from llm.securing import securing_decoder_factory"}
    out = HERE / "d121-20261030/scripts"
    out.mkdir(exist_ok=False)
    rows = []
    for name, marker in wanted.items():
        matched = [body + "\n" for body in bodies if marker in body]
        if len(matched) != 1:
            raise ValueError(f"approved runbook snippet changed: {name}, {len(matched)} matches")
        payload = matched[0].encode()
        ast.parse(payload, filename=name)
        (out / name).write_bytes(payload)
        rows.append({"script": name, "sha256": sha256(payload).hexdigest(), "bytes": len(payload)})
    receipt = {"runbook_sha256": sha256(raw).hexdigest(), "scripts": rows, "executed": False}
    (out / "PREPARED.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()

"""One paid ASR call per invocation, preserving auth-free evidence; no retries.

UV_CACHE_DIR=/private/tmp/meta-tools-uv-cache uv run --no-sync python \
  experiments/mvp/meta-tools/voice/probe_voice.py english
Run english, then thai. Existing result files refuse repeat calls.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import time
import wave

import requests

ROOT = Path(__file__).resolve().parent
ENDPOINT = "https://api.meta.ai/v1/asr/transcribe"
MODEL = "muse-voice-transcribe-1.0"
RATE_USD_PER_SECOND = 0.18 / 3600
SCRIPTS = {
    "english": "Keep the heavy containers below deck.",
    "thai": "เก็บตู้หนักไว้ใต้ดาดฟ้า",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("language", choices=SCRIPTS)
    args = parser.parse_args()
    name = args.language
    output = ROOT / f"{name}.result.json"
    if output.exists():
        raise SystemExit("Existing result: refusing an accidental paid retry")
    if name == "thai" and not (ROOT / "english.result.json").exists():
        raise SystemExit("English must be attempted first")
    wav_path = ROOT / f"{name}.wav"
    with wave.open(str(wav_path), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() in (16000, 24000)
        seconds = audio.getnframes() / audio.getframerate()
        assert 0 < seconds <= 15, "Audio must be nonempty and at most 15 seconds"
    # Every attempted call reserves a full extra second beyond measured duration.
    ceiling = math.ceil(seconds + 1) * RATE_USD_PER_SECOND
    prior = sum(json.loads(p.read_text())["reserved_ceiling_usd"] for p in ROOT.glob("*.result.json"))
    assert prior + ceiling <= 0.50, "Voice allocation exceeded"
    payload = {"model": MODEL, "audioEncoding": "WAV"}
    # Default PUSH_TO_TALK; omit language bias for the smallest access probe.
    manifest = {
        "endpoint": ENDPOINT,
        "multipart_parts": {
            "request": {"content_type": "application/json", "json": payload},
            "audio": {"filename": wav_path.name, "content_type": "audio/wav"},
        },
        "script": SCRIPTS[name],
        "language": name,
        "audio_sha256": hashlib.sha256(wav_path.read_bytes()).hexdigest(),
        "audio_seconds": seconds,
        "reserved_ceiling_usd": ceiling,
        "price_source": "https://dev.meta.ai/docs/pricing-rate-limits",
        "rate_usd_per_audio_hour": 0.18,
        "timeout_seconds": {"connect": 15, "read": 60},
        "automatic_retries": 0,
    }
    (ROOT / f"{name}.request.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    # Credentials enter only this process environment; never argv or evidence.
    for raw in Path("/Users/anda/Projects/.env").read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, value = line.split("=", 1)
        key = key.strip()
        if key in ("MODEL_API_KEY", "META_API_KEY", "SPARK_API_KEY"):
            parts = shlex.split(value, comments=True)
            if parts:
                os.environ[key] = parts[0]
    key = next((os.environ[k] for k in ("MODEL_API_KEY", "META_API_KEY", "SPARK_API_KEY") if os.environ.get(k)), None)
    if not key:
        raise SystemExit("Expected API key environment variable is unavailable")
    result = dict(manifest)
    result["started_utc"] = datetime.now(timezone.utc).isoformat()
    result["state"] = "pending"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    start = time.perf_counter()
    try:
        with wav_path.open("rb") as audio:
            response = requests.post(
                ENDPOINT,
                headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
                files={
                    "request": (None, json.dumps(payload), "application/json"),
                    "audio": (wav_path.name, audio, "audio/wav"),
                },
                timeout=(15, 60),
            )
        body = response.text.replace(key, "[REDACTED]")
        (ROOT / f"{name}.response.txt").write_text(body)
        result.update(http_status=response.status_code, state="completed", response_headers={
            k: v.replace(key, "[REDACTED]") for k, v in response.headers.items()
            if k.lower() in ("content-type", "date", "x-request-id", "request-id")
        })
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = None
        result["response"] = data if data is not None else body
        if response.ok and isinstance(data, dict) and "audioDurationMs" in data:
            billable_seconds = math.floor(data["audioDurationMs"] / 1000)
            result.update(
                billable_whole_seconds=billable_seconds,
                cost_usd_rate_derived=billable_seconds * RATE_USD_PER_SECOND,
                cost_basis="Provider audioDurationMs and published floor-seconds $0.18/hour; invoice not queried",
                transcript=data.get("transcript"),
            )
        else:
            result.update(cost_usd_rate_derived=None, cost_basis="No successful duration evidence; retain reservation as unknown hold")
    except requests.RequestException as exc:
        result.update(state="transport_error", error=str(exc).replace(key, "[REDACTED]"), cost_usd_rate_derived=None,
                      cost_basis="Unknown outcome; retain reservation")
    result["wall_seconds"] = time.perf_counter() - start
    result["finished_utc"] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

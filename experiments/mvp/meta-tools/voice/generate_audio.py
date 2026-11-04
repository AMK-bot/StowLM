"""Generate non-impersonating synthetic scripts; never accesses Meta credentials.

UV_CACHE_DIR=/private/tmp/meta-tools-uv-cache uv pip install \
  --target /private/tmp/meta-tools-voice-deps miniaudio==1.71
PYTHONPATH=/private/tmp/meta-tools-voice-deps UV_CACHE_DIR=/private/tmp/meta-tools-uv-cache \
  uv run --no-sync python experiments/mvp/meta-tools/voice/generate_audio.py

Uses Google's free public translation TTS endpoint. Native macOS say returned
zero-frame files in this session; afconvert then failed with 'fmt?'.
"""
from pathlib import Path
import json
import wave

import miniaudio
import requests

ROOT = Path(__file__).resolve().parent
CLIPS = [
    ("english", "en", "Keep the heavy containers below deck."),
    ("thai", "th", "เก็บตู้หนักไว้ใต้ดาดฟ้า"),
]
for name, language, script in CLIPS:
    response = requests.get(
        "https://translate.google.com/translate_tts",
        params={"ie": "UTF-8", "client": "tw-ob", "q": script, "tl": language},
        timeout=20,
    )
    response.raise_for_status()
    assert response.headers.get("Content-Type", "").startswith("audio/")
    mp3 = ROOT / f"{name}.mp3"
    mp3.write_bytes(response.content)
    decoded = miniaudio.decode_file(str(mp3), output_format=miniaudio.SampleFormat.SIGNED16,
                                    nchannels=1, sample_rate=24000)
    assert len(decoded.samples) and max(decoded.samples) > 0
    with wave.open(str(ROOT / f"{name}.wav"), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(decoded.samples.tobytes())
    print(json.dumps({"clip": name, "script": script, "duration_seconds": len(decoded.samples)/24000,
                      "tts_provider": "Google Translate public TTS", "miniaudio_version": miniaudio.__version__},
                     ensure_ascii=False))

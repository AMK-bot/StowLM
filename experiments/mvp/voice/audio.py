"""Single-worker stock synthetic TTS and reproducible simulated radio audio.

No credentials are accessed or forwarded to the public TTS provider. Radio is
a first-order 300 Hz high-pass followed by 3400 Hz low-pass, plus seeded white
noise at 12 dB whole-clip RMS SNR. It is a simulation, not recorded radio.
"""
from __future__ import annotations

from array import array
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import wave

RATE = 24000


def wav_info(path):
    path = Path(path)
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, RATE):
            raise ValueError("Expected mono PCM16 24000 Hz WAV")
        data = array("h", wav.readframes(wav.getnframes()))
        if sys.byteorder != "little":
            data.byteswap()
        if not data or not any(data):
            raise ValueError("Audio has no nonzero samples")
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "duration_s": len(data) / RATE, "frames": len(data),
            "channels": 1, "sample_rate": RATE, "sample_width": 2}


def write_wav(path, samples):
    data = array("h", (max(-32768, min(32767, round(s))) for s in samples))
    if sys.byteorder != "little":
        data.byteswap()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(data.tobytes())
    return wav_info(path)


def decode_mp3(source, destination):
    executable = os.environ.get("FFMPEG_BINARY") or shutil.which("ffmpeg")
    if executable:
        subprocess.run([executable, "-nostdin", "-hide_banner", "-loglevel", "error",
                        "-threads", "1", "-filter_threads", "1", "-i", str(source),
                        "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le",
                        "-threads", "1", "-y", str(destination)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        decoder = "ffmpeg threads=1 filter_threads=1"
    else:
        try:
            miniaudio = importlib.import_module("miniaudio")
        except ImportError:
            # The access probe left this pinned decoder locally; no installation.
            local = Path("/private/tmp/meta-tools-voice-deps")
            if not local.is_dir():
                raise RuntimeError("Install ffmpeg or provide existing miniaudio decoder") from None
            sys.path.insert(0, str(local))
            miniaudio = importlib.import_module("miniaudio")
        decoded = miniaudio.decode_file(str(source), output_format=miniaudio.SampleFormat.SIGNED16,
                                        nchannels=1, sample_rate=RATE)
        write_wav(destination, decoded.samples)
        decoder = f"miniaudio {miniaudio.__version__} synchronous decode"
    return {**wav_info(destination), "decoder": decoder}


def synthesize(sentence, language, destination):
    """Generate one invented sentence with a stock voice, without impersonation."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    provenance = destination.with_suffix(".audio.json")
    identity = {"script": sentence, "language": language,
                "tts_provider": "Google Translate public TTS stock synthetic voice",
                "real_person_recording": False, "person_imitation": False}
    if provenance.exists():
        prior = json.loads(provenance.read_text())
        if any(prior.get(key) != value for key, value in identity.items()):
            raise ValueError("Existing synthetic clip has different provenance")
        if wav_info(destination)["sha256"] != prior["sha256"]:
            raise ValueError("Synthetic WAV changed after preparation")
        return prior
    if len(sentence) > 200:
        raise ValueError("TTS script exceeds public endpoint length limit")
    url = "https://translate.google.com/translate_tts?" + urlencode(
        {"ie": "UTF-8", "client": "tw-ob", "q": sentence, "tl": language})
    # Fresh urllib request: never shares the Meta client or authorization headers.
    with urlopen(Request(url, headers={"User-Agent": "StowLM synthetic voice measurement"}), timeout=30) as response:
        if not response.headers.get("Content-Type", "").startswith("audio/"):
            raise ValueError("TTS response is not audio")
        mp3 = destination.with_suffix(".mp3")
        mp3.write_bytes(response.read())
    result = {**identity, **decode_mp3(mp3, destination),
              "mp3_sha256": hashlib.sha256(mp3.read_bytes()).hexdigest()}
    provenance.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def radio(source, destination, *, seed, snr_db=12.0):
    """Bandlimit speech, add deterministic white noise, preserve duration."""
    wav_info(source)
    with wave.open(str(source), "rb") as wav:
        samples = array("h", wav.readframes(wav.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    hp = 1 / (1 + 2 * math.pi * 300 / RATE)
    lp = (2 * math.pi * 3400 / RATE) / (1 + 2 * math.pi * 3400 / RATE)
    previous_x = previous_hp = previous_lp = 0.0
    signal = []
    for value in samples:
        current_hp = hp * (previous_hp + value - previous_x)
        current_lp = previous_lp + lp * (current_hp - previous_lp)
        signal.append(current_lp)
        previous_x, previous_hp, previous_lp = value, current_hp, current_lp
    rng = random.Random(seed)
    noise = [rng.uniform(-1, 1) for _ in signal]
    energy = sum(s * s for s in signal)
    noise_energy = sum(n * n for n in noise)
    if energy == 0:
        raise ValueError("Bandlimited signal is silent")
    scale = math.sqrt(energy / noise_energy / (10 ** (snr_db / 10)))
    mixed = [s + scale * n for s, n in zip(signal, noise)]
    peak_scale = min(1.0, 30000 / max(abs(s) for s in mixed))
    result = write_wav(destination, (s * peak_scale for s in mixed))
    return {**result, "seed": seed, "snr_db": snr_db,
            "measured_pre_quantization_snr_db": 10 * math.log10(energy / (noise_energy * scale * scale)),
            "bandpass_hz": [300, 3400], "filter": "first-order high-pass then low-pass",
            "noise": "seeded uniform white; whole-clip RMS SNR after speech bandpass",
            "global_peak_scale": peak_scale,
            "source_sha256": wav_info(source)["sha256"]}

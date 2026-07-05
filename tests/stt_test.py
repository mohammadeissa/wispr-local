#!/usr/bin/env python3
"""End-to-end STT check without a microphone: synthesize speech with macOS
`say`, convert with ffmpeg, transcribe with mlx-whisper.

First run downloads the Whisper model (~1.5 GB for large-v3-turbo).
Run: python3 tests/stt_test.py
"""

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

import config  # noqa: E402
import stt  # noqa: E402

PHRASE = "testing one two three apple banana orange"

tmp = tempfile.mkdtemp(prefix="wispr_stt_")
aiff = os.path.join(tmp, "t.aiff")
wav = os.path.join(tmp, "t.wav")

subprocess.run(["say", "-o", aiff, PHRASE], check=True)
subprocess.run(
    ["ffmpeg", "-y", "-loglevel", "error", "-i", aiff, "-ar", str(config.SAMPLE_RATE), "-ac", "1", wav],
    check=True,
)

audio, rate = sf.read(wav, dtype="float32")
assert rate == config.SAMPLE_RATE, f"unexpected sample rate {rate}"
print(f"synthesized clip: {len(audio)/rate:.2f}s")

transcriber = stt.Transcriber()

t0 = time.time()
text = transcriber.transcribe(audio)
first = time.time() - t0
print(f"first transcribe (incl. model load): {first:.1f}s -> {text!r}")

t0 = time.time()
text2 = transcriber.transcribe(audio)
warm = time.time() - t0
print(f"warm transcribe: {warm:.2f}s -> {text2!r}")

hits = [w for w in ("testing", "apple", "banana", "orange") if w in text2.lower()]
print(f"keyword hits: {hits}")
assert len(hits) >= 2, f"transcription too far off: {text2!r}"
assert isinstance(text2, str) and text2.strip(), "empty transcription"
print("STT OK")

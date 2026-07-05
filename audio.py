"""Microphone capture for Wispr Local (sounddevice / PortAudio)."""

import logging
import threading

import numpy as np
import sounddevice as sd

import config


class AudioRecorder:
    """Push-to-talk recorder. start() opens a 16 kHz mono float32 input stream;
    stop() returns the whole clip as one np.float32 array.

    `level` holds the RMS of the latest block for the overlay meter (a single
    float written by the PortAudio thread, read by the UI timer)."""

    def __init__(self):
        self._stream = None
        self._frames = []
        self._count = 0
        self._lock = threading.Lock()
        self._max_frames = int(config.CONFIG["max_record_seconds"] * config.SAMPLE_RATE)
        self.level = 0.0

    def _callback(self, indata, frames, time_info, status):
        if status:
            logging.warning("audio stream status: %s", status)
        block = indata[:, 0].copy()
        self.level = float(np.sqrt(np.mean(block ** 2)))
        with self._lock:
            if self._count < self._max_frames:
                self._frames.append(block)
                self._count += len(block)
            elif self._count == self._max_frames:
                logging.warning("max_record_seconds reached — dropping further audio")
                self._count += 1  # log once

    def start(self) -> None:
        self._close_stream()
        with self._lock:
            self._frames = []
            self._count = 0
        self.level = 0.0
        self._stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        self._close_stream()
        with self._lock:
            frames, self._frames, self._count = self._frames, [], 0
        self.level = 0.0
        if not frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(frames)

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logging.exception("error closing audio stream")
            self._stream = None

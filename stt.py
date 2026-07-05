"""Speech-to-text via mlx-whisper (Metal-accelerated on Apple Silicon)."""

import logging
import os
import tempfile

import numpy as np

import config


def _prefer_offline() -> None:
    """If the configured Whisper model is already cached, skip the Hugging Face
    version-check ping so the app runs with zero network access."""
    repo = config.CONFIG["stt_model"].replace("/", "--")
    snapshots = os.path.expanduser(f"~/.cache/huggingface/hub/models--{repo}/snapshots")
    try:
        if os.path.isdir(snapshots) and os.listdir(snapshots):
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
    except OSError:
        logging.exception("offline-cache check failed")


_prefer_offline()


class Transcriber:
    """Wraps mlx_whisper.transcribe. The module (and its model cache) stays
    loaded for the process lifetime — the biggest latency lever for short clips.
    First call downloads the model from Hugging Face (~1.5 GB for large-v3-turbo)."""

    def transcribe(self, audio: np.ndarray, initial_prompt: str | None = None) -> str:
        import mlx_whisper  # heavy import — keep lazy so app startup stays fast

        kwargs = {
            "path_or_hf_repo": config.CONFIG["stt_model"],
            "verbose": None,
        }
        if config.CONFIG["stt_language"]:
            kwargs["language"] = config.CONFIG["stt_language"]
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt

        try:
            result = mlx_whisper.transcribe(audio, **kwargs)
        except Exception:
            # Some mlx-whisper versions are picky about ndarray input — retry via WAV.
            logging.exception("ndarray transcribe failed; retrying via temp wav")
            result = mlx_whisper.transcribe(self._to_wav(audio), **kwargs)
        return (result.get("text") or "").strip()

    @staticmethod
    def _to_wav(audio: np.ndarray) -> str:
        import soundfile as sf

        path = os.path.join(tempfile.gettempdir(), "wispr_local_clip.wav")
        sf.write(path, audio, config.SAMPLE_RATE)
        return path

    def warmup(self) -> None:
        """Force model download/compile at startup so the first real dictation is fast."""
        self.transcribe(np.zeros(int(0.5 * config.SAMPLE_RATE), dtype=np.float32))

#!/usr/bin/env python3
"""Environment / permission diagnostics for Wispr Local. Run: python3 diagnose.py"""

import os
import shutil
import sys


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    print(f"Wispr Local diagnostics (python {sys.version.split()[0]})")
    failures = 0

    # Imports
    print("\nImports:")
    for mod in ("numpy", "sounddevice", "mlx_whisper", "ollama", "rumps",
                "AppKit", "Quartz", "ApplicationServices", "soundfile"):
        try:
            __import__(mod)
            check(mod, True)
        except Exception as e:
            failures += not check(mod, False, str(e)[:100])

    import config  # after imports so a broken env fails loudly above

    # Accessibility (no prompt)
    print("\nPermissions:")
    try:
        import permissions
        trusted = permissions.accessibility_trusted()
        if not check("Accessibility (hotkeys + paste)", trusted,
                     "" if trusted else "grant your terminal in System Settings → Privacy & Security"):
            failures += 1
    except Exception as e:
        failures += not check("Accessibility", False, str(e)[:100])

    # Microphone device present (permission itself prompts on first record)
    try:
        import sounddevice as sd
        device = sd.query_devices(kind="input")
        check("Default input device", True, device["name"])
    except Exception as e:
        failures += not check("Default input device", False, str(e)[:100])

    # Ollama server + model
    print("\nOllama:")
    try:
        import ollama
        client = ollama.Client(timeout=5)
        models = [m.model for m in client.list().models]
        check("Server reachable", True, f"{len(models)} models")
        want = config.CONFIG["llm_model"]
        present = any(m == want or m.startswith(want) for m in models)
        if not check(f"Model {want} present", present,
                     "" if present else f"run: ollama pull {want}"):
            failures += 1
    except Exception as e:
        failures += not check("Server reachable", False,
                              f"{str(e)[:80]} — start the Ollama app")

    # STT model cache
    print("\nSpeech-to-text:")
    repo = config.CONFIG["stt_model"].replace("/", "--")
    cache = os.path.expanduser(f"~/.cache/huggingface/hub/models--{repo}")
    check("Whisper model cached", os.path.isdir(cache),
          cache if os.path.isdir(cache) else "downloads (~1.5 GB) on first run")
    check("ffmpeg available", shutil.which("ffmpeg") is not None)

    print(f"\n{'All good.' if failures == 0 else f'{failures} issue(s) found.'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

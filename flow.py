#!/usr/bin/env python3
"""Wispr Local — a fully on-device Wispr Flow clone.

Hold Right Option (⌥), speak, release: your words are transcribed by
mlx-whisper, cleaned by a local LLM (Ollama), and pasted at the cursor of
whatever app is focused. Tap Right Control (⌃) for hands-free toggle mode.

Pipeline per dictation (worker thread):
  mic clip -> mlx_whisper.transcribe -> ollama cleanup (+ anti-hijack guard)
           -> voice commands -> dictionary -> clipboard paste (+ optional Enter)

Threading: hotkey events arrive on the main thread (NSEvent monitors); the
pipeline runs on a worker thread; UI mutations always hop back to the main
thread via AppHelper.callAfter.
"""

import logging
import subprocess
import threading
import time

import numpy as np
import rumps
from PyObjCTools import AppHelper

import audio
import cleanup
import config
import inject
import permissions
import stt
from hotkey import HotkeyMonitor
from overlay import OverlayWindow

LOADING, IDLE, RECORDING, PROCESSING = "loading", "idle", "recording", "processing"

TITLE_LOADING = "⏳"
TITLE_IDLE = "🎙️"
TITLE_RECORDING = "🔴"
TITLE_PROCESSING = "✨"


class WisprApp(rumps.App):
    def __init__(self):
        super().__init__("Wispr Local", title=TITLE_LOADING, quit_button="Quit Wispr Local")
        config.setup_logging()
        logging.info("Wispr Local starting")

        self.state = LOADING
        self._state_lock = threading.Lock()
        self._session_source = None  # "ptt" | "toggle"
        self._record_started = 0.0   # monotonic clock; drives the max-length watchdog
        self.cleanup_online = True
        self.last_cleaned = ""

        self.recorder = audio.AudioRecorder()
        self.transcriber = stt.Transcriber()
        self.overlay = None  # built post-launch on the main thread
        self.hotkeys = HotkeyMonitor(self._on_ptt_down, self._on_ptt_up, self._on_toggle)

        # Menu
        self.status_item = rumps.MenuItem("Status: loading models…")
        self.handsfree_item = rumps.MenuItem("", callback=self._flip_handsfree)
        self._retitle_handsfree()
        self.model_item = rumps.MenuItem(f"Cleanup model: {config.CONFIG['llm_model']}")
        self.stt_item = rumps.MenuItem(f"STT model: {config.CONFIG['stt_model'].split('/')[-1]}")
        self.dict_item = rumps.MenuItem("Edit dictionary…", callback=self._edit_dictionary)
        self.copy_item = rumps.MenuItem("Copy last transcript", callback=self._copy_last)
        self.menu = [
            self.status_item,
            None,
            self.handsfree_item,
            self.model_item,
            self.stt_item,
            None,
            self.dict_item,
            self.copy_item,
            None,
        ]

        self.level_timer = rumps.Timer(self._tick_level, 0.05)
        self.boot_timer = rumps.Timer(self._post_launch, 0.3)
        self.boot_timer.start()

    # --- startup -------------------------------------------------------------

    def _post_launch(self, timer):
        """One-shot setup once the NSApp run loop is live (main thread)."""
        timer.stop()
        trusted = permissions.ensure_accessibility(prompt=True)
        if not trusted:
            rumps.alert(
                title="Wispr Local needs Accessibility",
                message=(
                    "Enable your terminal app under System Settings → Privacy & "
                    "Security → Accessibility, then quit and relaunch Wispr Local.\n\n"
                    "Without it the hotkeys and text insertion cannot work."
                ),
            )
        if config.CONFIG["overlay_enabled"]:
            self.overlay = OverlayWindow.create()
        self.hotkeys.start()
        threading.Thread(target=self._warmup, daemon=True, name="warmup").start()

    def _warmup(self):
        try:
            self.transcriber.warmup()
            logging.info("STT warm")
        except Exception:
            logging.exception("STT warmup failed — first dictation will retry")
        try:
            cleanup.warmup()
            self.cleanup_online = True
            logging.info("LLM warm")
        except Exception:
            self.cleanup_online = False
            logging.exception("Ollama warmup failed — raw-transcript mode until it's back")
        AppHelper.callAfter(self._set_ready)

    def _set_ready(self):
        with self._state_lock:
            if self.state == LOADING:
                self.state = IDLE
        self.title = TITLE_IDLE
        note = "" if self.cleanup_online else " (cleanup offline — raw mode)"
        self.status_item.title = f"Status: ready — hold Right ⌥, or double-tap for hands-free{note}"
        if not self.cleanup_online:
            self._notify("Wispr Local", "Ollama unreachable — start the Ollama app for cleanup")

    # --- hotkey callbacks (main thread) ---------------------------------------

    def _on_ptt_down(self) -> bool:
        """Returns True only if a recording actually started (so the hotkey
        layer never marks PTT active when start was a no-op)."""
        if not config.CONFIG["ptt_enabled"]:
            return False
        return self._start_recording("ptt")

    def _on_ptt_up(self):
        if self._session_source == "ptt":
            self._stop_and_process()

    def _on_toggle(self):
        if not config.CONFIG["toggle_enabled"]:
            return
        if self.state == RECORDING:
            self._stop_and_process()
        else:
            self._start_recording("toggle")

    # --- state machine ----------------------------------------------------------

    def _start_recording(self, source: str) -> bool:
        with self._state_lock:
            if self.state != IDLE:
                return False
            self.state = RECORDING
            self._session_source = source
        try:
            self.recorder.start()
        except Exception:
            logging.exception("could not open microphone")
            self._notify("Wispr Local", "Microphone unavailable — check Privacy settings")
            with self._state_lock:
                self.state = IDLE
                self._session_source = None
            return False
        self._record_started = time.monotonic()
        inject.play_start()
        self.title = TITLE_RECORDING
        if self.overlay:
            self.overlay.show_listening()
        self.level_timer.start()
        return True

    def _stop_and_process(self):
        with self._state_lock:
            if self.state != RECORDING:
                return
            self.state = PROCESSING
            self._session_source = None
        self.level_timer.stop()
        clip = self.recorder.stop()
        inject.play_stop()
        self.title = TITLE_PROCESSING
        if self.overlay:
            self.overlay.show_processing()
        threading.Thread(
            target=self._pipeline, args=(clip,), daemon=True, name="pipeline"
        ).start()

    # --- pipeline (worker thread) --------------------------------------------------

    def _pipeline(self, clip: np.ndarray):
        try:
            duration = len(clip) / config.SAMPLE_RATE
            rms = float(np.sqrt(np.mean(clip**2))) if len(clip) else 0.0
            logging.info("clip: %.2fs rms=%.4f", duration, rms)
            if duration < config.CONFIG["min_duration_seconds"] or rms < config.CONFIG["min_rms"]:
                self._notify("Wispr Local", "No speech detected")
                return

            rules = config.load_dictionary()
            raw = self.transcriber.transcribe(
                clip, initial_prompt=config.dictionary_prompt(rules)
            )
            logging.info("raw transcript: %r", raw)
            if not raw:
                self._notify("Wispr Local", "No speech detected")
                return

            text = cleanup.cleanup(raw)
            text, do_enter = cleanup.apply_voice_commands(text)
            text = cleanup.apply_dictionary(text, rules)
            if not text.strip():
                return
            logging.info("cleaned text: %r", text)

            inject.paste_text(text)
            if do_enter:
                inject.press_enter()

            self.last_cleaned = text
            config.save_last_transcript(raw, text)
        except Exception:
            logging.exception("pipeline failed")
            self._notify("Wispr Local", "Dictation failed — see ~/.wispr-local/wispr.log")
        finally:
            AppHelper.callAfter(self._reset_ui)

    def _reset_ui(self):
        with self._state_lock:
            self.state = IDLE
        self.title = TITLE_IDLE
        if self.overlay:
            self.overlay.hide()

    # --- UI helpers ------------------------------------------------------------------

    def _tick_level(self, timer):
        if self.state != RECORDING:
            return
        # Watchdog: a stuck session (missed key-up, etc.) can never listen forever.
        if time.monotonic() - self._record_started > config.CONFIG["max_record_seconds"]:
            logging.warning("max record time reached — auto-stopping")
            self._stop_and_process()
            return
        if self.overlay:
            self.overlay.push_level(self.recorder.level)

    def _retitle_handsfree(self):
        on = config.CONFIG["toggle_enabled"]
        self.handsfree_item.title = f"Hands-free toggle (Right ⌃): {'ON' if on else 'OFF'}"

    def _flip_handsfree(self, _):
        config.CONFIG["toggle_enabled"] = not config.CONFIG["toggle_enabled"]
        self._retitle_handsfree()

    def _edit_dictionary(self, _):
        config.load_dictionary()  # ensures the file exists
        subprocess.run(["open", config.DICTIONARY_PATH], check=False)

    def _copy_last(self, _):
        if self.last_cleaned:
            inject.set_clipboard(self.last_cleaned)
            self._notify("Wispr Local", "Last transcript copied")
        else:
            self._notify("Wispr Local", "Nothing dictated yet")

    def _notify(self, title: str, message: str):
        try:
            rumps.notification(title, "", message)
        except Exception:
            # Notifications can fail outside a real .app bundle — log instead.
            logging.info("NOTIFY: %s — %s", title, message)


if __name__ == "__main__":
    WisprApp().run()

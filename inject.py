"""Text injection and audio cues for Wispr Local.

Primary injection path (same as Wispr Flow): save clipboard -> set text ->
synthetic Cmd+V via CGEvent -> restore clipboard after a short debounce
(restoring too soon races apps that read the pasteboard asynchronously).
Requires the Accessibility permission for the launching app.
"""

import logging
import time

import Quartz
from AppKit import NSPasteboard, NSPasteboardTypeString, NSSound

import config

_sound_refs = []  # keep references so sounds aren't GC'd mid-playback


def _play(name: str) -> None:
    if not config.CONFIG["sounds"]:
        return
    try:
        sound = NSSound.soundNamed_(name)
        if sound is not None:
            _sound_refs.append(sound)
            if len(_sound_refs) > 4:
                _sound_refs.pop(0)
            sound.play()
    except Exception:
        logging.exception("could not play sound %s", name)


def play_start() -> None:
    _play("Tink")


def play_stop() -> None:
    _play("Pop")


def get_clipboard() -> str | None:
    pb = NSPasteboard.generalPasteboard()
    return pb.stringForType_(NSPasteboardTypeString)


def set_clipboard(text: str) -> None:
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)


def _key_tap(keycode: int, flags: int = 0) -> None:
    """Post a synthetic key down+up with exactly the given modifier flags."""
    down = Quartz.CGEventCreateKeyboardEvent(None, keycode, True)
    up = Quartz.CGEventCreateKeyboardEvent(None, keycode, False)
    Quartz.CGEventSetFlags(down, flags)
    Quartz.CGEventSetFlags(up, flags)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    time.sleep(0.01)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def paste_text(text: str) -> None:
    """Insert text at the cursor of the focused app via clipboard + Cmd+V."""
    if not text:
        return
    old = get_clipboard()
    set_clipboard(text)
    time.sleep(0.06)  # let the pasteboard propagate before the keystroke
    _key_tap(config.KEY_V, config.CG_FLAG_COMMAND)
    time.sleep(config.CONFIG["restore_delay"])
    if old is not None:
        try:
            set_clipboard(old)
        except Exception:
            logging.exception("clipboard restore failed")


def press_enter() -> None:
    """Post an Enter keystroke (for the trailing 'press enter' voice command)."""
    time.sleep(0.05)
    _key_tap(config.KEY_RETURN)

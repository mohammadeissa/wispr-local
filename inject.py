"""Text injection and audio cues for Wispr Local.

Primary injection path (same as Wispr Flow): save clipboard -> set text ->
synthetic Cmd+V via CGEvent -> restore clipboard after a short debounce
(restoring too soon races apps that read the pasteboard asynchronously).
Requires the Accessibility permission for the launching app.
"""

import ctypes
import ctypes.util
import logging
import time

import Quartz
from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString, NSSound

import config

_sound_refs = []  # keep references so sounds aren't GC'd mid-playback

# Carbon's IsSecureEventInputEnabled() — true when a password field (or any app
# using EnableSecureEventInput) is focused, which blocks synthetic Cmd+C/Cmd+V.
try:
    _carbon = ctypes.CDLL(ctypes.util.find_library("Carbon"))
    _carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool
except Exception:  # pragma: no cover - non-macOS / missing framework
    _carbon = None


def is_secure_input() -> bool:
    if _carbon is None:
        return False
    try:
        return bool(_carbon.IsSecureEventInputEnabled())
    except Exception:
        return False


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


def save_clipboard() -> list:
    """Snapshot every pasteboard item and all its data types, so the user's
    clipboard (rich text, images, etc.) survives a copy/paste round-trip.
    pbcopy/pbpaste and string-only saves drop non-text formats."""
    pb = NSPasteboard.generalPasteboard()
    saved = []
    for item in (pb.pasteboardItems() or []):
        types = {}
        for t in (item.types() or []):
            data = item.dataForType_(t)
            if data is not None:
                types[t] = data
        if types:
            saved.append(types)
    return saved


def restore_clipboard(saved: list) -> None:
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    if not saved:
        return
    items = []
    for types in saved:
        item = NSPasteboardItem.alloc().init()
        for t, data in types.items():
            item.setData_forType_(data, t)
        items.append(item)
    try:
        pb.writeObjects_(items)
    except Exception:
        logging.exception("clipboard restore failed")


def capture_selection(timeout: float = 0.6) -> str | None:
    """Copy the current selection via synthetic Cmd+C and return it as text.

    Polls NSPasteboard.changeCount (the only way to know the copy landed — macOS
    has no clipboard-change notification) instead of a blind sleep, so it is both
    fast and race-free. Returns None if nothing was copied (no selection, or an
    app that ignores Cmd+C). Caller is responsible for save/restore_clipboard."""
    pb = NSPasteboard.generalPasteboard()
    before = pb.changeCount()
    _key_tap(config.KEY_C, config.CG_FLAG_COMMAND)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.02)
        if pb.changeCount() != before:
            return pb.stringForType_(NSPasteboardTypeString)
    return None


def paste() -> None:
    """Bare synthetic Cmd+V — caller manages clipboard contents around it."""
    _key_tap(config.KEY_V, config.CG_FLAG_COMMAND)


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

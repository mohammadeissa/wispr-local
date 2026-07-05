"""Global hotkey monitoring for Wispr Local — dual backend.

Backend A: NSEvent.addGlobalMonitorForEventsMatchingMask (AppKit). Needs
Accessibility. Zero extra permissions on most setups.

Backend B: listen-only Quartz CGEventTap on the main run loop — the approach
production dictation apps use. macOS lists the host app under Input
Monitoring on first tap creation; some macOS 26 setups deliver events only
via this path.

Both feed the same edge-triggered state machine; duplicate delivery is
harmless because transitions fire only on actual flag changes.

Deliberately NOT pynput: its global listener crashes the whole process on
macOS 26 (Tahoe) — TSM calls from a background thread trip a SIGTRAP. Both
backends here run on the main thread.

Keys watched (flagsChanged events only):
  Right Option (keyCode 61) held  -> push-to-talk down/up
  Right Control (keyCode 62) tap  -> hands-free toggle
"""

import logging
import time

import Quartz
from AppKit import NSEvent, NSEventMaskFlagsChanged, NSEventMaskKeyDown
from PyObjCTools import AppHelper

import config


class HotkeyMonitor:
    def __init__(self, on_ptt_down, on_ptt_up, on_toggle, on_improve=None):
        self.on_ptt_down = on_ptt_down
        self.on_ptt_up = on_ptt_up
        self.on_toggle = on_toggle
        self.on_improve = on_improve
        self._key_global = None
        self._key_local = None
        self._right_option_down = False
        self._right_control_down = False
        # Double-tap / hold disambiguation for Right Option.
        self._dt_enabled = bool(config.CONFIG["double_tap_enabled"])
        self._hold_delay = float(config.CONFIG["hold_delay_seconds"])
        self._dt_window = float(config.CONFIG["double_tap_seconds"])
        self._ro_gen = 0            # invalidates stale pending hold timers
        self._ro_last_tap = 0.0     # monotonic time of previous quick tap
        self._ptt_active = False    # a hold-to-talk session is live
        self._global_monitor = None
        self._local_monitor = None
        self._tap = None
        self._last_source = None  # dedupe: only one backend drives transitions

    # --- registration (main thread) ------------------------------------------

    def start(self) -> None:
        self._start_nsevent()
        self._start_event_tap()
        logging.info(
            "hotkey backends: nsevent=%s event-tap=%s",
            self._global_monitor is not None,
            self._tap is not None,
        )
        if self._tap is None and self._global_monitor is None:
            logging.error(
                "NO hotkey backend active — grant Accessibility and/or Input "
                "Monitoring to the launching terminal, then relaunch."
            )

    def _start_nsevent(self) -> None:
        try:
            mask = NSEventMaskFlagsChanged
            self._global_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask, lambda event: self._from_nsevent(event)
            )

            def _local(event):
                self._from_nsevent(event)
                return event  # local monitors must return the event

            self._local_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                mask, _local
            )

            # Separate keyDown monitors for the Ctrl+Opt+I improve chord. Only
            # NSEvent (Accessibility) — no event tap — so no Input Monitoring
            # dependency and a single delivery source (no double-fire).
            if self.on_improve is not None:
                self._key_global = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                    NSEventMaskKeyDown, lambda e: self._from_keydown(e)
                )

                def _key_local(event):
                    self._from_keydown(event)
                    return event

                self._key_local = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                    NSEventMaskKeyDown, _key_local
                )
        except Exception:
            logging.exception("NSEvent monitor registration failed")

    def _start_event_tap(self) -> None:
        try:
            mask = Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
            self._tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly,
                mask,
                self._from_tap,
                None,
            )
            if self._tap is None:
                logging.warning(
                    "CGEventTap not created — enable the terminal under System "
                    "Settings > Privacy & Security > Input Monitoring, then relaunch."
                )
                return
            source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
            Quartz.CFRunLoopAddSource(
                Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes
            )
            Quartz.CGEventTapEnable(self._tap, True)
        except Exception:
            logging.exception("event tap registration failed")
            self._tap = None

    # --- event entry points ------------------------------------------------------

    def _from_nsevent(self, event) -> None:
        try:
            self._process(event.keyCode(), int(event.modifierFlags()), "nsevent")
        except Exception:
            logging.exception("nsevent handler error")

    def _from_keydown(self, event) -> None:
        """Fire on_improve for Ctrl+Opt+I (Command/Shift must NOT be held, so it
        never collides with browser ⌥⌘I devtools etc.)."""
        try:
            if event.keyCode() != config.KEY_I:
                return
            flags = int(event.modifierFlags())
            mods = flags & (config.MOD_CONTROL | config.MOD_OPTION
                            | config.MOD_COMMAND | config.MOD_SHIFT)
            if mods == (config.MOD_CONTROL | config.MOD_OPTION):
                self.on_improve()
        except Exception:
            logging.exception("keydown handler error")

    def _from_tap(self, proxy, type_, event, refcon):
        try:
            if type_ in (
                Quartz.kCGEventTapDisabledByTimeout,
                Quartz.kCGEventTapDisabledByUserInput,
            ):
                Quartz.CGEventTapEnable(self._tap, True)  # macOS auto-disables slow taps
                return event
            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
            flags = int(Quartz.CGEventGetFlags(event))
            self._process(keycode, flags, "tap")
        except Exception:
            logging.exception("event tap handler error")
        return event

    # --- Right Option gesture: hold = PTT, double-tap = hands-free -------------------

    def _on_ro_down(self) -> None:
        if self._ptt_active:
            # A previous key-up was missed — clear the stale session first so we
            # never wedge in a permanently-"held" state.
            self._ptt_active = False
            self.on_ptt_up()
        if not self._dt_enabled:
            self._ptt_active = bool(self.on_ptt_down())
            return
        # Defer PTT start until the key survives the hold delay. A quick tap
        # (released first) never starts a recording, so double-taps stay clean.
        self._ro_gen += 1
        g = self._ro_gen
        AppHelper.callLater(self._hold_delay, lambda: self._ro_hold_fire(g))

    def _ro_hold_fire(self, g: int) -> None:
        if g == self._ro_gen and self._right_option_down and not self._ptt_active:
            # Only stay "active" if a recording truly started (IDLE -> RECORDING).
            # During a live toggle session this no-ops, so the release below falls
            # through to the double-tap path instead of a dropped stop.
            self._ptt_active = bool(self.on_ptt_down())

    def _on_ro_up(self) -> None:
        if not self._dt_enabled:
            self._ptt_active = False
            self.on_ptt_up()
            return
        self._ro_gen += 1  # invalidate any still-pending hold timer
        if self._ptt_active:
            self._ptt_active = False
            self.on_ptt_up()
            self._ro_last_tap = 0.0
            return
        now = time.monotonic()  # released before hold delay -> it was a tap
        if self._ro_last_tap and (now - self._ro_last_tap) <= self._dt_window:
            self._ro_last_tap = 0.0
            self.on_toggle()  # second quick tap -> hands-free start/stop
        else:
            self._ro_last_tap = now

    # --- shared edge-triggered logic ------------------------------------------------

    def _process(self, keycode: int, flags: int, source: str) -> None:
        logging.info("flagsChanged kc=%s flags=%#x via %s", keycode, flags, source)
        # Both backends may deliver the same physical event; lock transitions to
        # whichever backend spoke first so duplicates are ignored cleanly.
        if self._last_source is None:
            self._last_source = source
            logging.info("hotkey transitions driven by %s backend", source)
        if source != self._last_source:
            return
        if keycode == config.KEY_RIGHT_OPTION:
            down = bool(flags & config.MOD_OPTION)
            if down and not self._right_option_down:
                self._right_option_down = True
                self._on_ro_down()
            elif not down and self._right_option_down:
                self._right_option_down = False
                self._on_ro_up()
        elif keycode == config.KEY_RIGHT_CONTROL:
            down = bool(flags & config.MOD_CONTROL)
            if down and not self._right_control_down:
                self._right_control_down = True
                self.on_toggle()
            elif not down:
                self._right_control_down = False

#!/usr/bin/env python3
"""Live hotkey probe — proves whether THIS terminal receives key events.

Run from the same terminal you launch ./run.sh in:
    /opt/miniconda3/bin/python3 keyprobe.py

Press keys. Each flagsChanged event prints its keyCode. If you see NOTHING
when pressing Right Option, this terminal lacks effective Input Monitoring /
Accessibility (grant it to THIS app, then relaunch). Ctrl-C to quit.

Key codes: Left Option 58 · Right Option 61 · Left Control 59 · Right Control 62
"""
import ctypes
import ctypes.util

import Quartz
from AppKit import NSEvent

iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"))
iokit.IOHIDCheckAccess.restype = ctypes.c_int
iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
from ApplicationServices import AXIsProcessTrusted

im = {0: "GRANTED", 1: "DENIED", 2: "UNKNOWN"}.get(iokit.IOHIDCheckAccess(1))
print(f"Input Monitoring: {im}   Accessibility: {'TRUSTED' if AXIsProcessTrusted() else 'NOT TRUSTED'}")
print("Press keys (Right Option = 61). Ctrl-C to quit.\n")

NAMES = {58: "L-Option", 61: "R-Option", 59: "L-Control", 62: "R-Control",
         55: "Cmd", 56: "Shift", 63: "Fn"}


def show(event):
    kc = event.keyCode()
    print(f"  flagsChanged keyCode={kc} {NAMES.get(kc, '')}  flags={int(event.modifierFlags()):#x}")


NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
    Quartz.NSEventMaskFlagsChanged, show
)

# Event tap path (needs Input Monitoring) — same as the real app.
def tap_cb(proxy, type_, ev, refcon):
    kc = Quartz.CGEventGetIntegerValueField(ev, Quartz.kCGKeyboardEventKeycode)
    print(f"  [tap] keyCode={kc} {NAMES.get(kc, '')}")
    return ev


tap = Quartz.CGEventTapCreate(
    Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
    Quartz.kCGEventTapOptionListenOnly,
    Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged), tap_cb, None,
)
print(f"event tap created: {tap is not None}\n")
if tap is not None:
    src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)

Quartz.CFRunLoopRun()

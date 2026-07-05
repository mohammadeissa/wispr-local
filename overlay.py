"""Floating overlay indicator ("Flow Bar" style) for Wispr Local.

A borderless, non-activating NSPanel pinned bottom-center of the main screen.
It never takes focus (that would redirect the paste target) and ignores mouse
events. Shows a state label plus a live level meter while recording.

All methods must be called on the main thread. Failure to build the overlay is
non-fatal — the menu-bar icon and sounds remain the fallback indicator.
"""

import logging

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSFont,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSTextAlignmentCenter,
    NSTextField,
    NSView,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSMakeRect

PANEL_WIDTH = 250.0
PANEL_HEIGHT = 64.0
BAR_COUNT = 14

# NSWindowCollectionBehavior: canJoinAllSpaces | stationary | fullScreenAuxiliary
_COLLECTION_BEHAVIOR = (1 << 0) | (1 << 4) | (1 << 8)


class _LevelView(NSView):
    """Draws rolling vertical bars from recent mic RMS levels."""

    def initWithFrame_(self, frame):
        self = objc.super(_LevelView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._levels = [0.0] * BAR_COUNT
        return self

    def pushLevel_(self, level):
        self._levels = self._levels[1:] + [min(1.0, float(level) * 8.0)]
        self.setNeedsDisplay_(True)

    def resetLevels(self):
        self._levels = [0.0] * BAR_COUNT
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        bounds = self.bounds()
        width, height = bounds.size.width, bounds.size.height
        slot = width / BAR_COUNT
        bar_width = slot * 0.5
        NSColor.whiteColor().setFill()
        for i, level in enumerate(self._levels):
            bar_height = max(3.0, level * height)
            x = i * slot + (slot - bar_width) / 2.0
            y = (height - bar_height) / 2.0
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, y, bar_width, bar_height), bar_width / 2.0, bar_width / 2.0
            )
            path.fill()


class OverlayWindow:
    """Wrapper owning the panel, label, and level view."""

    def __init__(self, panel, label, level_view):
        self._panel = panel
        self._label = label
        self._level_view = level_view

    @classmethod
    def create(cls):
        """Build the panel; returns None on any failure (overlay is optional)."""
        try:
            style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, PANEL_WIDTH, PANEL_HEIGHT),
                style,
                NSBackingStoreBuffered,
                False,
            )
            panel.setLevel_(NSStatusWindowLevel)
            panel.setOpaque_(False)
            panel.setBackgroundColor_(NSColor.clearColor())
            panel.setIgnoresMouseEvents_(True)
            panel.setHasShadow_(True)
            panel.setCollectionBehavior_(_COLLECTION_BEHAVIOR)

            content = panel.contentView()
            content.setWantsLayer_(True)
            layer = content.layer()
            layer.setCornerRadius_(16.0)
            layer.setBackgroundColor_(
                NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.88).CGColor()
            )

            label = NSTextField.labelWithString_("🎙 Listening…")
            label.setFrame_(NSMakeRect(0, PANEL_HEIGHT - 26, PANEL_WIDTH, 20))
            label.setAlignment_(NSTextAlignmentCenter)
            label.setFont_(NSFont.systemFontOfSize_(13))
            label.setTextColor_(NSColor.whiteColor())
            content.addSubview_(label)

            level_view = _LevelView.alloc().initWithFrame_(
                NSMakeRect(24, 8, PANEL_WIDTH - 48, 24)
            )
            content.addSubview_(level_view)

            screen = NSScreen.mainScreen()
            if screen is not None:
                vf = screen.visibleFrame()
                x = vf.origin.x + (vf.size.width - PANEL_WIDTH) / 2.0
                y = vf.origin.y + 90.0
                panel.setFrameOrigin_((x, y))

            return cls(panel, label, level_view)
        except Exception:
            logging.exception("overlay creation failed — continuing without it")
            return None

    def show_listening(self) -> None:
        self._label.setStringValue_("🎙 Listening…")
        self._level_view.resetLevels()
        self._panel.orderFrontRegardless()

    def show_processing(self) -> None:
        self._label.setStringValue_("✨ Cleaning…")
        self._panel.orderFrontRegardless()

    def push_level(self, level: float) -> None:
        self._level_view.pushLevel_(level)

    def hide(self) -> None:
        self._panel.orderOut_(None)

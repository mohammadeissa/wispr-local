"""macOS TCC permission checks for Wispr Local."""

import logging

try:
    from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions
except ImportError:  # pyobjc not installed yet — diagnose.py reports this
    AXIsProcessTrusted = None
    AXIsProcessTrustedWithOptions = None

try:
    from ApplicationServices import kAXTrustedCheckOptionPrompt
except ImportError:
    kAXTrustedCheckOptionPrompt = "AXTrustedCheckOptionPrompt"


def accessibility_trusted() -> bool:
    """Non-prompting check."""
    if AXIsProcessTrusted is None:
        return False
    try:
        return bool(AXIsProcessTrusted())
    except Exception:
        logging.exception("Accessibility check failed")
        return False


def ensure_accessibility(prompt: bool = True) -> bool:
    """Check Accessibility trust; optionally trigger the system prompt.

    Required for the global hotkey monitor and for posting the Cmd+V / Enter
    keystrokes. The grant applies to the LAUNCHING app (Terminal/iTerm), not
    the Python interpreter. After granting, relaunch Wispr Local.
    """
    if AXIsProcessTrustedWithOptions is None:
        logging.error("pyobjc ApplicationServices missing — cannot check Accessibility")
        return False
    try:
        trusted = bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(prompt)}))
    except Exception:
        logging.exception("Accessibility check failed")
        return False
    if not trusted:
        logging.warning(
            "Accessibility NOT granted. Enable your terminal app under "
            "System Settings > Privacy & Security > Accessibility, then relaunch."
        )
    return trusted


# Microphone note: macOS prompts on the first sounddevice InputStream open,
# attributed to the launching app. If recordings come back all-zero/silent,
# enable the terminal under System Settings > Privacy & Security > Microphone.

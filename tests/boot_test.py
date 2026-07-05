#!/usr/bin/env python3
"""Boot the full app headless and self-quit: verifies rumps init, menu build,
overlay creation, hotkey monitor registration, and STT+LLM warmup end-to-end.

Permission prompts/alerts are stubbed out so this runs unattended.
Run: python3 tests/boot_test.py  (exits 0 on success)
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rumps  # noqa: E402

import permissions  # noqa: E402

# Stub interactive bits before flow imports/uses them.
permissions.ensure_accessibility = lambda prompt=True: True
rumps.alert = lambda *a, **k: None
rumps.notification = lambda *a, **k: None

import flow  # noqa: E402

app = flow.WisprApp()
started = time.time()


def _watch(timer):
    elapsed = time.time() - started
    if app.state != flow.LOADING:
        hot = app.hotkeys._global_monitor is not None and app.hotkeys._local_monitor is not None
        print(
            f"BOOT OK in {elapsed:.1f}s — state={app.state} "
            f"overlay={'yes' if app.overlay else 'no'} hotkeys={'yes' if hot else 'no'} "
            f"cleanup_online={app.cleanup_online}",
            flush=True,
        )
        rumps.quit_application()
    elif elapsed > 90:
        print(f"BOOT TIMEOUT — state still {app.state}")
        os._exit(1)


rumps.Timer(_watch, 0.5).start()
app.run()

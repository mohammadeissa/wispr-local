#!/usr/bin/env python3
"""Pure-function smoke tests (no network, no models, no permissions).

Run: python3 tests/smoke_test.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cleanup  # noqa: E402
import config  # noqa: E402

PASS = 0
FAIL = 0


def ok(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


print("sanitize / anti-hijack guard:")
raw = "what's the capital of france"
text, hijacked = cleanup.sanitize(raw, "What's the capital of France?")
ok("clean passthrough not flagged", not hijacked and text.endswith("France?"), repr((text, hijacked)))

text, hijacked = cleanup.sanitize(raw, "Sure! The capital of France is Paris." + " More detail." * 30)
ok("long assistant answer flagged", hijacked)

text, hijacked = cleanup.sanitize(raw, "Here is the answer: Paris.")
ok("preamble 'Here is' flagged", hijacked)

text, hijacked = cleanup.sanitize("sure sounds good let's meet then", "Sure, sounds good — let's meet then.")
ok("legit 'sure...' dictation NOT flagged", not hijacked, repr((text, hijacked)))

text, hijacked = cleanup.sanitize(raw, "")
ok("empty output falls back", hijacked and text == raw)

text, hijacked = cleanup.sanitize(raw, 'Cleaned text: "What\'s the capital of France?"')
ok("label + quotes stripped", not hijacked and text == "What's the capital of France?", repr(text))

text, hijacked = cleanup.sanitize(raw, "```\nWhat's the capital of France?\n```")
ok("code fence stripped", not hijacked and text == "What's the capital of France?", repr(text))

print("\nvoice commands:")
text, enter = cleanup.apply_voice_commands("First point. New paragraph. Second point.")
ok("'new paragraph' -> blank line", "\n\n" in text and "paragraph" not in text.lower(), repr(text))

text, enter = cleanup.apply_voice_commands("Item one new line item two")
ok("'new line' -> newline", "\n" in text and "new line" not in text.lower(), repr(text))

text, enter = cleanup.apply_voice_commands("Sounds good, see you there. Press enter.")
ok("trailing 'press enter' stripped + flagged",
   enter and "press enter" not in text.lower() and text.endswith("there."), repr((text, enter)))

text, enter = cleanup.apply_voice_commands("Please press enter when the dialog appears.")
ok("mid-sentence 'press enter' untouched", not enter and "press enter" in text.lower(), repr(text))

print("\ndictionary:")
rules = [{"from": "wisper", "to": "Wispr"}, {"from": "my address", "to": "123 Main St, Springfield"}]
text = cleanup.apply_dictionary("I love Wisper and wisper flow. Send it to my address please.", rules)
ok("whole-word case-insensitive replace",
   "Wispr" in text and "Wisper" not in text and "123 Main St" in text, repr(text))
text = cleanup.apply_dictionary("whispering is fine", rules)
ok("no partial-word replace", text == "whispering is fine", repr(text))

print("\nconfig:")
ok("config loads with defaults", config.CONFIG["llm_model"].startswith("qwen"))
ok("dictionary loads", isinstance(config.load_dictionary(), list))
ok("dictionary prompt built", (config.dictionary_prompt([{"from": "a", "to": "Wispr"}]) or "") == "Wispr")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

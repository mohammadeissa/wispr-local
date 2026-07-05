"""LLM transcript cleanup via Ollama, plus voice-command and dictionary passes.

The cleanup model must REFORMAT the dictation, never answer it. Two defenses:
the anti-hijack system prompt (config.CLEANUP_SYSTEM_PROMPT) and the runtime
guard in sanitize() — if the output looks like an assistant response, we fall
back to the raw transcript so the user's words are never replaced by a
hallucinated answer.
"""

import logging
import re

import config

# Phrases an assistant-style (hijacked) response starts with.
_PREAMBLE_PHRASES = (
    "here is", "here's", "here are", "as an ai", "i'd be happy", "i would be happy",
    "i can help", "i cannot", "i can't", "certainly!", "sure!", "sure,",
    "of course!", "okay, here", "sure thing",
)
# Single first words that signal a response — only hijack if the raw transcript
# did NOT also start with the same word (so "sure sounds good..." stays legit).
_PREAMBLE_FIRST_WORDS = {"sure", "certainly", "here", "here's", "okay", "gladly"}

_LABEL_PREFIX_RE = re.compile(r"^(?:cleaned(?:\s+text)?|output|result)\s*[::]\s*", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n(.*?)\n?```\s*$", re.DOTALL)

_client = None


def _get_client():
    global _client
    if _client is None:
        import ollama

        _client = ollama.Client(timeout=config.CONFIG["llm_timeout_seconds"])
    return _client


def _first_word(text: str) -> str:
    m = re.match(r"[^\w']*([\w']+)", text.lower())
    return m.group(1) if m else ""


def sanitize(raw: str, out: str) -> tuple[str, bool]:
    """Normalize LLM output; detect hijack. Returns (text, hijacked)."""
    out = (out or "").strip()

    m = _CODE_FENCE_RE.match(out)
    if m:
        out = m.group(1).strip()
    out = _LABEL_PREFIX_RE.sub("", out).strip()

    # Strip wrapping quotes the model added (only if the raw didn't have them).
    if (
        len(out) > 1
        and out[0] in "\"'“‘"
        and out[-1] in "\"'”’"
        and raw.strip()[:1] not in "\"'“‘"
    ):
        out = out[1:-1].strip()

    if not out:
        return raw, True

    # Length guard: cleanup should never grow the text substantially.
    if len(out) > max(len(raw) * 3, len(raw) + 200):
        return raw, True

    # Preamble guard.
    out_l = out.lower()
    raw_fw, out_fw = _first_word(raw), _first_word(out)
    if any(out_l.startswith(p) for p in _PREAMBLE_PHRASES) and raw_fw != out_fw:
        return raw, True
    if out_fw in _PREAMBLE_FIRST_WORDS and out_fw != raw_fw:
        return raw, True

    return out, False


def cleanup(raw: str) -> str:
    """Raw transcript -> cleaned text. Falls back to raw on any failure."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        r = _get_client().chat(
            model=config.CONFIG["llm_model"],
            messages=[
                {"role": "system", "content": config.CLEANUP_SYSTEM_PROMPT},
                {"role": "user", "content": f"<transcript>{raw}</transcript>"},
            ],
            options={"temperature": config.CONFIG["llm_temperature"], "top_p": 0.9},
            keep_alive=-1,
            stream=False,
        )
        out = r["message"]["content"] or ""
    except Exception:
        logging.exception("ollama cleanup failed — using raw transcript")
        return raw

    text, hijacked = sanitize(raw, out)
    if hijacked:
        logging.warning("cleanup output rejected by guard — using raw transcript. out=%r", out[:200])
        return raw
    return text


def apply_voice_commands(text: str) -> tuple[str, bool]:
    """Convert spoken structure commands to characters.

    'new paragraph' -> blank line, 'new line'/'next line' -> newline.
    A trailing 'press enter' / 'send message' is stripped and reported as
    press_enter=True so the caller can post an Enter keystroke after pasting.
    """
    press_enter = False
    m = re.search(config.ENTER_COMMANDS_RE, text, flags=re.IGNORECASE)
    if m:
        text = text[: m.start()]
        press_enter = True
    text = re.sub(config.NEW_PARAGRAPH_RE, "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(config.NEW_LINE_RE, "\n", text, flags=re.IGNORECASE)
    return text.strip(), press_enter


def apply_dictionary(text: str, rules: list) -> str:
    """Case-insensitive whole-word replacements from the user dictionary."""
    for rule in rules:
        try:
            pattern = r"\b" + re.escape(rule["from"]) + r"\b"
            text = re.sub(pattern, rule["to"], text, flags=re.IGNORECASE)
        except Exception:
            logging.exception("bad dictionary rule: %r", rule)
    return text


def warmup() -> None:
    """Load the model into Ollama's memory (keep_alive=-1) so the first real
    dictation doesn't pay the cold-start."""
    _get_client().chat(
        model=config.CONFIG["llm_model"],
        messages=[{"role": "user", "content": "hi"}],
        options={"num_predict": 1},
        keep_alive=-1,
        stream=False,
    )

"""Human-readable model names for the UI and the web payloads.

A raw model id (``claude-haiku-5-5``, ``opencode/deepseek-v4.1-flash-free``) becomes the name a person reads
(``Haiku 5.5``, ``DeepSeek V4.1 Flash``). Provider prefixes are stripped first; an id that is not in the map comes back
with only the prefix removed. Versions are never invented: ``gpt-6-sol`` is ``GPT-6 Sol``, not ``GPT-6.1 Sol``.
"""

import re

_PREFIXES = ("opencode-go/", "opencode/", "anthropic/", "cursor-")

# Claude lead models arrive as the short form ``sonnet-5.5``; the version is read from the id, never guessed.
_CLAUDE_SHORT = re.compile(r"(sonnet|opus|haiku)-(\d+)\.(\d+)")

_NAMES = {
    "deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
    "deepseek-v4.1-flash-free": "DeepSeek V4.1 Flash",
    "claude-haiku-5-5": "Haiku 5.5",
    "claude-sonnet-5-5": "Sonnet 5.5",
    "claude-opus-5-5": "Opus 5.5",
    "composer-2.5": "Composer 2.5",
    "gpt-6-sol": "GPT-6 Sol",
    "gpt-6.1-sol": "GPT-6.1 Sol",
    "gpt-6-luna": "GPT-6 Luna",
    "flint-40b-free": "Flint 40B",
    "glm-5.3-flash-free": "GLM-5.3 Flash",
    "grok-4.6-medium": "Grok 4.6",
}


def display_model(raw: str) -> str:
    """The readable name for a raw model id; unknown ids keep their text minus any provider prefix."""
    text = raw or ""
    for prefix in _PREFIXES:
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    if text.lower() in _NAMES:
        return _NAMES[text.lower()]
    short = _CLAUDE_SHORT.fullmatch(text.lower())
    if short:
        return f"{short[1].capitalize()} {short[2]}.{short[3]}"
    return text

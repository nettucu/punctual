"""Message catalogs: `locales/<locale>.json`, one flat key per message, shared by the server and the browser (`/i18n.js`).

The instance language is `settings.locale`. English is the source language and the fallback.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from punctual.settings import settings

LOCALES_DIR: Path = Path(__file__).parent / "locales"
LOCALES: tuple[str, ...] = ("en", "ro")


@cache
def catalog(locale: str) -> dict[str, str]:
    """All messages of a locale."""
    return json.loads((LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))


def t(key: str, **params: object) -> str:
    """Message `key` in the instance language, with `{name}` placeholders filled from `params`.

    Raises:
        KeyError: If the key is missing from the English catalog (a programming error).
    """
    text: str = catalog(settings.locale).get(key) or catalog("en")[key]
    return text.format(**params) if params else text


def has(key: str) -> bool:
    """Whether `key` exists in the catalogs (for labels with a raw-code fallback)."""
    return key in catalog("en")

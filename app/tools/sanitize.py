"""Text that comes from an uploaded PDF or from the user is data. Before it enters the model input it is cleaned, capped and quoted."""
from __future__ import annotations

import re
import unicodedata

_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿]")


def clean(text, n: int = 80) -> str:
    """One line, no control characters, no invisible or direction-changing characters, no quote marks, at most n characters."""
    t = _INVISIBLE.sub("", str(text if text is not None else ""))
    t = "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in t)
    t = re.sub(r"\s+", " ", t.replace('"', "'").replace("`", "'")).strip()
    return t if len(t) <= n else t[:n].rstrip() + "…"


def quoted(text, n: int = 80) -> str:
    """The cleaned text in quotes, so the model reads it as a value and not as an instruction."""
    return f'"{clean(text, n)}"'

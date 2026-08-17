"""
Pulse STT transcript handling.

Care extraction MUST see the raw, unredacted utterance (medication names
included). Logging may omit PHI; the pipeline must not mask it.

Pipecat is transport only. Pulse remains the STT source of truth.
"""

from __future__ import annotations

import re
from typing import Optional

# Pulse PII mode turns drug names into tokens like [COMPANYNAME_1] / [CITY_1].
_REDACTION_RE = re.compile(
    r"(?i)("
    r"\[(?:redacted|phi|pii|med(?:ication)?|name)\]|"
    r"\[[A-Z]+(?:NAME)?_\d+\]|"
    r"<redacted>|"
    r"\*{3,}|"
    r"\bredacted\b|"
    r"\[\*+\]"
    r")"
)


def preserve_raw_transcript(text: Optional[str]) -> str:
    """Keep Pulse/text tokens intact aside from edge whitespace."""
    if text is None:
        return ""
    return str(text).strip()


def looks_redacted(text: Optional[str]) -> bool:
    """True if STT looks pre-masked instead of a real utterance."""
    raw = preserve_raw_transcript(text)
    if not raw:
        return False
    return bool(_REDACTION_RE.search(raw))

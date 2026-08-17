"""
Pulse STT transcript handling.

Care extraction MUST see the raw, unredacted utterance (medication names
included). Logging may omit PHI; the pipeline must not mask it.
Pipecat is not part of this stack — Pulse is the STT source of truth.
"""

from __future__ import annotations

import re
from typing import Optional

# Placeholders that would mean the STT text was sanitized before RemiVox.
_REDACTION_RE = re.compile(
    r"(?i)(\[(?:redacted|phi|pii|med(?:ication)?|name)\]|"
    r"<redacted>|"
    r"\*{3,}|"
    r"\bredacted\b|"
    r"\[\*+\])"
)


def preserve_raw_transcript(text: Optional[str]) -> str:
    """
    Identity transform: keep Pulse/text tokens intact.

    Only trims leading/trailing whitespace. Does not remove, mask, or
    rewrite medication names or other care entities.
    """
    if text is None:
        return ""
    return str(text).strip()


def looks_redacted(text: Optional[str]) -> bool:
    """True if the string appears masked instead of a real utterance."""
    raw = preserve_raw_transcript(text)
    if not raw:
        return False
    return bool(_REDACTION_RE.search(raw))


def assert_unredacted_for_extraction(text: Optional[str]) -> str:
    """Return the raw transcript, or raise if it looks pre-redacted."""
    raw = preserve_raw_transcript(text)
    if looks_redacted(raw):
        raise ValueError("redacted_transcript")
    return raw

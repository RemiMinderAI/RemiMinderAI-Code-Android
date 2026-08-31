"""
Turning a stored visit summary into something Vox reads aloud in full.

Paramita heard the first section and then the word "the". That was not Vox
choosing to stop — it was the text arriving already cut. Two caps were stacked:
get_user_summaries returned only a 160-character preview, and the executor then
trimmed whatever it got to 500. The preview won, so about four fifths of a
typical 730-character summary never left the database.

What she should hear is all three sections in the order the app shows them,
with a spoken hand-off between each, and no tapping to get the rest — the
audience is elderly patients and caregivers, and "Next To Do" is the part they
act on.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

# A ceiling, not a target. Nothing in the TTS path enforces one, but an
# unbounded string is still an unbounded audio file, and a patient will not sit
# through ten minutes. Cutting happens at a sentence end — never mid-word,
# which is the exact failure this module exists to fix.
SPOKEN_SUMMARY_CHAR_LIMIT = 2500

MEDICATIONS_LEAD_IN = "Your medications are:"
NEXT_STEPS_LEAD_IN = "Your next steps are:"

# Real rows use action_items; rows written by the v2 normalizer use actions and
# decisions. Both shapes are in production (112 vs 17 at last count), so read
# whichever is present rather than assuming the newer one.
_ACTION_KEYS = ("action_items", "actions", "decisions")


def _items(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        text = str(item or "").strip().rstrip(".")
        if text:
            out.append(text)
    return out


def _first_present(structured: dict, keys: Iterable[str]) -> list[str]:
    for key in keys:
        items = _items(structured.get(key))
        if items:
            return items
    return []


def _join_spoken(items: list[str]) -> str:
    """Separated by periods so the voice pauses between them, not a run-on."""
    return " ".join(f"{item}." for item in items)


def _trim_to_sentence(text: str, limit: int) -> str:
    """
    Cut at the last sentence end inside the limit.

    The whole point: if this ever has to shorten something, it must not leave
    the listener on a dangling article the way the 160-character preview did.
    """
    if len(text) <= limit:
        return text
    window = text[:limit]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if cut > limit // 2:
        return window[: cut + 1]
    # No sentence break to fall back on — end on a whole word at least.
    space = window.rfind(" ")
    return (window[:space] if space > 0 else window).rstrip(" ,;:") + "."


def build_summary_speech(
    summary_text: str = "",
    structured: Optional[dict] = None,
    *,
    limit: int = SPOKEN_SUMMARY_CHAR_LIMIT,
) -> str:
    """
    The full spoken script: overview, then medications, then next steps.

    A section that has nothing in it is skipped entirely — announcing
    "Your medications are:" and then saying nothing is worse than silence.
    """
    structured = structured if isinstance(structured, dict) else {}

    overview = str(structured.get("summary") or summary_text or "").strip()
    medications = _items(structured.get("medications"))
    next_steps = _first_present(structured, _ACTION_KEYS)

    parts: list[str] = []
    if overview:
        parts.append(overview)
    if medications:
        parts.append(f"{MEDICATIONS_LEAD_IN} {_join_spoken(medications)}")
    if next_steps:
        parts.append(f"{NEXT_STEPS_LEAD_IN} {_join_spoken(next_steps)}")

    return _trim_to_sentence(" ".join(parts).strip(), limit)

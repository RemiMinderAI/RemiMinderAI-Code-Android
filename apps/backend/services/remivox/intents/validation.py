"""
Validation layer for RemiVox care intents (Part 2).

Governing rules:
  1. Complete info -> execute immediately, no follow-up questions.
  2. Incomplete info -> ask ONLY for what is missing, ONE question per turn.
  3. NEVER invent defaults. Frequency never defaults to daily; date never
     defaults to today unless the user said "today".
  4. once/None frequency is acceptable for a task or appointment only after the
     day question has been answered — a lab pickup on a named day is inherently
     one-off, which is inference from stated facts, not invention.
  5. Everything already provided is preserved across turns; a slot that is
     already filled is never asked about again.

Entities arriving here use the normalized LLM schema (see llm_extractor).
"""

from __future__ import annotations

from typing import Any, Optional

# "date_or_day" is satisfied by an explicit date, a weekday, or a recurrence that
# makes a single calendar date meaningless (daily / interval / custom).
REQUIRED_FIELDS: dict[str, dict[str, list[str]]] = {
    "CREATE_REMINDER": {
        "medication": ["title", "time", "frequency"],
        # A task or appointment needs the day it happens on; frequency is
        # inferred as "once" once that day is known (rule 4).
        "appointment": ["title", "time", "date_or_day"],
        "task": ["title", "time", "date_or_day"],
    },
}

# Ask in this order when more than one slot is missing.
FIELD_PRIORITY = ("title", "time", "date_or_day", "frequency", "interval_hours", "custom_days")

_RECURRING_FREQUENCIES = {"daily", "weekly", "twice_daily", "interval", "custom"}


def _has(entities: dict[str, Any], key: str) -> bool:
    value = (entities or {}).get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def _has_date_or_day(entities: dict[str, Any]) -> bool:
    if _has(entities, "date") or _has(entities, "day_of_week"):
        return True
    # A recurring schedule answers "which day" by itself.
    return str((entities or {}).get("frequency") or "") in _RECURRING_FREQUENCIES


def reminder_type_of(entities: dict[str, Any]) -> str:
    rtype = str((entities or {}).get("reminder_type") or "medication").strip().lower()
    return rtype if rtype in REQUIRED_FIELDS["CREATE_REMINDER"] else "medication"


def validate_create(entities: dict[str, Any]) -> list[str]:
    """
    Return the missing required field names, highest priority first.

    Empty list = ready to execute.
    """
    entities = entities or {}
    rtype = reminder_type_of(entities)
    required = REQUIRED_FIELDS["CREATE_REMINDER"][rtype]

    missing: list[str] = []
    for field in required:
        if field == "date_or_day":
            if not _has_date_or_day(entities):
                missing.append(field)
        elif not _has(entities, field):
            missing.append(field)

    # A stated frequency can itself be incomplete.
    frequency = str(entities.get("frequency") or "")
    if frequency == "interval" and not _has(entities, "interval_hours"):
        missing.append("interval_hours")
    if frequency == "custom" and not _has(entities, "custom_days"):
        missing.append("custom_days")

    order = {name: index for index, name in enumerate(FIELD_PRIORITY)}
    missing.sort(key=lambda name: order.get(name, len(order)))
    return missing


def next_question_slot(missing: list[str]) -> Optional[str]:
    """The single slot to ask about this turn (one question at a time)."""
    return missing[0] if missing else None


def apply_inferred_defaults(entities: dict[str, Any]) -> dict[str, Any]:
    """
    Fill only what is legitimately inferable from what the user already said.

    Two rules, both inference from stated facts rather than invention:

      * An explicit calendar date means one occurrence. "Remind me today at
        2:31" names the occurrence; asking "every day or just once?" after that
        is asking something the user already answered.
      * A task or appointment pinned to a named day is likewise a one-off.

    A weekday alone is NOT enough for a medication — "Metoprolol on Thursday"
    could as easily be weekly — so only an actual date generalises. Where
    nothing was stated, frequency is still never assumed.
    """
    out = dict(entities or {})
    rtype = reminder_type_of(out)
    if not out.get("frequency"):
        if _has(out, "date"):
            out["frequency"] = "once"
        elif rtype in {"task", "appointment"} and _has(out, "day_of_week"):
            out["frequency"] = "once"
    # A frequency that arrived this turn settles any earlier ambiguity, so the
    # ambiguous-phrase marker must not survive into the next round and re-ask.
    if out.get("frequency"):
        out.pop("frequency_ambiguous", None)
    return out


def validate_update(entities: dict[str, Any]) -> list[str]:
    """
    Updates need a target and at least one new value.

    Returns missing slot names ("title" for the target, "new_value" when the user
    named a reminder but nothing to change about it).
    """
    entities = entities or {}
    missing: list[str] = []
    if not _has(entities, "title"):
        missing.append("title")
    has_new_value = any(
        _has(entities, key)
        for key in ("new_time", "new_frequency", "time", "frequency", "interval_hours", "custom_days")
    )
    if not has_new_value:
        missing.append("new_value")
    return missing


def merge_entities(
    pending: Optional[dict[str, Any]],
    updates: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """
    Carry every previously captured slot forward; only non-empty updates win.

    This is what guarantees "never re-ask the medication or the time".
    """
    out = dict(pending or {})
    for key, value in (updates or {}).items():
        if value is None or value == "" or value == []:
            continue
        if key == "time_ambiguous" and not value:
            continue
        out[key] = value
    return out

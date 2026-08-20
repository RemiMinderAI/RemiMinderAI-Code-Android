"""
Adapter: normalized NLU output -> IntentResult the Action Executor already speaks.

Keeping the translation here means the executor, response builder and state
machine stay unchanged whether the entities came from Gemini or from the legacy
regex router.
"""

from __future__ import annotations

from typing import Any, Optional

from services.remivox.intents.models import IntentResult, VoxIntent
from services.remivox.intents.validation import (
    apply_inferred_defaults,
    merge_entities,
    next_question_slot,
    validate_create,
    validate_update,
)

# Normalized frequency -> the value stored in reminders.recurrence.
_FREQUENCY_TO_RECURRENCE = {
    "once": "once",
    "daily": "daily",
    "weekly": "weekly",
    "twice_daily": "twice",
    "interval": "interval",
    "custom": "custom",
}

_CARRY_PENDING_FOR = {
    VoxIntent.CREATE_REMINDER,
    VoxIntent.UPDATE_REMINDER,
}


def recurrence_for(frequency: Optional[str]) -> Optional[str]:
    if not frequency:
        return None
    return _FREQUENCY_TO_RECURRENCE.get(str(frequency), None)


def _title(entities: dict[str, Any]) -> Optional[str]:
    title = entities.get("title")
    return str(title).strip() if title else None


def _create_entities(entities: dict[str, Any]) -> dict[str, Any]:
    title = _title(entities)
    return {
        "medication": title,
        "title": title,
        "time": entities.get("time"),
        "frequency": recurrence_for(entities.get("frequency")),
        "reminder_type": entities.get("reminder_type") or "medication",
        "date": entities.get("date"),
        "day_of_week": entities.get("day_of_week"),
        "interval_hours": entities.get("interval_hours"),
        "custom_days": entities.get("custom_days"),
        "doctor_name": entities.get("doctor_name"),
    }


def _update_entities(entities: dict[str, Any], raw: str) -> dict[str, Any]:
    title = _title(entities)
    new_time = entities.get("new_time") or entities.get("time")
    new_frequency = entities.get("new_frequency") or entities.get("frequency")
    # "Change my Metoprolol at 8 AM to 10 PM": time identifies the target,
    # new_time is the value. With only one time present it is the new value,
    # never a selector — otherwise we would match the reminder already at 10 PM.
    # ...and only when the two differ: the regex fallback populates both slots
    # with the same value, which would otherwise be read as "find the reminder
    # already at the new time".
    stated_time = entities.get("time")
    match_time = (
        stated_time
        if stated_time and entities.get("new_time") and stated_time != new_time
        else None
    )
    return {
        "title_hint": title,
        "medication": title,
        "match_time": match_time,
        "time": new_time,
        "frequency": recurrence_for(new_frequency),
        "interval_hours": entities.get("interval_hours"),
        "custom_days": entities.get("custom_days"),
        "raw_transcript": raw,
    }


def to_intent_result(
    nlu: dict[str, Any],
    *,
    language: str,
    raw_transcript: str,
    pending_intent: Optional[str] = None,
    pending_entities: Optional[dict[str, Any]] = None,
) -> IntentResult:
    """
    Build the IntentResult for one turn, applying the validation layer.

    Incomplete CREATE/UPDATE intents become CLARIFY with exactly one missing slot,
    carrying every already-known entity forward in pending_entities.
    """
    intent = VoxIntent(str(nlu.get("intent") or VoxIntent.UNKNOWN.value))
    entities = dict(nlu.get("entities") or {})
    confidence = float(nlu.get("confidence") or 0.0)
    pending = dict(pending_entities or {})

    def result(
        out_intent: VoxIntent,
        out_entities: dict[str, Any],
        *,
        missing: Optional[list[str]] = None,
    ) -> IntentResult:
        return IntentResult(
            intent=out_intent,
            language=language,
            entities=out_entities,
            confidence=confidence,
            missing_slots=missing or [],
            raw_transcript=raw_transcript,
            normalized_text=raw_transcript,
        )

    # A confirmation only means something against the action that is pending.
    if intent == VoxIntent.CONFIRM_ACTION:
        return result(intent, merge_entities(pending, {"confirmed": True}))

    if intent == VoxIntent.CANCEL_ACTION:
        return result(intent, {"reason": raw_transcript})

    if intent == VoxIntent.CREATE_REMINDER:
        base = pending if pending_intent == VoxIntent.CREATE_REMINDER.value else {}
        merged = apply_inferred_defaults(merge_entities(base, entities))
        missing = validate_create(merged)
        if missing:
            slot = next_question_slot(missing)
            return result(
                VoxIntent.CLARIFY,
                {
                    "pending_intent": VoxIntent.CREATE_REMINDER.value,
                    "pending_entities": merged,
                    "missing_slots": [slot] if slot else [],
                },
                missing=[slot] if slot else [],
            )
        return result(VoxIntent.CREATE_REMINDER, _create_entities(merged))

    if intent == VoxIntent.UPDATE_REMINDER:
        base = pending if pending_intent == VoxIntent.UPDATE_REMINDER.value else {}
        merged = merge_entities(base, entities)
        missing = validate_update(merged)
        if "title" in missing:
            return result(
                VoxIntent.CLARIFY,
                {
                    "pending_intent": VoxIntent.UPDATE_REMINDER.value,
                    "pending_entities": merged,
                    "missing_slots": ["reminder"],
                },
                missing=["reminder"],
            )
        # Target known but nothing to change: the executor asks, holding the match.
        return result(VoxIntent.UPDATE_REMINDER, _update_entities(merged, raw_transcript))

    # An unintelligible answer to a pending question is a failed clarification
    # round, not a change of subject: re-ask the same slot so the round counter
    # advances and the pipeline restarts the flow once the limit is reached.
    # (A clearly different intent still wins — only UNKNOWN is treated this way.)
    if intent == VoxIntent.UNKNOWN and pending_intent in {
        VoxIntent.CREATE_REMINDER.value,
        VoxIntent.UPDATE_REMINDER.value,
    }:
        if pending_intent == VoxIntent.CREATE_REMINDER.value:
            slot = next_question_slot(validate_create(pending))
        else:
            slot = next_question_slot(validate_update(pending)) and "reminder"
        if slot:
            return result(
                VoxIntent.CLARIFY,
                {
                    "pending_intent": pending_intent,
                    "pending_entities": pending,
                    "missing_slots": [slot],
                },
                missing=[slot],
            )

    title = _title(entities)

    if intent in {
        VoxIntent.DELETE_REMINDER,
        VoxIntent.COMPLETE_REMINDER,
        VoxIntent.SKIP_REMINDER,
    }:
        # For these there is no "new" time, so any time stated is the user
        # disambiguating WHICH reminder they mean.
        out = {
            "title_hint": title,
            "medication": title,
            "raw_transcript": raw_transcript,
            "match_time": entities.get("time"),
        }
        if pending_intent == intent.value:
            out = merge_entities(pending, out)
        return result(intent, out)

    if intent == VoxIntent.SNOOZE_REMINDER:
        return result(
            intent,
            {
                "title_hint": title,
                "medication": title,
                "minutes": entities.get("minutes"),
                "match_time": entities.get("time"),
            },
        )

    return result(intent, {})


def carries_pending(intent: VoxIntent) -> bool:
    return intent in _CARRY_PENDING_FOR

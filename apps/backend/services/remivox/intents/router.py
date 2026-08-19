"""
Intent Router (Stage C) — deterministic structured intents.

No LLM. Uses extractors + conversation pending state.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from services.remivox.intents.extractors import (
    extract_frequency,
    extract_medication_or_title,
    extract_time_hhmm,
)
from services.remivox.intents.models import IntentResult, VoxIntent, VoxRecurrence
from services.remivox.languages import (
    DEFAULT_REMIVOX_LANGUAGE,
    normalize_language_code,
)

# Keep in sync with executor._checkin and the Flutter snooze action.
DEFAULT_SNOOZE_MINUTES = 30

_CANCEL_PHRASES = (
    "cancel that",
    "cancel it",
    "never mind",
    "nevermind",
    "forget it",
    "don't want that",
    "do not want that",
    "stop that",
    "no cancel",
    "no, cancel",
)

_CONFIRM_PHRASES = (
    "yes, delete",
    "yes delete",
    "yes, that's correct",
    "yes thats correct",
    "yes, that is correct",
    "confirm",
    "yes go ahead",
    "yes, go ahead",
    "do it",
    "yes please",
)

_MEDICAL_ADVICE_PHRASES = (
    "diagnose",
    "diagnosis",
    "what dose",
    "dosage",
    "increase my dose",
    "decrease my dose",
    "change my dose",
    "stop taking",
    "should i stop",
    "should i take",
    "side effect",
    "drug interaction",
    "treat my",
    "treatment for",
    "is it safe to",
    "prescribe",
)

_UPDATE_PHRASES = (
    "change",
    "modify",
    "update",
    "move",
    "switch",
    "reschedule",
    "edit",
)

_HYDRA_KNOWLEDGE_PHRASES = (
    "lab",
    "results",
    "blood",
    "test",
    "explain",
    "what does",
)

_CAREGIVER_SHARE_PHRASES = (
    "tell",
    "share",
    "let know",
)

_CAREGIVER_AUDIENCE_PHRASES = (
    "daughter",
    "son",
    "family",
    "caregiver",
)


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(p in text for p in phrases)


def _has_keyword(text: str, phrases: tuple[str, ...]) -> bool:
    return any(
        re.search(rf"\b{re.escape(phrase)}\b", text)
        for phrase in phrases
    )


def _merge_entities(
    base: dict[str, Any],
    updates: dict[str, Any],
) -> dict[str, Any]:
    out = dict(base or {})
    for key, value in (updates or {}).items():
        if value is not None and value != "":
            out[key] = value
    return out


def _create_missing(entities: dict[str, Any], text: str = "") -> list[str]:
    missing: list[str] = []
    if not (entities.get("medication") or entities.get("title")):
        missing.append("medication")
    if not entities.get("time"):
        missing.append("time")
    if not entities.get("frequency"):
        # "today" / "tonight" / "this evening" = once; otherwise default daily
        lower_text = text.lower() if isinstance(text, str) else ""
        if any(w in lower_text for w in ("today", "tonight", "this evening", "this morning", "right now", "in an hour", "once")):
            entities["frequency"] = "once"
        else:
            entities["frequency"] = "daily"
    return missing


def _extract_create_entities(text: str) -> dict[str, Any]:
    medication = extract_medication_or_title(text)
    time_hhmm = extract_time_hhmm(text)
    frequency = extract_frequency(text)
    reminder_type = "medication"
    lower = text.lower()
    if any(w in lower for w in ("appointment", "visit", "doctor", "appt")):
        reminder_type = "appointment"
    elif any(w in lower for w in ("task", "to do", "todo", "pick up", "laundry", "errand", "chore")):
        reminder_type = "task"
    entities: dict[str, Any] = {
        "medication": medication,
        "title": medication,
        "time": time_hhmm,
        "frequency": frequency.value if frequency else None,
        "reminder_type": reminder_type,
    }
    return entities


_STT_CORRECTIONS = {
    "smooth": "snooze",
    "news": "snooze",
    "snoops": "snooze",
    "moves": "move",
    "shuttle": "schedule",
    "counsel": "cancel",
    "remainder": "reminder",
    "reminds": "reminder",
}

def _correct_stt(text: str) -> str:
    """Fix common Deepgram mishears for action words."""
    words = text.split()
    corrected = []
    for w in words:
        corrected.append(_STT_CORRECTIONS.get(w.lower(), w))
    return " ".join(corrected)


def route_intent(
    *,
    text: str,
    language: str = DEFAULT_REMIVOX_LANGUAGE,
    pending_intent: Optional[str] = None,
    pending_entities: Optional[dict[str, Any]] = None,
) -> IntentResult:
    """
    Resolve user text into a structured IntentResult.

    When pending_intent is set (e.g. CREATE waiting for time), merge new
    entities into the pending create/update/delete flow.
    """
    raw = _correct_stt((text or "").strip())
    lower = raw.lower()
    lang = normalize_language_code(language)
    pending = pending_entities or {}

    if not raw:
        return IntentResult(
            intent=VoxIntent.UNKNOWN,
            language=lang,
            entities={},
            confidence=0.0,
            raw_transcript=raw,
            normalized_text=raw,
        )

    # --- Cancel/delete a specific reminder ---
    if (_has_any(lower, ("cancel", "delete", "remove", "get rid of"))
        and "reminder" in lower
        and any(w in lower for w in ("my", "the", "a"))
    ):
        hint = extract_medication_or_title(raw)
        if not hint:
            import re as _re
            m = _re.search(r"(?:cancel|delete|remove)\s+(?:my\s+|the\s+|a\s+)?(.+?)\s+reminder", raw, _re.IGNORECASE)
            if m:
                hint = m.group(1).strip()
        return IntentResult(
            intent=VoxIntent.DELETE_REMINDER,
            language=lang,
            entities={"title_hint": hint, "medication": hint, "raw_transcript": raw},
            confidence=0.95,
            raw_transcript=raw,
            normalized_text=raw,
        )

    # --- Cancel pending action (bare "cancel", "no", "nope") ---
    if _has_any(lower, _CANCEL_PHRASES) or lower in {"no", "cancel", "nope"}:
        return IntentResult(
            intent=VoxIntent.CANCEL_ACTION,
            language=lang,
            entities={"reason": raw},
            confidence=1.0,
            raw_transcript=raw,
            normalized_text=raw,
        )

    # --- Confirm pending sensitive action ---
    if pending_intent == VoxIntent.DELETE_REMINDER.value and (
        _has_any(lower, _CONFIRM_PHRASES)
        or lower in {"yes", "yeah", "yep", "correct", "ok", "okay"}
    ):
        return IntentResult(
            intent=VoxIntent.CONFIRM_ACTION,
            language=lang,
            entities=_merge_entities(pending, {"confirmed": True}),
            confidence=1.0,
            raw_transcript=raw,
            normalized_text=raw,
        )

    if _has_any(lower, _CONFIRM_PHRASES) and pending_intent:
        return IntentResult(
            intent=VoxIntent.CONFIRM_ACTION,
            language=lang,
            entities=_merge_entities(pending, {"confirmed": True}),
            confidence=0.9,
            raw_transcript=raw,
            normalized_text=raw,
        )

    # --- Medical advice refusal (before create heuristics) ---
    if _has_any(lower, _MEDICAL_ADVICE_PHRASES):
        return IntentResult(
            intent=VoxIntent.MEDICAL_ADVICE_REFUSAL,
            language=lang,
            entities={},
            confidence=1.0,
            raw_transcript=raw,
            normalized_text=raw,
        )

    # --- Continue pending CREATE with slot fills ("8 PM", "every day") ---
    if pending_intent == VoxIntent.CREATE_REMINDER.value:
        updates = _extract_create_entities(raw)
        # Pure time/frequency utterances may not yield a medication — keep pending.
        if not updates.get("medication") and len(raw.split()) <= 6:
            updates["medication"] = None
            updates["title"] = None
        merged = _merge_entities(pending, {k: v for k, v in updates.items() if v})
        missing = _create_missing(merged, raw)
        if missing:
            return IntentResult(
                intent=VoxIntent.CLARIFY,
                language=lang,
                entities={
                    "pending_intent": VoxIntent.CREATE_REMINDER.value,
                    "pending_entities": merged,
                    "missing_slots": missing,
                },
                missing_slots=missing,
                confidence=0.95,
                raw_transcript=raw,
                normalized_text=raw,
            )
        return IntentResult(
            intent=VoxIntent.CREATE_REMINDER,
            language=lang,
            entities=merged,
            confidence=0.95,
            raw_transcript=raw,
            normalized_text=raw,
        )

    # --- Pending DELETE: user names which reminder ---
    if pending_intent == VoxIntent.DELETE_REMINDER.value:
        hint = extract_medication_or_title(raw) or raw
        merged = _merge_entities(pending, {"title_hint": hint, "medication": hint})
        return IntentResult(
            intent=VoxIntent.DELETE_REMINDER,
            language=lang,
            entities=merged,
            confidence=0.9,
            raw_transcript=raw,
            normalized_text=raw,
        )

    # --- Pending UPDATE slot fill ---
    if pending_intent == VoxIntent.UPDATE_REMINDER.value:
        updates: dict[str, Any] = {}
        t = extract_time_hhmm(raw)
        f = extract_frequency(raw)
        m = extract_medication_or_title(raw)
        if t:
            updates["time"] = t
        if f:
            updates["frequency"] = f.value
        if m:
            updates["medication"] = m
            updates["title_hint"] = m
        merged = _merge_entities(pending, updates)
        return IntentResult(
            intent=VoxIntent.UPDATE_REMINDER,
            language=lang,
            entities=merged,
            confidence=0.9,
            raw_transcript=raw,
            normalized_text=raw,
        )

    # --- Create reminder ---
    create_triggers = (
        "set",
        "create",
        "add",
        "schedule",
        "remind me",
        "reminder for",
        "reminder about",
        "set up a reminder",
    )
    if any(t in lower for t in create_triggers) or re.search(
        r"\breminder\b", lower
    ):
        # Prefer create over update when "set/create" present
        if any(
            t in lower
            for t in ("set", "create", "add", "schedule", "remind me", "reminder for", "reminder about")
        ) and not any(t in lower for t in ("update", "change", "move", "reschedule", "delete", "remove")):
            entities = _extract_create_entities(raw)
            missing = _create_missing(entities, raw)
            if missing:
                return IntentResult(
                    intent=VoxIntent.CLARIFY,
                    language=lang,
                    entities={
                        "pending_intent": VoxIntent.CREATE_REMINDER.value,
                        "pending_entities": {k: v for k, v in entities.items() if v},
                        "missing_slots": missing,
                    },
                    missing_slots=missing,
                    confidence=0.9,
                    raw_transcript=raw,
                    normalized_text=raw,
                )
            return IntentResult(
                intent=VoxIntent.CREATE_REMINDER,
                language=lang,
                entities=entities,
                confidence=1.0,
                raw_transcript=raw,
                normalized_text=raw,
            )

    # --- Delete ---
    if any(w in lower for w in ("delete", "remove", "get rid of")) and "reminder" in lower:
        hint = extract_medication_or_title(raw)
        return IntentResult(
            intent=VoxIntent.DELETE_REMINDER,
            language=lang,
            entities={"title_hint": hint, "medication": hint, "raw_transcript": raw},
            confidence=0.95,
            raw_transcript=raw,
            normalized_text=raw,
        )

    # --- Update ---
    update_time = extract_time_hhmm(raw)
    update_frequency = extract_frequency(raw)
    update_hint = extract_medication_or_title(raw)
    update_requested = _has_keyword(lower, _UPDATE_PHRASES) or (
        "actually" in lower and (update_time or update_frequency)
    )
    # Extract medication from "change my X to" / "move X to" patterns
    if not update_hint and update_requested:
        m = re.search(
            r"(?:change|update|move|switch|reschedule|modify)\s+(?:my\s+)?(.+?)\s+(?:to|from|at)\b",
            raw, re.IGNORECASE,
        )
        if m:
            hint_text = m.group(1).strip()
            hint_text = re.sub(r"\b(reminder|the)\b", "", hint_text, flags=re.I).strip()
            if len(hint_text) >= 2:
                update_hint = hint_text
    update_targeted = (
        "reminder" in lower
        or "that" in lower
        or bool(update_hint)
        or (update_requested and update_time)
    )
    if update_requested and update_targeted:
        entities = {
            "title_hint": update_hint,
            "medication": update_hint,
            "time": update_time,
            "frequency": (
                update_frequency.value if update_frequency else None
            ),
            "raw_transcript": raw,
        }
        return IntentResult(
            intent=VoxIntent.UPDATE_REMINDER,
            language=lang,
            entities={k: v for k, v in entities.items() if v},
            confidence=0.9,
            raw_transcript=raw,
            normalized_text=raw,
        )

    # --- Complete / taken ---
    if any(
        w in lower
        for w in (
            "i took",
            "i've taken",
            "taken",
            "mark as taken",
            "mark taken",
            "mark done",
            "mark as done",
            "mark as completed",
            "mark my",
            "completed",
            "done taking",
            "i finished",
            "done with",
        )
    ):
        hint = extract_medication_or_title(raw) or raw
        return IntentResult(
            intent=VoxIntent.COMPLETE_REMINDER,
            language=lang,
            entities={"title_hint": hint, "medication": hint, "raw_transcript": raw},
            confidence=0.95,
            raw_transcript=raw,
            normalized_text=raw,
        )

    # --- Snooze ---
    if any(w in lower for w in ("snooze", "remind me later", "later")):
        minutes = DEFAULT_SNOOZE_MINUTES
        m = re.search(r"(\d{1,3})\s*(?:min|minute)", lower)
        if m:
            minutes = max(5, min(180, int(m.group(1))))
        hint = extract_medication_or_title(raw)
        return IntentResult(
            intent=VoxIntent.SNOOZE_REMINDER,
            language=lang,
            entities={"title_hint": hint, "minutes": minutes, "raw_transcript": raw},
            confidence=0.9,
            raw_transcript=raw,
            normalized_text=raw,
        )

    # --- Skip ---
    if re.search(r"\bskip\b", lower):
        hint = extract_medication_or_title(raw)
        return IntentResult(
            intent=VoxIntent.SKIP_REMINDER,
            language=lang,
            entities={"title_hint": hint, "raw_transcript": raw},
            confidence=0.9,
            raw_transcript=raw,
            normalized_text=raw,
        )

    # --- Conversational handoffs (before appointment/summary reads) ---
    if _has_keyword(lower, _CAREGIVER_SHARE_PHRASES) and _has_keyword(
        lower,
        _CAREGIVER_AUDIENCE_PHRASES,
    ):
        return IntentResult(
            intent=VoxIntent.CAREGIVER_BRIEF,
            language=lang,
            entities={"audience": "caregiver"},
            confidence=0.95,
            raw_transcript=raw,
            normalized_text=raw,
        )

    if _has_keyword(lower, _HYDRA_KNOWLEDGE_PHRASES):
        return IntentResult(
            intent=VoxIntent.UNKNOWN,
            language=lang,
            entities={
                "route": "hydra",
                "conversation_type": "knowledge",
            },
            confidence=0.95,
            raw_transcript=raw,
            normalized_text=raw,
        )

    # --- Reads ---
    if any(
        w in lower
        for w in (
            "medications today",
            "medicine today",
            "meds today",
            "read my medication",
            "my medications",
            "what medicine",
            "what meds",
            "my meds",
            "what are my med",
            "today's med",
            "medications for today",
            "tell me my med",
            "list my med",
            "show my med",
            "do i have med",
            "any medications",
            "any meds",
            "medication list",
            "what medications",
        )
    ):
        return IntentResult(
            intent=VoxIntent.READ_TODAY_MEDICATIONS,
            language=lang,
            entities={},
            confidence=1.0,
            raw_transcript=raw,
            normalized_text=raw,
        )

    if any(
        w in lower
        for w in (
            "appointment", "appt", "next visit", "follow-up", "follow up",
            "followup", "doctor appointment", "doctor visit", "schedule visit",
            "book appointment", "next doctor", "see the doctor",
        )
    ):
        return IntentResult(
            intent=VoxIntent.READ_APPOINTMENTS,
            language=lang,
            entities={},
            confidence=0.9,
            raw_transcript=raw,
            normalized_text=raw,
        )

    if any(
        w in lower
        for w in (
            "last doctor", "last visit", "summary", "doctor visit", "notes",
            "read my last", "visit summary", "read summary", "read me the",
            "what did the doctor", "what happened at", "doctor said",
            "last appointment", "previous visit", "show summary",
            "tell me about my visit", "my last summary",
        )
    ):
        return IntentResult(
            intent=VoxIntent.READ_DOCTOR_SUMMARY,
            language=lang,
            entities={},
            confidence=0.9,
            raw_transcript=raw,
            normalized_text=raw,
        )

    if any(
        w in lower
        for w in (
            "caregiver brief",
            "care brief",
            "who missed",
            "who is due",
            "who's due",
            "my patients",
            "patient status",
        )
    ):
        return IntentResult(
            intent=VoxIntent.CAREGIVER_BRIEF,
            language=lang,
            entities={},
            confidence=0.95,
            raw_transcript=raw,
            normalized_text=raw,
        )

    if any(w in lower for w in ("help", "what can you do", "what do you do")):
        return IntentResult(
            intent=VoxIntent.HELP,
            language=lang,
            entities={},
            confidence=1.0,
            raw_transcript=raw,
            normalized_text=raw,
        )

    return IntentResult(
        intent=VoxIntent.UNKNOWN,
        language=lang,
        entities={},
        confidence=0.2,
        raw_transcript=raw,
        normalized_text=raw,
    )

"""
Validate care-critical fields after intent + entity extraction.

Runs before the Action Executor so reminder mutations never execute on
incomplete or Pulse-redacted transcripts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from services.remivox.intents.models import IntentResult, VoxIntent
from services.remivox.transcript import looks_redacted, preserve_raw_transcript

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
IDENTITY_SLOTS = ("medication", "title_hint", "title", "reminder_id")


@dataclass
class CareValidation:
    ok: bool
    intent_result: IntentResult
    missing_slots: list[str] = field(default_factory=list)
    blocked_reason: Optional[str] = None


def _has_identity(entities: dict[str, Any]) -> bool:
    return any(str(entities.get(k) or "").strip() for k in IDENTITY_SLOTS)


def _medication_label(entities: dict[str, Any]) -> str:
    return str(
        entities.get("medication")
        or entities.get("title")
        or entities.get("title_hint")
        or ""
    ).strip()


def _create_entities(intent_result: IntentResult) -> dict[str, Any]:
    entities = dict(intent_result.entities or {})
    if intent_result.intent == VoxIntent.CLARIFY:
        pending = dict(entities.get("pending_entities") or {})
        if pending:
            return pending
    return entities


def _create_missing(entities: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not _medication_label(entities):
        missing.append("medication")
    time_val = str(entities.get("time") or "").strip()
    if not time_val or not _HHMM_RE.match(time_val):
        missing.append("time")
    if not str(entities.get("frequency") or "").strip():
        missing.append("frequency")
    return missing


def _as_clarify(
    intent_result: IntentResult,
    pending_intent: str,
    entities: dict[str, Any],
    missing: list[str],
) -> IntentResult:
    return IntentResult(
        intent=VoxIntent.CLARIFY,
        language=intent_result.language,
        entities={
            "pending_intent": pending_intent,
            "pending_entities": {k: v for k, v in entities.items() if v not in (None, "")},
            "missing_slots": missing,
        },
        missing_slots=missing,
        confidence=min(intent_result.confidence, 0.95),
        raw_transcript=intent_result.raw_transcript,
        normalized_text=intent_result.normalized_text,
    )


def validate_care_critical_fields(intent_result: IntentResult) -> CareValidation:
    raw = preserve_raw_transcript(intent_result.raw_transcript)
    intent = intent_result.intent

    if looks_redacted(raw):
        blocked = IntentResult(
            intent=VoxIntent.UNKNOWN,
            language=intent_result.language,
            entities={},
            confidence=0.0,
            missing_slots=["unredacted_transcript"],
            raw_transcript=intent_result.raw_transcript,
            normalized_text=intent_result.normalized_text,
        )
        return CareValidation(
            ok=False,
            intent_result=blocked,
            missing_slots=["unredacted_transcript"],
            blocked_reason="redacted_transcript",
        )

    if intent == VoxIntent.CLARIFY:
        missing = list(intent_result.missing_slots or [])
        pending = _create_entities(intent_result)
        pending_intent = str((intent_result.entities or {}).get("pending_intent") or "")
        if pending_intent == VoxIntent.CREATE_REMINDER.value:
            missing = _create_missing(pending) or missing
        return CareValidation(ok=True, intent_result=intent_result, missing_slots=missing)

    if intent == VoxIntent.CREATE_REMINDER:
        entities = dict(intent_result.entities or {})
        missing = _create_missing(entities)
        if missing:
            return CareValidation(
                ok=True,
                intent_result=_as_clarify(
                    intent_result, VoxIntent.CREATE_REMINDER.value, entities, missing
                ),
                missing_slots=missing,
                blocked_reason="missing_care_fields",
            )
        return CareValidation(ok=True, intent_result=intent_result)

    if intent == VoxIntent.UPDATE_REMINDER:
        entities = dict(intent_result.entities or {})
        if not _has_identity(entities):
            missing = ["medication"]
            return CareValidation(
                ok=True,
                intent_result=_as_clarify(
                    intent_result, VoxIntent.UPDATE_REMINDER.value, entities, missing
                ),
                missing_slots=missing,
                blocked_reason="missing_care_fields",
            )
        return CareValidation(ok=True, intent_result=intent_result)

    if intent == VoxIntent.DELETE_REMINDER:
        entities = dict(intent_result.entities or {})
        if not _has_identity(entities):
            missing = ["reminder"]
            return CareValidation(
                ok=True,
                intent_result=_as_clarify(
                    intent_result, VoxIntent.DELETE_REMINDER.value, entities, missing
                ),
                missing_slots=missing,
                blocked_reason="missing_care_fields",
            )
        return CareValidation(ok=True, intent_result=intent_result)

    return CareValidation(ok=True, intent_result=intent_result)

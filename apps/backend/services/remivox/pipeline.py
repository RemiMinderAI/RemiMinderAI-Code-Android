"""
RemiVox v2 care-turn pipeline (Stage C).

Voice Layer (Pulse/Lightning) stays in route/remivox.py + remivox.voice.
This pipeline owns: Intent Router → Action Executor → Response Builder (+ state).

Hydra is NOT used here for care actions.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from services.remivox.actions.executor import execute_intent
from services.remivox.actions.types import ActionResult
from services.remivox.intents.adapter import to_intent_result
from services.remivox.intents.llm_extractor import extract_care_intent, regex_fallback
from services.remivox.intents.models import VoxIntent
from services.remivox.languages import (
    DEFAULT_REMIVOX_LANGUAGE,
    resolve_session_language,
)
from services.remivox.observability import log_action_execution, log_intent_decision, log_interaction
from services.remivox.response.builder import build_response
from services.remivox.state.conversation import (
    clear_state,
    get_state,
    upsert_pending,
)

logger = logging.getLogger(__name__)

# Two unanswered clarification rounds, then start the whole request over rather
# than trapping the user in a question loop.
MAX_CLARIFY_ROUNDS = 2
_CLARIFY_ROUNDS_KEY = "_clarify_rounds"


def _nlu_mode() -> str:
    """REMIVOX_NLU=llm (default) | regex — instant rollback path."""
    return (os.getenv("REMIVOX_NLU") or "llm").strip().lower()


async def resolve_intent(
    *,
    text: str,
    language: str,
    timezone_name: str,
    pending_intent: Optional[str],
    pending_entities: Optional[dict[str, Any]],
    missing_slots: Optional[list[str]],
) -> tuple[Any, dict[str, Any]]:
    """Run the NLU layer (LLM or regex) and adapt it to an IntentResult."""
    context = {
        "pending_intent": pending_intent,
        "pending_entities": pending_entities or {},
        "missing_slots": missing_slots or [],
    }
    if _nlu_mode() == "regex":
        nlu = regex_fallback(text, context, timezone_name=timezone_name, language=language)
    else:
        nlu = await extract_care_intent(
            text,
            context,
            timezone_name=timezone_name,
            language=language,
        )

    intent_result = to_intent_result(
        nlu,
        language=language,
        raw_transcript=text,
        pending_intent=pending_intent,
        pending_entities=pending_entities,
    )
    return intent_result, nlu


async def run_care_turn(
    *,
    user_uuid: str,
    text: str,
    language: str = DEFAULT_REMIVOX_LANGUAGE,
    detected_language: Optional[str] = None,
    reminders: dict,
    summaries: list[dict],
    timezone_name: str = "UTC",
    session_id: Optional[str] = None,
    transcript: Optional[str] = None,
) -> dict[str, Any]:
    """
    Deterministic care workflow for one user turn (text already normalized).

    Returns dict compatible with RemiVoxBriefingResponse fields plus intent metadata.
    """
    sid = (session_id or "default").strip() or "default"
    detected = detected_language or language or DEFAULT_REMIVOX_LANGUAGE
    # Preserve detected/session language — do not force English.
    lang = resolve_session_language(
        detected_language=detected,
        preferred_language=language or DEFAULT_REMIVOX_LANGUAGE,
        has_audio=bool(detected_language),
    )

    prior = get_state(user_uuid, sid)
    pending_intent = prior.pending_intent if prior else None
    pending_entities = prior.pending_entities if prior else None

    intent_result, nlu = await resolve_intent(
        text=text,
        language=lang,
        timezone_name=timezone_name or "UTC",
        pending_intent=pending_intent,
        pending_entities=pending_entities,
        missing_slots=prior.missing_slots if prior else None,
    )
    log_intent_decision(
        user_id=user_uuid,
        intent=intent_result.intent.value,
        confidence=intent_result.confidence,
        missing_slots=intent_result.missing_slots,
        detected_language=detected,
        session_id=sid,
    )

    action_result: Optional[ActionResult] = None
    if intent_result.intent == VoxIntent.CLARIFY:
        rounds = int((pending_entities or {}).get(_CLARIFY_ROUNDS_KEY) or 0) + 1
        if rounds > MAX_CLARIFY_ROUNDS:
            logger.info("VOX_NLU clarify limit reached (%d rounds) — restarting flow", rounds)
            action_result = ActionResult(
                success=False,
                action="restart_flow",
                message_key="restart_flow",
                payload={},
                clear_pending=True,
            )
        else:
            carried = dict(intent_result.entities.get("pending_entities") or {})
            carried[_CLARIFY_ROUNDS_KEY] = rounds
            intent_result.entities["pending_entities"] = carried

    if action_result is None:
        action_result = await execute_intent(
            intent_result=intent_result,
            user_uuid=user_uuid,
            reminders=reminders,
            summaries=summaries,
            timezone_name=timezone_name or "UTC",
        )

    validation_result = "ok"
    if action_result.missing_slots or action_result.message_key in {
        "clarify_missing_slots",
        "ambiguous_reminder",
        "ask_which_reminder",
        "confirm_delete",
        "no_match",
    }:
        validation_result = action_result.message_key
    execution_result = "success" if action_result.success else (action_result.message_key or "failed")

    log_action_execution(
        user_id=user_uuid,
        action=action_result.action,
        validation_result=validation_result,
        execution_result=execution_result,
        success=action_result.success,
        session_id=sid,
        error=action_result.error,
    )

    # A turn keeps the conversation open only by ASKING for something. Anything
    # else ends it, whether it succeeded or was declined.
    #
    # This used to read "success and not pending_intent", so a refusal — which
    # is neither successful nor a question — matched no branch and left the
    # previous pending state exactly where it was. A create half-way through
    # collecting slots would survive "mark done Aspirin" being turned down, and
    # the next thing the user said finished it: a patient saying they had TAKEN
    # a drug ended up with a future dose of it scheduled. Thirteen refusal paths
    # had that shape, and only some of them had thought to set clear_pending.
    #
    # Declaring a pending intent is now the only way to hold the turn open, so a
    # new refusal cannot forget to end it.
    if action_result.clear_pending or not action_result.pending_intent:
        clear_state(user_uuid, sid)
    elif action_result.pending_intent:
        upsert_pending(
            user_id=user_uuid,
            session_id=sid,
            language=lang,
            detected_language=detected,
            pending_intent=action_result.pending_intent,
            pending_entities=action_result.pending_entities,
            missing_slots=action_result.missing_slots,
        )
    elif intent_result.intent.value == "CLARIFY":
        upsert_pending(
            user_id=user_uuid,
            session_id=sid,
            language=lang,
            detected_language=detected,
            pending_intent=str(
                (intent_result.entities or {}).get("pending_intent") or ""
            )
            or None,
            pending_entities=dict(
                (intent_result.entities or {}).get("pending_entities") or {}
            ),
            missing_slots=list(intent_result.missing_slots or []),
        )

    built = build_response(
        intent_result=intent_result,
        action_result=action_result,
        reply_language=lang,
    )

    log_interaction(
        user_id=user_uuid,
        detected_language=detected,
        transcript=None,  # PHI — never log
        intent=intent_result.intent.value,
        entities=None,  # PHI — never log
        action=action_result.action,
        success=action_result.success,
        response_language=lang,
        session_id=sid,
        error=action_result.error,
        confidence=intent_result.confidence,
        missing_slots=intent_result.missing_slots or action_result.missing_slots,
        validation_result=validation_result,
        execution_result=execution_result,
        extra={"message_key": action_result.message_key},
    )

    pending_out = None
    state_now = get_state(user_uuid, sid)
    if state_now and state_now.pending_intent:
        pending_out = {
            "intent": state_now.pending_intent,
            "entities": state_now.pending_entities,
            "missing_slots": state_now.missing_slots,
        }

    return {
        "text": built["text"],
        "action": action_result.action,
        "action_payload": {
            **(action_result.payload or {}),
            "intent": intent_result.intent.value,
            "entities": intent_result.entities,
            "pending": pending_out,
        },
        "reply_language": lang,
        "intent": intent_result.intent.value,
        "entities": intent_result.entities,
        "success": action_result.success,
        "nlu_source": nlu.get("source"),
        # Billing needs to tell a user-caused refusal (no_match) apart from our
        # own failure (create_failed); action + success alone cannot.
        "message_key": action_result.message_key,
    }


async def run_pipeline(
    *,
    user_uuid: str,
    prompt: Optional[str] = None,
    audio_base64: Optional[str] = None,
    content_type: str = "audio/wav",
    reply_language: str = DEFAULT_REMIVOX_LANGUAGE,
    timezone_name: str = "UTC",
    auto_detect_language: bool = True,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Full end-to-end entry (optional). Stage C route still owns Voice Layer calls;
    prefer run_care_turn after STT/translate.
    """
    _ = (
        audio_base64,
        content_type,
        auto_detect_language,
    )
    if not (prompt or "").strip():
        raise ValueError("run_pipeline requires prompt text in Stage C route wiring")
    return await run_care_turn(
        user_uuid=user_uuid,
        text=prompt or "",
        language=reply_language,
        reminders={"today": [], "upcoming": [], "past": []},
        summaries=[],
        timezone_name=timezone_name,
        session_id=session_id,
    )

"""
Action Executor (Stage C).

Protected care actions: Intent → validate → reminder_service → DB.
Hydra must never call these side effects directly.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from schemas.reminder_schemas import ReminderAction, ReminderCreate, ReminderUpdate
from services.reminder_service import (
    ReminderNotDueError,
    cancel_reminder,
    complete_reminder,
    create_new_reminder,
    skip_reminder,
    snooze_reminder,
    update_reminder_details,
)
from services.remivox.actions.types import ActionResult
from services.remivox.intents.extractors import (
    all_active_reminders,
    find_high_confidence_reminder,
    reminder_local_hhmm,
    reminder_title,
    resolve_reminder,
)
from services.remivox.intents.adapter import SUMMARY_DATE_ROUNDS_KEY
from services.remivox.intents.models import IntentResult, PROTECTED_ACTIONS, VoxIntent
from services.remivox.intents.router import DEFAULT_SNOOZE_MINUTES
from services.remivox.summary_speech import build_summary_speech

# Two questions about the same date is a conversation; three is a loop.
MAX_SUMMARY_DATE_ASKS = 2
from services.remivox_intents import build_caregiver_brief

logger = logging.getLogger(__name__)


_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _tz(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return timezone.utc


def _scheduled_from_hhmm(time_hhmm: str, timezone_name: str) -> datetime:
    """Next occurrence of a wall-clock time, in UTC."""
    hour, minute = [int(x) for x in time_hhmm.split(":", 1)]
    tz = _tz(timezone_name)
    now = datetime.now(tz)
    scheduled = now.replace(hour=hour % 24, minute=minute % 60, second=0, microsecond=0)
    if scheduled <= now:
        scheduled += timedelta(days=1)
    return scheduled.astimezone(timezone.utc)


def _scheduled_from_parts(
    time_hhmm: str,
    timezone_name: str,
    *,
    date_iso: Optional[str] = None,
    day_of_week: Optional[str] = None,
) -> datetime:
    """
    Resolve time + optional date/weekday to an absolute UTC instant.

    An explicit date wins; then a named weekday (the next one, today included if
    the time has not passed); otherwise the next occurrence of the time.
    """
    hour, minute = [int(x) for x in str(time_hhmm).split(":", 1)]
    tz = _tz(timezone_name)
    now = datetime.now(tz)

    if date_iso:
        try:
            day = datetime.strptime(str(date_iso), "%Y-%m-%d").date()
            scheduled = datetime(
                day.year, day.month, day.day, hour % 24, minute % 60, tzinfo=tz
            )
            # "Thursday at 3 PM" said at 3:23 PM on a Thursday means NEXT
            # Thursday. The model resolves the weekday to today regardless, so
            # roll forward rather than scheduling in the past. Only when the user
            # named a weekday — an explicit calendar date is taken at face value.
            if day_of_week and scheduled <= now:
                scheduled += timedelta(days=7)
            return scheduled.astimezone(timezone.utc)
        except ValueError:
            logger.warning("Vox v2 ignoring unparseable date for scheduling")

    if day_of_week:
        wanted = _WEEKDAY_INDEX.get(str(day_of_week).strip().lower())
        if wanted is not None:
            scheduled = now.replace(
                hour=hour % 24, minute=minute % 60, second=0, microsecond=0
            )
            delta = (wanted - scheduled.weekday()) % 7
            if delta == 0 and scheduled <= now:
                delta = 7
            return (scheduled + timedelta(days=delta)).astimezone(timezone.utc)

    return _scheduled_from_hhmm(time_hhmm, timezone_name)


def _local_display(scheduled: Optional[datetime], item: dict) -> str:
    """"tomorrow at 10 PM" — spoken form of when a reminder is actually due."""
    if scheduled is None:
        return ""
    tz = _tz(str(item.get("timezone") or "UTC"))
    local = scheduled.astimezone(tz)
    now = datetime.now(tz)
    delta_days = (local.date() - now.date()).days
    day = {0: "today", 1: "tomorrow"}.get(delta_days) or local.strftime("%A")
    hour = local.hour % 12 or 12
    meridiem = "AM" if local.hour < 12 else "PM"
    clock = f"{hour}:{local.minute:02d} {meridiem}" if local.minute else f"{hour} {meridiem}"
    return f"{day} at {clock}"


# Which intent a check-in mode resumes as, so a disambiguation answer is matched
# against the action the user was actually in the middle of.
_CHECKIN_INTENT = {
    "complete": VoxIntent.COMPLETE_REMINDER.value,
    "skip": VoxIntent.SKIP_REMINDER.value,
    "snooze": VoxIntent.SNOOZE_REMINDER.value,
}


def _candidate_payload(items: list[dict], timezone_name: str) -> dict[str, Any]:
    """
    Candidates for a "which one?" question, with the time that tells them apart.

    Two rows both called "Metoprolol" make the question unanswerable when only
    the names are read back. The time is the one thing that distinguishes a
    morning dose from an evening one, and it is what the patient will say in
    reply, so it has to be in the question.
    """
    return {
        "candidates": [reminder_title(i) for i in items],
        "candidate_times": [reminder_local_hhmm(i, timezone_name) for i in items],
    }


def _time_mismatch_result(
    action: str,
    entities: dict[str, Any],
    candidates: list[dict],
    timezone_name: str,
) -> ActionResult:
    """The user named a time no reminder with that title is set for."""
    return ActionResult(
        success=False,
        action=action,
        message_key="time_mismatch",
        payload={
            "title": entities.get("title_hint") or entities.get("medication") or "",
            "asked_time": entities.get("match_time"),
            "available_times": [
                reminder_local_hhmm(item, timezone_name) for item in candidates[:4]
            ],
        },
        error="stated time matched no reminder",
    )


def _summary_local_day(row: dict, timezone_name: str) -> Optional[date]:
    """
    The day the VISIT happened, in the user's own timezone.

    Matched against what the user said, so it has to be the appointment rather
    than the paperwork. get_user_summaries aliases s.created_at as "visit_date"
    — when the summary text was generated — and the visit details screen has a
    regenerate button, so that value moves to the day somebody pressed it.
    visit_created_at is v.created_at and stays put; visit_date is only the
    fallback for a caller that predates it.

    The column is timestamptz, so the calendar day depends on where the user
    is: a 9 PM visit in Toronto is already tomorrow in UTC. That is not a
    detail — of the five rows whose two timestamps fall on different UTC days,
    four are the same day once converted to the patient's timezone, and only
    one is a real regeneration lag. Comparing in UTC would invent four
    mismatches and miss the point of the one that counts.
    """
    raw = row.get("visit_created_at") or row.get("visit_date")
    if not raw:
        return None
    text = str(raw).strip().replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_tz(timezone_name)).date()


_NUMERIC_DATE_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?$")


def _parse_iso_day(value: str) -> Optional[date]:
    """The ISO day this is, or None when it is not one."""
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _order_readings(phrase: str, today: date) -> list[date]:
    """
    The two dates an all-numeric phrase could mean, when it could mean two.

    "08/12/2026" is 8 December writing month second and 12 August writing it
    first, and speech-to-text produces the first because smart_format writes
    US order. Only ask the two-way question when there genuinely are two
    readings — if either number is over 12 the order is forced, and if the
    phrase is words rather than numbers there is nothing to name.
    """
    match = _NUMERIC_DATE_RE.match((phrase or "").strip())
    if not match:
        return []
    first, second, year_text = match.groups()
    a, b = int(first), int(second)
    if a > 12 or b > 12 or a < 1 or b < 1:
        return []

    if year_text:
        year = int(year_text)
        if year < 100:
            year += 2000
        years = [year]
    else:
        years = []

    readings: list[date] = []
    for day, month in ((b, a), (a, b)):  # day-first reading, then month-first
        for candidate_year in years or [today.year, today.year - 1]:
            try:
                found = date(candidate_year, month, day)
            except ValueError:
                continue
            if not years and found > today:
                continue
            readings.append(found)
            break
    # Two identical readings ("05/05") are not a choice.
    unique = []
    for r in readings:
        if r not in unique:
            unique.append(r)
    return unique if len(unique) == 2 else []


def _spoken_day(value: date, today: Optional[date] = None) -> str:
    """
    "25 August" — the day named back, so a wrong one is audible.

    The year is spoken only when it is not the current one: saying it every
    time is noise, but leaving it off "31 December 2030" hides the very thing
    that makes the answer make sense.
    """
    spoken = f"{value.day} {value.strftime('%B')}"
    if today is not None and value.year != today.year:
        spoken = f"{spoken} {value.year}"
    return spoken


def _summary_speech(row: dict) -> tuple[str, str]:
    """Title and full spoken text for one summary row."""
    title = str(row.get("title") or "your doctor visit").strip()
    spoken = build_summary_speech(
        str(row.get("summary") or row.get("summary_text") or "").strip(),
        row.get("structured"),
    )
    return title, spoken or str(row.get("summary_preview") or "").strip()


def _latest_summary(summaries: list[dict]) -> tuple[str, str]:
    """
    The newest visit, and the full script to read for it.

    summary_preview stays last in the fallback chain on purpose: it is a
    160-character list-row excerpt, and reading it aloud is what ended the
    reply on a dangling "the". It is only reached now when a row genuinely has
    nothing else.
    """
    if not summaries:
        return "your latest doctor visit", ""
    latest = summaries[0]
    title = str(latest.get("title") or "your latest doctor visit").strip()
    spoken = build_summary_speech(
        str(latest.get("summary") or latest.get("summary_text") or "").strip(),
        latest.get("structured"),
    )
    return title, spoken or str(latest.get("summary_preview") or "").strip()


async def execute_intent(
    *,
    intent_result: IntentResult,
    user_uuid: str,
    reminders: dict,
    summaries: list[dict],
    timezone_name: str = "UTC",
) -> ActionResult:
    intent = intent_result.intent
    entities = dict(intent_result.entities or {})

    if intent == VoxIntent.CLARIFY:
        pending_intent = str(entities.get("pending_intent") or "")
        pending_entities = dict(entities.get("pending_entities") or {})
        missing = list(entities.get("missing_slots") or intent_result.missing_slots or [])
        return ActionResult(
            success=True,
            action="clarify",
            message_key="clarify_missing_slots",
            payload={"missing_slots": missing, "pending_entities": pending_entities},
            pending_intent=pending_intent or None,
            pending_entities=pending_entities,
            missing_slots=missing,
        )

    if intent == VoxIntent.CANCEL_ACTION:
        return ActionResult(
            success=True,
            action="cancel_action",
            message_key="cancelled",
            payload={},
            clear_pending=True,
        )

    if intent == VoxIntent.CONFIRM_ACTION:
        # Confirm only completes a pending DELETE (sensitive).
        rid = entities.get("reminder_id")
        title = entities.get("medication") or entities.get("title_hint") or "that reminder"
        if not rid:
            return ActionResult(
                success=False,
                action="confirm_action",
                message_key="confirm_nothing_pending",
                error="No pending delete to confirm",
                clear_pending=True,
            )
        ok = await cancel_reminder(str(rid), user_uuid)
        if not ok:
            return ActionResult(
                success=False,
                action="delete_reminder",
                message_key="delete_failed",
                error="cancel_reminder returned false",
                clear_pending=True,
            )
        return ActionResult(
            success=True,
            action="delete_reminder",
            message_key="deleted",
            payload={"reminder_id": str(rid), "title": title},
            clear_pending=True,
        )

    if intent == VoxIntent.CREATE_REMINDER:
        return await _create_reminder(entities, user_uuid, timezone_name)

    if intent == VoxIntent.UPDATE_REMINDER:
        return await _update_reminder(entities, user_uuid, reminders, timezone_name)

    if intent == VoxIntent.COMPLETE_REMINDER:
        return await _checkin(entities, user_uuid, reminders, mode="complete", timezone_name=timezone_name)

    if intent == VoxIntent.SNOOZE_REMINDER:
        return await _checkin(entities, user_uuid, reminders, mode="snooze", timezone_name=timezone_name)

    if intent == VoxIntent.SKIP_REMINDER:
        return await _checkin(entities, user_uuid, reminders, mode="skip", timezone_name=timezone_name)

    if intent == VoxIntent.DELETE_REMINDER:
        return await _delete_reminder_flow(entities, reminders, user_uuid, timezone_name)

    if intent == VoxIntent.READ_TODAY_MEDICATIONS:
        typed = [
            i
            for i in all_active_reminders(reminders)
            if str(i.get("reminder_type", "")).lower() == "medication"
        ]
        # Only fall back to all today if NO medication reminders exist at all
        use = typed if typed else list(reminders.get("today") or [])
        names = [reminder_title(i) for i in use[:5]]
        return ActionResult(
            success=True,
            action="read_today_medications",
            message_key="read_meds",
            payload={"names": names},
        )

    if intent == VoxIntent.READ_APPOINTMENTS:
        appts = [
            item
            for item in all_active_reminders(reminders)
            if any(
                n in " ".join(
                    str(item.get(k, "")) for k in ("title", "message", "reminder_type", "type")
                ).lower()
                for n in ("appointment", "appt", "visit", "follow")
            )
        ]
        names = [reminder_title(i) for i in (appts or all_active_reminders(reminders))[:3]]
        return ActionResult(
            success=True,
            action="read_appointments",
            message_key="read_appointments",
            payload={"names": names, "found_labeled": bool(appts)},
        )

    if intent == VoxIntent.READ_DOCTOR_SUMMARY:
        wanted_date = str(entities.get("date") or "").strip()
        ambiguous_date = str(entities.get("date_ambiguous") or "").strip()
        logger.info(
            "READ_DOCTOR_SUMMARY: %d summaries available, date=%s ambiguous=%s",
            len(summaries),
            wanted_date or "none",
            "yes" if ambiguous_date else "no",
        )

        # A day was meant but could not be pinned to one. Reading the most
        # recent summary here is the failure this whole branch exists to stop:
        # it answers a question about one day with another day's medicine.
        # A date that arrives in a shape this cannot read is an ambiguity, not
        # an absence. normalize_extraction already promotes these, so in
        # production wanted_date is ISO or empty — but "upstream guarantees it"
        # is exactly the reasoning that let a dropped date read as no date
        # twice already. Falling through from here reaches _latest_summary.
        if wanted_date and not _parse_iso_day(wanted_date):
            logger.info("READ_DOCTOR_SUMMARY: unreadable date reached the executor")
            ambiguous_date = ambiguous_date or wanted_date
            wanted_date = ""

        if ambiguous_date and not wanted_date:
            today_local = datetime.now(_tz(timezone_name)).date()
            asked = int(entities.get(SUMMARY_DATE_ROUNDS_KEY) or 0) + 1

            # However good the question, asking it a third time means it is not
            # working. Say what there is and stop, rather than holding the user
            # in a loop they cannot answer their way out of.
            if asked > MAX_SUMMARY_DATE_ASKS:
                dated = [
                    (row, _summary_local_day(row, timezone_name)) for row in summaries
                ]
                newest = max(
                    (pair for pair in dated if pair[1] is not None),
                    key=lambda pair: pair[1],
                    default=None,
                )
                logger.info(
                    "READ_DOCTOR_SUMMARY: giving up after %d asks, offering=%s",
                    asked - 1,
                    "yes" if newest else "no",
                )
                return ActionResult(
                    success=False,
                    action="read_summary",
                    message_key="summary_date_give_up",
                    payload={
                        "nearest_spoken": (
                            _spoken_day(newest[1], today_local) if newest else ""
                        ),
                        "nearest_title": (
                            str(newest[0].get("title") or "").strip() if newest else ""
                        ),
                    },
                    error="date_not_resolvable_after_retries",
                    # The offer is named, never read. Stop holding the turn.
                    clear_pending=True,
                )

            # When the phrase is a numeric date that could be read two ways,
            # ask about THAT, not "which day was the visit?" — the open
            # question invites the answer that just failed, and a cooperative
            # user is stuck saying the same thing again.
            readings = _order_readings(ambiguous_date, today_local)
            payload: dict[str, Any] = {"phrase": ambiguous_date}
            key = "summary_date_unclear"
            if readings:
                key = "summary_date_which_order"
                payload["options"] = [
                    _spoken_day(reading, today_local) for reading in readings
                ]

            # Hold the question open. Asking which day and then discarding the
            # turn is the mark-done failure again: a question nothing can
            # answer. The next utterance carries a date, and this intent is
            # replayed with it.
            return ActionResult(
                success=False,
                action="read_summary",
                message_key=key,
                payload=payload,
                error="date_not_resolvable",
                pending_intent=VoxIntent.READ_DOCTOR_SUMMARY.value,
                pending_entities={
                    **entities,
                    "date": None,
                    "date_ambiguous": None,
                    SUMMARY_DATE_ROUNDS_KEY: asked,
                },
            )

        if wanted_date:
            target = _parse_iso_day(wanted_date)
            if target is not None:
                today_local = datetime.now(_tz(timezone_name)).date()
                matches = [
                    row
                    for row in summaries
                    if _summary_local_day(row, timezone_name) == target
                ]
                if not matches:
                    days = [
                        (row, _summary_local_day(row, timezone_name))
                        for row in summaries
                    ]
                    dated = [(r, d) for r, d in days if d is not None]
                    nearest = (
                        min(dated, key=lambda pair: abs((pair[1] - target).days))[1]
                        if dated
                        else None
                    )
                    return ActionResult(
                        success=False,
                        action="read_summary",
                        message_key="summary_not_on_date",
                        payload={
                            "date_spoken": _spoken_day(target, today_local),
                            "nearest_spoken": (
                                _spoken_day(nearest, today_local) if nearest else ""
                            ),
                            "in_future": target > today_local,
                            "have_any": bool(summaries),
                        },
                        error="no_summary_on_date",
                    )

                # More than one VISIT that day is a real choice; more than one
                # summary for the same visit is just a regeneration, and the
                # newest of those is the one to read. Rows arrive newest first.
                visits = []
                for row in matches:
                    key = row.get("visit_id")
                    if key not in visits:
                        visits.append(key)
                if len(visits) > 1:
                    seen = []
                    titles = []
                    for row in matches:
                        key = row.get("visit_id")
                        if key in seen:
                            continue
                        seen.append(key)
                        titles.append(
                            str(row.get("title") or row.get("doctor_name") or "").strip()
                            or "an unnamed visit"
                        )
                    return ActionResult(
                        success=False,
                        action="read_summary",
                        message_key="summary_which_visit",
                        payload={
                            "date_spoken": _spoken_day(target, today_local),
                            "titles": titles[:3],
                            "count": len(visits),
                        },
                        error="multiple_visits_on_date",
                    )

                title, summary = _summary_speech(matches[0])
                return ActionResult(
                    success=True,
                    action="read_summary",
                    message_key="read_summary",
                    payload={
                        "title": title,
                        "summary": summary or "",
                        # Named back on every dated read, so a day the user did
                        # not mean is heard rather than believed.
                        "date_spoken": _spoken_day(target, today_local),
                    },
                )

        title, summary = _latest_summary(summaries)
        return ActionResult(
            success=True,
            action="read_summary",
            message_key="read_summary",
            # No [:500] here. That was a second cut stacked on the preview, and
            # three sections do not fit in it — build_summary_speech already
            # bounds the length, and ends on a sentence when it has to.
            payload={"title": title, "summary": summary or ""},
        )

    if intent == VoxIntent.CAREGIVER_BRIEF:
        text = await build_caregiver_brief(user_uuid)
        # Strip legacy disclaimer suffix if present for Stage C personality.
        text = text.replace(
            "This is not medical advice. Take medicines only as prescribed and ask a clinician "
            "if anything feels unclear.",
            "",
        ).strip()
        return ActionResult(
            success=True,
            action="caregiver_brief",
            message_key="caregiver_brief",
            payload={"text": text},
        )

    if intent == VoxIntent.HELP:
        return ActionResult(
            success=True,
            action="help",
            message_key="help",
            payload={},
        )

    if intent == VoxIntent.MEDICAL_ADVICE_REFUSAL:
        return ActionResult(
            success=True,
            action="medical_advice_refusal",
            message_key="medical_advice_refusal",
            payload={},
        )

    if intent == VoxIntent.UNKNOWN:
        # The user said something we could not route. Offer the capability list
        # rather than the full briefing — the briefing is for the no-speech case
        # and reads as a non sequitur after a specific question.
        return ActionResult(
            success=False,
            action="unknown_intent",
            message_key="unknown_intent",
            payload={},
        )

    if intent in PROTECTED_ACTIONS:
        return ActionResult(
            success=False,
            action=intent.value,
            message_key="unsupported_protected",
            error=f"Protected action not implemented: {intent.value}",
        )

    return ActionResult(
        success=False,
        action=intent.value,
        message_key="unknown_intent",
        error="Unhandled intent",
    )


async def _create_reminder(
    entities: dict[str, Any],
    user_uuid: str,
    timezone_name: str,
) -> ActionResult:
    title = (entities.get("title") or entities.get("medication") or "").strip()
    time_hhmm = entities.get("time")
    frequency = entities.get("frequency") or "once"
    reminder_type = entities.get("reminder_type") or "medication"
    # "As needed" names no schedule. Stop before asking for a time we would only
    # use to build a recurrence the user never asked for.
    if entities.get("schedule_unsupported"):
        return ActionResult(
            success=False,
            action="create_reminder",
            message_key="schedule_not_supported",
            payload={"title": title or None},
            clear_pending=True,
        )
    if not title or not time_hhmm:
        missing = []
        if not title:
            missing.append("medication")
        if not time_hhmm:
            missing.append("time")
        if not entities.get("frequency"):
            missing.append("frequency")
        return ActionResult(
            success=False,
            action="create_reminder",
            message_key="clarify_missing_slots",
            pending_intent=VoxIntent.CREATE_REMINDER.value,
            pending_entities=entities,
            missing_slots=missing,
            error="missing_slots",
        )
    interval_hours = entities.get("interval_hours")
    custom_days = entities.get("custom_days") or None
    interval_count = int(entities.get("interval_count") or 1)
    try:
        scheduled = _scheduled_from_parts(
            str(time_hhmm),
            timezone_name,
            date_iso=entities.get("date"),
            day_of_week=entities.get("day_of_week"),
        )
        created = await create_new_reminder(
            ReminderCreate(
                user_id=user_uuid,
                reminder_type=reminder_type,  # type: ignore[arg-type]
                title=title[:80],
                scheduled_time=scheduled,
                timezone=timezone_name or "UTC",
                recurrence=frequency,  # type: ignore[arg-type]
                interval_hours=interval_hours,
                interval_count=interval_count,
                custom_days=custom_days,
                context_data={"source": "remivox_v2"},
            )
        )
        if not created:
            return ActionResult(
                success=False,
                action="create_reminder",
                message_key="create_failed",
                error="create_new_reminder returned None",
                clear_pending=True,
            )
        return ActionResult(
            success=True,
            action="create_reminder",
            message_key="created",
            payload={
                "reminder_id": str(created.get("id")),
                "title": title,
                "time": time_hhmm,
                "frequency": frequency,
                "interval_hours": interval_hours,
                "interval_count": interval_count,
                "custom_days": custom_days,
                "date": entities.get("date"),
                "day_of_week": entities.get("day_of_week"),
                "reminder_type": reminder_type,
            },
            clear_pending=True,
        )
    except Exception as exc:
        logger.warning("Vox v2 create reminder failed: %s", exc)
        return ActionResult(
            success=False,
            action="create_reminder",
            message_key="create_failed",
            error=str(exc),
            clear_pending=True,
        )


async def _update_reminder(
    entities: dict[str, Any],
    user_uuid: str,
    reminders: dict,
    timezone_name: str,
) -> ActionResult:
    hint = str(entities.get("title_hint") or entities.get("medication") or "")
    from services.remivox.intents.extractors import all_active_reminders, reminder_title, score_reminders
    # Fallback: use raw transcript for matching if no hint extracted
    if not hint and entities.get("raw_transcript"):
        hint = entities["raw_transcript"]
    active = all_active_reminders(reminders)
    logger.info(f"UPDATE_MATCH: hint='{hint}', active_titles={[reminder_title(r) for r in active]}")
    scored = score_reminders(reminders, hint)
    logger.info(f"UPDATE_SCORES: {[(s, reminder_title(r)) for s, r in scored[:5]]}")
    resolved = resolve_reminder(
        reminders,
        hint,
        time_hhmm=entities.get("match_time"),
        timezone_name=timezone_name,
    )
    if resolved.time_mismatch:
        return _time_mismatch_result(
            "update_reminder", entities, resolved.time_mismatch, timezone_name
        )
    match, ambiguous = resolved.match, resolved.ambiguous
    if ambiguous:
        return ActionResult(
            success=False,
            action="update_reminder",
            message_key="ambiguous_reminder",
            payload=_candidate_payload(ambiguous[:4], timezone_name),
            pending_intent=VoxIntent.UPDATE_REMINDER.value,
            pending_entities=entities,
            missing_slots=["reminder"],
        )
    if not match:
        return ActionResult(
            success=False,
            action="update_reminder",
            message_key="no_match",
            error="No high-confidence reminder match",
            clear_pending=True,
        )

    rid = str(match.get("id"))
    updates: dict[str, Any] = {}
    if entities.get("time"):
        updates["scheduled_time"] = _scheduled_from_parts(
            str(entities["time"]),
            timezone_name,
            date_iso=entities.get("date"),
            day_of_week=entities.get("day_of_week"),
        )
    if entities.get("frequency"):
        updates["recurrence"] = entities["frequency"]
    if entities.get("interval_hours"):
        updates["interval_hours"] = entities["interval_hours"]
    if entities.get("custom_days"):
        updates["custom_days"] = entities["custom_days"]
    if entities.get("title") and entities.get("title") != hint:
        updates["title"] = entities["title"]

    if not updates:
        return ActionResult(
            success=False,
            action="update_reminder",
            message_key="clarify_missing_slots",
            payload={"title": reminder_title(match)},
            pending_intent=VoxIntent.UPDATE_REMINDER.value,
            pending_entities={**entities, "reminder_id": rid, "title_hint": reminder_title(match)},
            missing_slots=["time"],
        )

    try:
        # `updates` only ever holds fields that actually changed — passing a
        # None explicitly would mark it "set" and null out a NOT NULL column.
        updated = await update_reminder_details(
            rid,
            user_uuid,
            ReminderUpdate(**updates),
        )
        return ActionResult(
            success=bool(updated),
            action="update_reminder",
            message_key="updated" if updated else "update_failed",
            payload={
                "reminder_id": rid,
                "title": reminder_title(match),
                "updates": {
                    k: (v.isoformat() if hasattr(v, "isoformat") else v)
                    for k, v in updates.items()
                },
            },
            clear_pending=True,
            error=None if updated else "update returned empty",
        )
    except Exception as exc:
        logger.warning("Vox v2 update reminder failed: %s", exc)
        return ActionResult(
            success=False,
            action="update_reminder",
            message_key="update_failed",
            error=str(exc),
            clear_pending=True,
        )


async def _checkin(
    entities: dict[str, Any],
    user_uuid: str,
    reminders: dict,
    *,
    mode: str,
    timezone_name: str = "UTC",
) -> ActionResult:
    named = str(entities.get("title_hint") or entities.get("medication") or "").strip()
    hint = named or str(entities.get("raw_transcript") or "").strip() or "medication"
    resolved = resolve_reminder(
        reminders,
        hint,
        time_hhmm=entities.get("match_time"),
        timezone_name=timezone_name,
        min_score=2,
        # The user named a medication: it must actually match one. Without this
        # the "is a medication" and "is due now" bonuses reach min_score on
        # their own and an unmatched name logs the wrong drug as taken.
        require_title_evidence=bool(named),
    )
    if resolved.time_mismatch:
        return _time_mismatch_result(
            f"{mode}_reminder", entities, resolved.time_mismatch, timezone_name
        )
    match, ambiguous = resolved.match, resolved.ambiguous
    if ambiguous:
        # Without pending state the question is unanswerable: the next turn
        # arrives with no memory of what was asked, so "the 8 AM one" is a
        # sentence about nothing. update and delete already did this; check-in
        # asked and then discarded the context needed to hear the reply.
        return ActionResult(
            success=False,
            action=f"{mode}_reminder",
            message_key="ambiguous_reminder",
            payload=_candidate_payload(ambiguous[:4], timezone_name),
            pending_intent=_CHECKIN_INTENT.get(mode, VoxIntent.COMPLETE_REMINDER.value),
            pending_entities=entities,
            missing_slots=["reminder"],
        )
    if not match:
        if named:
            # The user named something we could not match. Logging the wrong
            # medication as taken is worse than asking them to say it again.
            return ActionResult(
                success=False,
                action=f"{mode}_reminder",
                message_key="no_match",
                error="Named reminder did not match",
            )
        # Nothing named — fall back to the first item due today.
        today = list(reminders.get("today") or [])
        match = today[0] if today else None
    if not match:
        return ActionResult(
            success=False,
            action=f"{mode}_reminder",
            message_key="no_match",
            error="No active reminder found",
        )
    rid = str(match.get("id"))
    title = reminder_title(match)
    try:
        if mode == "snooze":
            minutes = int(entities.get("minutes") or DEFAULT_SNOOZE_MINUTES)
            minutes = max(5, min(180, minutes))
            try:
                snoozed = await snooze_reminder(rid, user_uuid, minutes)
            except ReminderNotDueError as not_due:
                # Snoozing a dose that has not come due yet would move it
                # earlier, not later. Say when it is actually due.
                return ActionResult(
                    success=False,
                    action="snooze_reminder",
                    message_key="snooze_not_due_yet",
                    payload={
                        "title": title,
                        "due_local": _local_display(not_due.scheduled_time, match),
                    },
                    error="reminder not due yet",
                )
            if not snoozed:
                # Previously the return value was discarded, so an already-once-
                # snoozed reminder still answered "snoozed" and nothing moved.
                return ActionResult(
                    success=False,
                    action="snooze_reminder",
                    message_key="snooze_unavailable",
                    payload={"title": title},
                    error="snooze refused",
                )
            return ActionResult(
                success=True,
                action="snooze_reminder",
                message_key="snoozed",
                payload={"reminder_id": rid, "title": title, "minutes": minutes},
            )
        if mode == "skip":
            await skip_reminder(rid, user_uuid, ReminderAction(notes="vox_v2_skip"))
            return ActionResult(
                success=True,
                action="skip_reminder",
                message_key="skipped",
                payload={"reminder_id": rid, "title": title},
            )
        try:
            await complete_reminder(rid, user_uuid, ReminderAction(notes="vox_v2_taken"))
        except ReminderNotDueError as not_due:
            return ActionResult(
                success=False,
                action="complete_reminder",
                message_key="not_due_yet",
                payload={
                    "title": title,
                    "due_local": _local_display(not_due.scheduled_time, match),
                },
                error="reminder not due yet",
            )
        return ActionResult(
            success=True,
            action="complete_reminder",
            message_key="completed",
            payload={"reminder_id": rid, "title": title},
        )
    except Exception as exc:
        logger.warning("Vox v2 check-in failed: %s", exc)
        return ActionResult(
            success=False,
            action=f"{mode}_reminder",
            message_key="checkin_failed",
            error=str(exc),
            payload={"title": title},
        )


async def _delete_reminder_flow(
    entities: dict[str, Any],
    reminders: dict,
    user_uuid: str = "",
    timezone_name: str = "UTC",
) -> ActionResult:
    named = str(entities.get("title_hint") or entities.get("medication") or "").strip()
    # Fall back to the whole utterance before giving up on a match, but remember
    # that the user never actually named a reminder.
    hint = named or str(entities.get("raw_transcript") or "").strip()

    def _ask_which() -> ActionResult:
        names = [reminder_title(i) for i in all_active_reminders(reminders)[:4]]
        return ActionResult(
            success=False,
            action="delete_reminder",
            message_key="ask_which_reminder",
            payload={"candidates": names},
            pending_intent=VoxIntent.DELETE_REMINDER.value,
            pending_entities=entities,
            missing_slots=["reminder"],
        )

    if not hint:
        return _ask_which()

    resolved = resolve_reminder(
        reminders,
        hint,
        time_hhmm=entities.get("match_time"),
        timezone_name=timezone_name,
    )
    if resolved.time_mismatch:
        return _time_mismatch_result(
            "delete_reminder", entities, resolved.time_mismatch, timezone_name
        )
    match, ambiguous = resolved.match, resolved.ambiguous
    if ambiguous:
        return ActionResult(
            success=False,
            action="delete_reminder",
            message_key="ambiguous_reminder",
            payload=_candidate_payload(ambiguous[:4], timezone_name),
            pending_intent=VoxIntent.DELETE_REMINDER.value,
            pending_entities=entities,
            missing_slots=["reminder"],
        )
    if not match:
        if not named:
            # "Delete everything" / "remove them all" — never guess, and never
            # bulk delete. Ask which one.
            return _ask_which()
        return ActionResult(
            success=False,
            action="delete_reminder",
            message_key="no_match",
            error="No matching reminder",
            clear_pending=True,
        )

    title = reminder_title(match)
    rid = str(match.get("id"))
    # Delete directly — multi-turn confirm is unreliable with REST session state
    ok = await cancel_reminder(str(rid), user_uuid)
    if not ok:
        return ActionResult(
            success=False,
            action="delete_reminder",
            message_key="delete_failed",
            error="cancel_reminder returned false",
            clear_pending=True,
        )
    return ActionResult(
        success=True,
        action="delete_reminder",
        message_key="deleted",
        payload={"reminder_id": str(rid), "title": title},
        clear_pending=True,
    )

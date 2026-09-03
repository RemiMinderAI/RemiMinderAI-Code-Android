"""
Response Builder (Stage C).

Personality: friendly neighbor helping organize care.
Disclaimer only for MEDICAL_ADVICE_REFUSAL.
"""

from __future__ import annotations

from typing import Any, Optional

from services.remivox.actions.types import ActionResult
from services.remivox.intents.models import IntentResult, VoxIntent
from services.remivox.intents.router import DEFAULT_SNOOZE_MINUTES
from services.remivox.languages import DEFAULT_REMIVOX_LANGUAGE

NO_DISCLAIMER_INTENTS: frozenset[VoxIntent] = frozenset(
    {
        VoxIntent.CREATE_REMINDER,
        VoxIntent.UPDATE_REMINDER,
        VoxIntent.COMPLETE_REMINDER,
        VoxIntent.SNOOZE_REMINDER,
        VoxIntent.SKIP_REMINDER,
        VoxIntent.DELETE_REMINDER,
        VoxIntent.CANCEL_ACTION,
        VoxIntent.CONFIRM_ACTION,
        VoxIntent.READ_TODAY_MEDICATIONS,
        VoxIntent.READ_APPOINTMENTS,
        VoxIntent.READ_DOCTOR_SUMMARY,
        VoxIntent.CAREGIVER_BRIEF,
        VoxIntent.HELP,
        VoxIntent.CLARIFY,
        VoxIntent.UNKNOWN,
    }
)

_SLOT_PROMPTS = {
    "medication": "What should I call this reminder — for example, Metoprolol or Vitamin D?",
    "time": "What time should I set it for?",
    "frequency": "How often — every day, weekly, or just once?",
    "reminder": "Which reminder did you mean?",
    "confirmation": "Just to be sure — should I go ahead?",
}

# Asked when the title slot is empty — phrased for what is being scheduled.
_TITLE_PROMPTS = {
    "medication": "Which medicine should I remind you about?",
    "task": "What should I call this task?",
    "appointment": "What is the appointment for?",
}

_DAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def _speak_time(value: Any) -> str:
    """"20:30" -> "8:30 PM" so the reply reads the way a person would say it."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        hour_s, minute_s = text.split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
    except (ValueError, TypeError):
        return text
    meridiem = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    if minute:
        return f"{display_hour}:{minute:02d} {meridiem}"
    return f"{display_hour} {meridiem}"


def _speak_day(value: Any) -> str:
    text = str(value or "").strip().lower()
    for name in _DAY_NAMES:
        if name.lower() == text:
            return name
    return text.title() if text else ""


def _speak_days(days: Any) -> str:
    if not isinstance(days, (list, tuple)):
        return ""
    return _join_names([_speak_day(day) for day in days])


def _schedule_phrase(payload: dict[str, Any]) -> str:
    """The "when" half of a creation confirmation, in plain speech."""
    frequency = str(payload.get("frequency") or "once")
    when = _speak_time(payload.get("time"))
    at_time = f" at {when}" if when else ""

    if frequency == "interval":
        hours = payload.get("interval_hours")
        starting = f" starting at {when}" if when else ""
        if hours:
            unit = "hour" if int(hours) == 1 else "hours"
            return f"every {int(hours)} {unit}{starting}"
        return f"on a schedule{starting}"

    # interval_count means "every N of the base period". The row is stored
    # correctly, but reading it back as plain "every day" told the user their
    # every-other-day reminder was daily — which reads as a bug in the app and
    # invites them to "correct" a schedule that was already right.
    try:
        every_n = int(payload.get("interval_count") or 1)
    except (TypeError, ValueError):
        every_n = 1
    if every_n < 1:
        every_n = 1

    def _every(period: str) -> str:
        """'day' -> 'every day' / 'every other day' / 'every 3 days'."""
        if every_n == 1:
            return f"every {period}"
        if every_n == 2:
            return f"every other {period}"
        return f"every {every_n} {period}s"

    if frequency == "custom":
        days = _speak_days(payload.get("custom_days"))
        if days:
            if every_n == 2:
                return f"every other {days}{at_time}"
            if every_n > 2:
                return f"every {every_n} weeks on {days}{at_time}"
            return f"every {days}{at_time}"
        return f"on the days you chose{at_time}"

    if frequency == "daily":
        return f"{_every('day')}{at_time}"

    if frequency == "weekly":
        day = _speak_day(payload.get("day_of_week"))
        if day:
            if every_n == 2:
                return f"every other {day}{at_time}"
            if every_n > 2:
                return f"every {every_n} weeks on {day}{at_time}"
            return f"every {day}{at_time}"
        return f"{_every('week')}{at_time}"

    if frequency in {"twice", "twice_daily"}:
        return f"twice a day{at_time}"

    # once
    day = _speak_day(payload.get("day_of_week"))
    if day:
        return f"on {day}{at_time}"
    if payload.get("date"):
        return f"on {payload['date']}{at_time}"
    return at_time.strip() or "once"


# The readings a user has to choose between, phrased as the choice itself.
_FREQUENCY_CHOICE = {
    "biweekly": "Just so I get it right — twice a week, or every two weeks?",
    "bi-weekly": "Just so I get it right — twice a week, or every two weeks?",
    "fortnightly": "Every two weeks — is that right?",
    "bimonthly": "Twice a month, or every two months?",
    "monthly": "Every four weeks, or the same date each month?",
    "every few days": "How many days apart would you like it?",
    "a couple of times a week": "Which days of the week should I remind you?",
    "four times a day": "Every six hours, or four times you choose?",
}


def _candidate_labels(payload: dict[str, Any]) -> list[str]:
    """Name each candidate with the time that separates it from its twin."""
    names = list(payload.get("candidates") or [])
    times = list(payload.get("candidate_times") or [])
    labels: list[str] = []
    for index, name in enumerate(names):
        if not name:
            continue
        spoken = _speak_time(times[index]) if index < len(times) else ""
        labels.append(f"{name} at {spoken}" if spoken else str(name))
    return labels


def _join_names(names: list[str]) -> str:
    clean = [n for n in names if n]
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    if len(clean) == 2:
        return f"{clean[0]} and {clean[1]}"
    return ", ".join(clean[:-1]) + f", and {clean[-1]}"


def build_response(
    *,
    intent_result: IntentResult,
    action_result: Optional[ActionResult] = None,
    reply_language: str = DEFAULT_REMIVOX_LANGUAGE,
) -> dict[str, Any]:
    """Build spoken/text reply: {text, include_disclaimer, language, intent}."""
    intent = intent_result.intent
    include_disclaimer = intent == VoxIntent.MEDICAL_ADVICE_REFUSAL
    action = action_result or ActionResult(
        success=False,
        action="none",
        message_key="empty",
    )
    key = action.message_key
    payload = action.payload or {}

    text = _text_for(key, intent, payload, action)

    if include_disclaimer and "not medical advice" not in text.lower():
        text = (
            f"{text} I'm not a doctor and can't advise on diagnosis, dosing, "
            "stopping medicines, or treatment — please check with your clinician."
        )

    return {
        "text": text.strip(),
        "language": reply_language,
        "include_disclaimer": include_disclaimer,
        "intent": intent.value,
        "action": action.action,
        "success": action.success,
    }


def _text_for(
    key: str,
    intent: VoxIntent,
    payload: dict[str, Any],
    action: ActionResult,
) -> str:
    if key == "clarify_missing_slots":
        missing = payload.get("missing_slots") or action.missing_slots or []
        pending = payload.get("pending_entities") or action.pending_entities or {}
        title = pending.get("title") or pending.get("medication")
        rtype = str(pending.get("reminder_type") or "medication")
        # One question per turn — the validation layer already ordered these.
        slot = missing[0] if missing else None

        if slot in {"title", "medication"}:
            return _TITLE_PROMPTS.get(rtype, _TITLE_PROMPTS["medication"])
        if slot == "time":
            if title:
                return f"What time should I remind you about {title}?"
            return _SLOT_PROMPTS["time"]
        if slot == "date_or_day":
            if title:
                return f"Which day is the {title}?"
            return "Which day should that be?"
        if slot == "frequency":
            if title:
                return f"Should I remind you about {title} every day, or just once?"
            return _SLOT_PROMPTS["frequency"]
        if slot == "frequency_choice":
            phrase = str(pending.get("frequency_ambiguous") or "").strip().lower()
            question = _FREQUENCY_CHOICE.get(phrase)
            if question:
                return question
            spoken = phrase or "that"
            return (
                f"I want to get {spoken} right — how often should this repeat?"
            )
        if slot == "interval_hours":
            return "How many hours apart should I remind you?"
        if slot == "custom_days":
            return "Which days of the week should I remind you?"
        if slot == "new_value":
            if title:
                return f"What should I change about {title} — the time, or how often?"
            return "What should I change about it?"
        if slot == "reminder":
            return _SLOT_PROMPTS["reminder"]
        if slot:
            return _SLOT_PROMPTS.get(slot, "Could you share a bit more detail?")
        return "Could you share a bit more detail?"

    if key == "snooze_unavailable":
        # A reminder may only be snoozed once. This used to answer "snoozed"
        # regardless, so the patient believed a dose had moved when it had not.
        title = payload.get("title") or "That reminder"
        return (
            f"{title} has already been snoozed once, so I have left it where it is."
        )

    if key == "snooze_not_due_yet":
        # Snooze reused the mark-done refusal, so a patient asking to postpone
        # was told how to complete instead. Same guard, different verb.
        title = payload.get("title") or "That reminder"
        due = payload.get("due_local")
        if due:
            return (
                f"{title} isn't due until {due}, "
                "so there's nothing to snooze yet."
            )
        return f"{title} isn't due yet, so there's nothing to snooze."

    if key == "not_due_yet":
        # Deliberately NOT a question. The previous copy asked "did you mean
        # today's?" but nothing held pending state to receive the answer, so a
        # "yes" fell through to UNKNOWN and the user got generic help text after
        # a specific exchange. Never ask what the system cannot act on.
        title = payload.get("title") or "That reminder"
        due = payload.get("due_local")
        if due:
            return (
                f"{title} isn't due until {due}, so I haven't marked it done. "
                "You can mark it done once it's due."
            )
        return f"{title} isn't due yet, so I haven't marked it done."

    if key == "time_mismatch":
        title = payload.get("title") or "that"
        asked = _speak_time(payload.get("asked_time"))
        times = _join_names(
            [_speak_time(t) for t in (payload.get("available_times") or []) if t]
        )
        if asked and times:
            return (
                f"I don't see a {title} reminder at {asked} — you have one at "
                f"{times}. Did you mean that one?"
            )
        if times:
            return f"I don't see one at that time — you have {title} at {times}."
        return f"I don't see a {title} reminder at that time."

    if key == "schedule_not_supported":
        title = payload.get("title")
        subject = f"for {title}" if title else "for that"
        return (
            f"I can't set an as-needed reminder {subject} — I can only remind "
            "you at set times. If you'd like, tell me a time and I'll set one."
        )

    if key == "restart_flow":
        return (
            "Let's start over. Tell me the full reminder — for example: "
            "Metoprolol at 8 PM every day."
        )

    if key == "cancelled":
        return "Okay — I cancelled that. What else can I help with?"

    if key == "confirm_nothing_pending":
        return "I don't have anything waiting for confirmation right now."

    if key == "created":
        title = payload.get("title", "your reminder")
        schedule = _schedule_phrase(payload)
        if schedule:
            return f"All set — I'll remind you about {title} {schedule}."
        return f"All set — I'll remind you about {title}."

    if key == "create_failed":
        return "I couldn't save that reminder just now. Want to try again in a moment?"

    if key == "updated":
        title = payload.get("title", "your reminder")
        return f"Updated — your {title} reminder is all set."

    if key == "update_failed":
        return "I found it, but couldn't update it right now. Want to try again?"

    if key == "ambiguous_reminder":
        # Two rows both called "Metoprolol" make "which one?" unanswerable when
        # only the names are read back. A twice-daily medication produces
        # exactly that, so the time is the question, not a decoration.
        labels = _candidate_labels(payload)
        if labels:
            return f"You have {_join_names(labels)} — which one did you mean?"
        return "I found more than one reminder that could match. Which one should I use?"

    if key == "no_match":
        return "I couldn't find a matching reminder. Want to tell me the name again?"

    if key == "ask_which_reminder":
        names = _join_names(list(payload.get("candidates") or []))
        if names:
            return f"Which reminder should I remove — {names}?"
        return "Which reminder should I remove?"

    if key == "confirm_delete":
        title = payload.get("title", "that reminder")
        return f"Should I delete your {title} reminder?"

    if key == "deleted":
        title = payload.get("title", "that reminder")
        return f"Done — I removed your {title} reminder."

    if key == "delete_failed":
        return "I couldn't remove that reminder just now."

    if key == "completed":
        title = payload.get("title", "that reminder")
        return f"Got it — I logged {title} as taken."

    if key == "snoozed":
        title = payload.get("title", "that reminder")
        minutes = payload.get("minutes", DEFAULT_SNOOZE_MINUTES)
        return f"Okay — I snoozed {title} for {minutes} minutes."

    if key == "skipped":
        title = payload.get("title", "that reminder")
        return f"Okay — I marked {title} as skipped."

    if key == "checkin_failed":
        title = payload.get("title", "that reminder")
        return f"I found {title}, but couldn't update it right now."

    if key == "read_meds":
        names = _join_names(list(payload.get("names") or []))
        if names:
            return f"Here are your medication reminders: {names}."
        return "I don't see medication reminders due right now."

    if key == "read_appointments":
        names = _join_names(list(payload.get("names") or []))
        if payload.get("found_labeled") and names:
            return f"Here are your appointments or visits: {names}."
        if names:
            return f"I don't see a labeled appointment, but coming up next: {names}."
        return "I don't see an upcoming appointment in your reminders."

    if key == "read_summary":
        summary = payload.get("summary") or ""
        title = payload.get("title") or "your latest visit"
        # Only set when the user asked for a particular day. Saying it back is
        # what makes a wrong match audible instead of invisible.
        when = str(payload.get("date_spoken") or "").strip()
        if summary and when:
            return f"From {title} on {when}: {summary}"
        if summary:
            return f"From {title}: {summary}"
        return "I don't see a completed doctor visit summary yet."

    if key == "summary_not_on_date":
        when = str(payload.get("date_spoken") or "that day").strip()
        nearest = str(payload.get("nearest_spoken") or "").strip()
        if payload.get("in_future"):
            return (
                f"{when} hasn't happened yet, so there's no visit summary "
                "from then."
            )
        if not payload.get("have_any"):
            return "I don't see a completed doctor visit summary yet."
        if nearest:
            # Named, never substituted. Reading the nearest day's summary as
            # though it were the one asked for is the whole defect.
            return (
                f"I don't have a visit summary from {when}. "
                f"The nearest one I have is from {nearest}."
            )
        return f"I don't have a visit summary from {when}."

    if key == "summary_date_unclear":
        phrase = str(payload.get("phrase") or "").strip()
        if phrase:
            return (
                f"I'm not sure which day you mean by {phrase}. "
                "Which date was the visit?"
            )
        return "Which date was the visit?"

    if key == "summary_which_visit":
        when = str(payload.get("date_spoken") or "that day").strip()
        titles = [str(t).strip() for t in (payload.get("titles") or []) if str(t).strip()]
        count = payload.get("count") or len(titles)
        if len(titles) >= 2:
            listed = " and ".join([", ".join(titles[:-1]), titles[-1]]) if len(titles) > 2 else " and ".join(titles)
            return f"You have {count} visits from {when} — {listed}. Which one?"
        return f"You have {count} visits from {when}. Which one would you like?"

    if key == "caregiver_brief":
        return payload.get("text") or "I don't see linked patients for a care brief yet."

    if key == "help":
        return (
            "I can set and update reminders, mark medicines taken, snooze or skip, "
            "read today's meds, appointments, or your latest visit summary, "
            "and share a quick caregiver brief. Just talk to me like a neighbor."
        )

    if key == "medical_advice_refusal":
        return (
            "I can't help with diagnosis, changing doses, stopping medicines, "
            "or treatment advice. Please check with your clinician for that."
        )

    if key == "unknown_intent":
        return (
            "I didn't catch that. I can set reminders, read your medications, "
            "summarize your doctor visits, or update an existing reminder. "
            "What would you like?"
        )

    if key == "briefing":
        return payload.get("text") or "How can I help? I can set reminders, read your medications, or summarize your last doctor visit."

    if intent == VoxIntent.UNKNOWN:
        return payload.get("text") or (
            "I'm not sure I caught that. You can ask me to set a reminder, "
            "mark a medicine taken, or read today's meds."
        )

    return "Okay — I'm here if you need help with your reminders."

"""
Entity extractors for RemiVox v2 Intent Router (Stage C).

Deterministic NLP only — no LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from services.remivox.intents.models import VoxRecurrence

_WORD_TO_NUM = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "noon": "12",
    "midnight": "0",
}

_TIME_RE = re.compile(
    r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|am|pm)?\b",
    re.IGNORECASE,
)

# Spoken/word times that _TIME_RE (digit-only) cannot see, e.g.
# "at eight PM", "at eight thirty PM", "from eight to nine". Used to keep the
# time phrase out of stored reminder titles.
_NUM_WORD_PAT = (
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"noon|midnight|fice|nive|fiv"
)
_MIN_WORD_PAT = (
    r"o'?clock|oclock|fifteen|thirty|forty[\s-]?five|forty|fifty|"
    r"twenty[\s-]?(?:one|two|three|four|five|six|seven|eight|nine)?|"
    r"ten|five|\d{2}"
)
_TIME_UNIT_PAT = (
    rf"(?:{_NUM_WORD_PAT}|\d{{1,2}})"
    rf"(?:\s*[:\s]\s*(?:{_MIN_WORD_PAT}))?"
    rf"(?:\s*(?:a\.?\s?m\.?|p\.?\s?m\.?))?"
    rf"(?:\s+(?:in\s+the\s+)?(?:morning|afternoon|evening|night))?"
)
_SPOKEN_TIME_RE = re.compile(
    rf"\b(?:at|by|around|about|from)\s+{_TIME_UNIT_PAT}"
    rf"(?:\s*(?:to|till|until|through|-)\s*{_TIME_UNIT_PAT})?",
    re.IGNORECASE,
)

# Day/relative words that belong to scheduling, never to the title.
_WHEN_WORDS_RE = re.compile(
    r"\b(?:today|tonight|tomorrow|this\s+(?:morning|afternoon|evening|night)|"
    r"right\s+now|now|starting\s+today)\b",
    re.IGNORECASE,
)

_CREATE_RE = re.compile(
    r"(?:set(?:\s*up)?|create|add|schedule|make)\s+(?:another\s+|a\s+)?"
    r"(?:reminder\s+)?(?:for\s+)?"
    r"(?:me\s+to\s+(?:take\s+)?)?(?P<title>.+?)(?:\s+(?:every\s+day|everyday|daily|weekly|once)"
    r"|\s+monday|\s+all\s+days|\s+7\s+days|\s+at\s+\d|\s*$)",
    re.IGNORECASE,
)

_DAILY_PHRASES = (
    "every day",
    "everyday",
    "daily",
    "all days",
    "all day",
    "monday through sunday",
    "monday to sunday",
    "mon through sun",
    "7 days a week",
    "seven days a week",
    "every morning",
    "every evening",
    "every night",
)

_WEEKLY_PHRASES = ("weekly", "every week", "once a week")
_TWICE_PHRASES = ("twice", "two times", "twice a day", "two times a day")


_TENS_AS_HOUR = {
    "twenty": "2", "thirty": "3", "forty": "4", "fifty": "5",
    "sixty": "6", "seventy": "7", "eighty": "8", "ninety": "9",
}

_SPOKEN_MINS = {
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "forty five": "45", "fifty": "50",
}

# Compound minutes: "twenty five" -> "25", "thirty seven" -> "37"
_TENS_MINS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
_ONES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "fice": 5, "nive": 9, "fiv": 5,  # common Pulse mishears
}

_TENS_PAT = "|".join(_TENS_AS_HOUR.keys())
_MINS_PAT = "|".join(sorted(_SPOKEN_MINS.keys(), key=len, reverse=True))
_STT_COMPOUND_RE = re.compile(
    rf"\b({_TENS_PAT})\s+({_MINS_PAT})\b", re.IGNORECASE,
)
_STT_BARE_TENS_RE = re.compile(
    rf"\b({_TENS_PAT})\s*(?=a\.?m\.?|p\.?m\.?|am|pm|in\s+the|o.?clock|\b(?:morning|evening|night)\b)",
    re.IGNORECASE,
)


_ONES_PAT = "one|two|three|four|five|six|seven|eight|nine|fice|nive|fiv"
_TENS_MINS_PAT = "twenty|thirty|forty|fifty"

# "eight twenty five pm" -> "8:25 pm" (single-digit hour + compound minutes)
_STT_HOUR_COMPOUND_MIN_RE = re.compile(
    rf"\b({_ONES_PAT}|\d{{1,2}})\s+({_TENS_MINS_PAT})(?:\s*[-\s]?\s*({_ONES_PAT}))?\s*(?=(a\.?m\.?|p\.?m\.?|am|pm|in\s+the|morning|evening|night|$))",
    re.IGNORECASE,
)


def _fix_stt_compound_times(text: str) -> str:
    """Fix Pulse STT artifacts: 'eighty thirty pm' -> '8:30 pm', 'eight twenty five pm' -> '8:25 pm'."""
    def _repl(m):
        h = _TENS_AS_HOUR[m.group(1).lower()]
        mn = _SPOKEN_MINS[m.group(2).lower()]
        return f"{h}:{mn}"
    result = _STT_COMPOUND_RE.sub(_repl, text)

    # Handle "eight thirty pm" / "eight twenty five pm" BEFORE the bare-tens
    # pass: that pass would otherwise consume the minutes word ("thirty pm" ->
    # "3 pm"), leaving "at 8 3 PM" and an 08:00 misparse.
    def _repl_hour_compound(m):
        h_raw = m.group(1).lower()
        h = _WORD_TO_NUM.get(h_raw) or _ONES.get(h_raw) or h_raw
        tens = _TENS_MINS[m.group(2).lower()]
        ones = _ONES.get((m.group(3) or "").lower(), 0)
        mn = tens + ones
        return f"{h}:{mn:02d} "
    result = _STT_HOUR_COMPOUND_MIN_RE.sub(_repl_hour_compound, result)

    def _repl_bare(m):
        return _TENS_AS_HOUR[m.group(1).lower()]
    result = _STT_BARE_TENS_RE.sub(_repl_bare, result)
    return result


def normalize_number_words(text: str) -> str:
    result = _fix_stt_compound_times(text)
    for word, digit in _WORD_TO_NUM.items():
        result = re.sub(r"\b" + word + r"\b", digit, result, flags=re.IGNORECASE)
    return result


def extract_frequency(text: str) -> Optional[VoxRecurrence]:
    lower = (text or "").lower()
    if any(p in lower for p in _DAILY_PHRASES):
        return VoxRecurrence.DAILY
    if any(p in lower for p in _WEEKLY_PHRASES):
        return VoxRecurrence.WEEKLY
    if any(p in lower for p in _TWICE_PHRASES):
        return VoxRecurrence.TWICE
    if re.search(r"\bonce\b", lower):
        return VoxRecurrence.ONCE
    return None


def extract_time_hhmm(text: str) -> Optional[str]:
    """Extract local time as HH:MM 24-hour."""
    normalized = normalize_number_words(text or "")
    match = _TIME_RE.search(normalized)
    if not match:
        # Bare "8" / "8pm" already covered; try morning/evening cues without digit
        lower = normalized.lower()
        if "noon" in lower:
            return "12:00"
        if "midnight" in lower:
            return "00:00"
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower().replace(".", "")

    if meridiem.startswith("p") and hour < 12:
        hour += 12
    if meridiem.startswith("a") and hour == 12:
        hour = 0
    if not meridiem:
        lower = normalized.lower()
        if "morning" in lower and hour <= 11:
            pass
        elif "evening" in lower or "night" in lower:
            if hour < 12:
                hour += 12
        elif hour <= 7:
            # Med context bare early hours usually mean PM (e.g. "8" → 20:00).
            hour += 12

    hour = hour % 24
    minute = max(0, min(59, minute))
    return f"{hour:02d}:{minute:02d}"


def extract_medication_or_title(text: str) -> Optional[str]:
    prompt = (text or "").strip()
    if not prompt:
        return None

    match = _CREATE_RE.search(prompt)
    title = match.group("title") if match else None

    if not title:
        m2 = re.search(
            r"(?:reminder\s+for|remind\s+me\s+(?:about|to\s+take)|"
            r"mark\s+(?:my\s+)?|take|about|for)\s+(.+?)"
            r"(?:\s+every|\s+daily|\s+at\s+\d|\s+as\s+taken|\s+monday|$)",
            prompt,
            re.IGNORECASE,
        )
        if m2:
            title = m2.group(1)

    if not title:
        m3 = re.search(
            r"(?:set|create|add|schedule)\s+([A-Za-z][A-Za-z0-9 \-]{1,40}?)\s+reminder",
            prompt,
            re.IGNORECASE,
        )
        if m3:
            title = m3.group(1)

    if not title:
        m4 = re.search(
            r"(?:update|change|move|reschedule|edit|delete|remove|mark)\s+"
            r"(?:my\s+)?([A-Za-z][A-Za-z0-9 \-]{1,40}?)\s+reminder",
            prompt,
            re.IGNORECASE,
        )
        if m4:
            title = m4.group(1)

    if not title:
        return None

    title = re.sub(
        r"\b(every\s+day|everyday|daily|weekly|once|twice|another|a|the|"
        r"monday through sunday|all days|7 days a week)\b",
        "",
        title,
        flags=re.I,
    )
    # Strip the time phrase before the digit-only pass so word times
    # ("at eight thirty PM") never end up inside the stored title.
    title = _SPOKEN_TIME_RE.sub("", title)
    title = _TIME_RE.sub("", title)
    title = _WHEN_WORDS_RE.sub("", title)
    title = re.sub(r"\breminders?\b", "", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" .,")
    # Drop leading filler
    title = re.sub(
        r"^(?:reminder\s+for|me\s+to\s+take|to\s+take|about)\s+",
        "",
        title,
        flags=re.I,
    ).strip(" .,")
    # Strip trailing filler words and day names
    title = re.sub(r"\s+(?:on|for|with|at|to|in)\s*$", "", title, flags=re.I).strip(" .,")
    # Strip "on Monday/Tuesday/etc" or "on Thursday"
    title = re.sub(
        r"\s+on\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*$",
        "", title, flags=re.I,
    ).strip(" .,")
    # Strip "on" + date patterns
    title = re.sub(r"\s+on\s*$", "", title, flags=re.I).strip(" .,")
    # Strip leading filler: "my task for", "a", "my"
    title = re.sub(r"^(?:my\s+task\s+(?:for|to)\s+|my\s+|a\s+)", "", title, flags=re.I).strip(" .,")
    title = normalize_spoken_title(title)
    if len(title) < 2:
        return None
    return title[:80]


def reminder_title(item: dict) -> str:
    return (
        item.get("title")
        or item.get("message")
        or item.get("medication_name")
        or item.get("reminder_type")
        or "reminder"
    ).strip()


def _items(bucket: Any) -> list[dict]:
    if not isinstance(bucket, list):
        return []
    return [item for item in bucket if isinstance(item, dict)]


def all_active_reminders(reminders: dict) -> list[dict]:
    return _items(reminders.get("today")) + _items(reminders.get("upcoming"))


# A stated time is the most specific signal a user can give, and the only thing
# separating two reminders that share a title. It must outrank any title match.
TIME_MATCH_BONUS = 20


def reminder_local_hhmm(item: dict, fallback_tz: str = "UTC") -> Optional[str]:
    """
    The reminder's wall-clock time as the patient sees it, "HH:MM".

    scheduled_time is stored as UTC; comparing against it directly would be off
    by the timezone offset, so convert using the reminder's own timezone.
    """
    raw = item.get("scheduled_time")
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(raw, datetime):
        return None
    if raw.tzinfo is None:
        raw = raw.replace(tzinfo=timezone.utc)
    name = str(item.get("timezone") or fallback_tz or "UTC")
    try:
        tz = ZoneInfo(name)
    except Exception:
        tz = timezone.utc
    return raw.astimezone(tz).strftime("%H:%M")


def matches_local_time(item: dict, time_hhmm: str, fallback_tz: str = "UTC") -> bool:
    """Hour and minute only — the user says "8 AM", never a full timestamp."""
    local = reminder_local_hhmm(item, fallback_tz)
    return bool(local) and local == str(time_hhmm).strip()


def score_reminders(
    reminders: dict,
    hint: str,
    *,
    time_hhmm: Optional[str] = None,
    timezone_name: str = "UTC",
) -> list[tuple[int, dict]]:
    """Return (score, item) pairs sorted high→low for active + missed."""
    past = _items(reminders.get("past"))
    missed = [
        item
        for item in past
        if str(item.get("status", "")).strip().lower() == "missed"
        or str(item.get("display_status", "")).strip().lower() == "missed"
    ]
    active = all_active_reminders(reminders) + missed
    lower = (hint or "").lower()
    scored: list[tuple[int, dict]] = []
    for item in active:
        score = title_evidence(item, lower)
        if str(item.get("display_status", "")).lower() == "due now":
            score += 1
        if str(item.get("reminder_type", "")).lower() == "medication":
            score += 1
        # Only when the user actually stated a time — otherwise behaviour is
        # identical to before.
        if time_hhmm and matches_local_time(item, time_hhmm, timezone_name):
            score += TIME_MATCH_BONUS
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


# ── Spoken vitamin names ──────────────────────────────────────────────────
#
# "Vitamin D 3" is heard as two tokens and comes back right. "d3" is spoken as
# one, and the transcript expands it — "d three", or glued as "btwelve". The
# number-word conversion in the legacy time parser runs the other way, so this
# is its own problem, not a setting on that one.
#
# Only combinations that name a real vitamin are rewritten. That matters more
# than it looks: a pattern loose enough to catch a glued "btwelve" also catches
# "bone", which would turn a bone-density reminder into B1. So the glued forms
# are an explicit list with no English words in it, and the spelled-out forms
# require a real separator.
_VITAMIN_FORMS = {"D2", "D3", "B1", "B2", "B3", "B6", "B12", "K1", "K2"}
_VITAMIN_NUM_WORDS = {"one": "1", "two": "2", "three": "3", "six": "6", "twelve": "12"}

# "d3", "D 3", "b-12" — a letter beside a digit is never an English word.
_VITAMIN_DIGIT_RE = re.compile(r"\b([dbk])\s*-?\s*(\d{1,2})\b", re.IGNORECASE)
# "d three", "b twelve" — the separator is required, so "bone" cannot match.
_VITAMIN_WORD_RE = re.compile(
    r"\b([dbk])[\s-]+(one|two|three|six|twelve)\b", re.IGNORECASE
)
# Glued artefacts. "bone" and "done" are deliberately absent; B1 is reachable
# only through the separated forms above.
_VITAMIN_GLUED_RE = re.compile(
    r"\b(b)(twelve|three|six|two)\b|\b(d)(three|two)\b|\b(k)(two)\b",
    re.IGNORECASE,
)


def _vitamin_token(letter: str, number: str) -> Optional[str]:
    combo = f"{letter.upper()}{_VITAMIN_NUM_WORDS.get(number.lower(), number)}"
    return combo if combo in _VITAMIN_FORMS else None


def _sub_pair(match: "re.Match[str]") -> str:
    groups = [g for g in match.groups() if g]
    if len(groups) != 2:
        return match.group(0)
    return _vitamin_token(groups[0], groups[1]) or match.group(0)


def normalize_spoken_title(title: str) -> str:
    """
    Repair vitamin names the transcript spelled out, and capitalise the title.

    Only the first letter is capitalised. Title-casing the whole string would
    give "Pick Up Lab Results", and would flatten medication names that carry
    their own capitals, like CoQ10.
    """
    if not title:
        return title
    for pattern in (_VITAMIN_DIGIT_RE, _VITAMIN_WORD_RE, _VITAMIN_GLUED_RE):
        title = pattern.sub(_sub_pair, title)
    # A bare "D3" is a vitamin nobody calls by letter alone.
    if title.upper() in _VITAMIN_FORMS:
        title = f"Vitamin {title.upper()}"
    if title and title[0].islower():
        title = title[0].upper() + title[1:]
    return title


def title_evidence(item: dict, hint_lower: str) -> int:
    """
    How much of the score comes from the TITLE actually matching the hint.

    Split out from the contextual bonuses because those bonuses are
    title-independent: "is a medication" (+1) and "is due now" (+1) sum to
    min_score on their own, so before this existed any hint whatsoever — a
    mistranscribed drug name, a word from another sentence — resolved to
    whichever medication happened to be due, and a completion was recorded
    against a drug the patient never named.
    """
    title = reminder_title(item).lower()
    score = 0
    # Exact title match gets highest score
    if title.strip() == hint_lower.strip() or title.strip() in hint_lower:
        score += 10
    # Match title tokens in hint
    for token in re.findall(r"[a-z0-9]{3,}", title):
        if token in hint_lower:
            score += 2
    # Also match hint tokens in title (reverse match)
    for token in re.findall(r"[a-z0-9]{3,}", hint_lower):
        if token in title and len(token) >= 4:
            score += 2
    return score


@dataclass
class ReminderMatch:
    """
    Outcome of resolving an utterance to one reminder.

    time_mismatch is distinct from ambiguous on purpose: the user named a time,
    reminders with that title exist, but none is at that time. Falling through
    to a title-only match would act on the wrong dose.
    """

    match: Optional[dict] = None
    ambiguous: list[dict] = field(default_factory=list)
    time_mismatch: list[dict] = field(default_factory=list)


def resolve_reminder(
    reminders: dict,
    hint: str,
    *,
    time_hhmm: Optional[str] = None,
    timezone_name: str = "UTC",
    min_score: int = 2,
    require_title_evidence: bool = False,
) -> ReminderMatch:
    """
    Resolve to a single reminder, using a stated time to break title ties.

    With require_title_evidence, a candidate whose title contributed nothing to
    its score is discarded before ranking. Callers set it when the user NAMED
    something: the contextual bonuses alone reach min_score, so without this a
    named medication that matches nothing still resolves — to the wrong dose.
    Callers who pass no name ("mark done", with one reminder due) leave it off.
    """
    lower = (hint or "").lower()
    if require_title_evidence:
        keep = {
            id(item)
            for item in all_active_reminders(reminders) + _items(reminders.get("past"))
            if title_evidence(item, lower) > 0
        }
        if not keep:
            return ReminderMatch()

    scored = score_reminders(
        reminders, hint, time_hhmm=time_hhmm, timezone_name=timezone_name
    )
    if require_title_evidence:
        scored = [pair for pair in scored if id(pair[1]) in keep]
    if not scored:
        return ReminderMatch()

    if time_hhmm:
        # Candidates the title alone would have accepted, scored without the
        # time bonus so the comparison is like-for-like.
        title_candidates = [
            item
            for score, item in score_reminders(reminders, hint)
            if score >= min_score
            and (not require_title_evidence or title_evidence(item, lower) > 0)
        ]
        # Only meaningful when we actually know the candidates' times. Reminder
        # dicts without a scheduled_time cannot contradict a stated time, and
        # treating them as a mismatch would refuse a perfectly good match.
        comparable = [
            item
            for item in title_candidates
            if reminder_local_hhmm(item, timezone_name) is not None
        ]
        if comparable and not any(
            matches_local_time(item, time_hhmm, timezone_name) for item in comparable
        ):
            return ReminderMatch(time_mismatch=comparable)

    top = scored[0][0]
    if top < min_score:
        return ReminderMatch()
    leaders = [item for score, item in scored if score == top]
    if len(leaders) == 1:
        return ReminderMatch(match=leaders[0])
    return ReminderMatch(ambiguous=leaders)


def find_high_confidence_reminder(
    reminders: dict,
    hint: str,
    *,
    min_score: int = 2,
) -> tuple[Optional[dict], list[dict]]:
    """
    Return (best_match, ambiguous_candidates).

    High confidence: top score >= min_score and uniquely leading.
    Multiple equal high scores → ambiguous list.
    """
    scored = score_reminders(reminders, hint)
    if not scored:
        return None, []
    top = scored[0][0]
    if top < min_score:
        return None, []
    leaders = [item for score, item in scored if score == top]
    if len(leaders) == 1:
        return leaders[0], []
    return None, leaders

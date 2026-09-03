"""
Asking for one day's visit summary must not read another day's.

"Read me my visit summary from August 25th" used to read the newest row
whatever date was asked for. The date was extracted and then thrown away.
"""

import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.remivox.actions.executor import execute_intent  # noqa: E402
from services.remivox.intents.adapter import to_intent_result  # noqa: E402
from services.remivox.intents.models import VoxIntent  # noqa: E402
from services.remivox.response.builder import build_response  # noqa: E402

TZ = "America/Toronto"


def summary(visit_id, when_utc, *, title, text, summary_created=None):
    """One row shaped like get_user_summaries returns."""
    return {
        "summary_id": f"s-{visit_id}-{when_utc}",
        "visit_id": visit_id,
        "title": title,
        "doctor_name": "Dr Example",
        "summary_text": text,
        "summary_preview": text[:160],
        "structured": None,
        # What the screen shows, and what the old code matched on: the moment
        # the TEXT was generated, which a regeneration moves.
        "visit_date": summary_created or when_utc,
        # When the visit happened.
        "visit_created_at": when_utc,
    }


def run(entities, summaries, timezone_name=TZ):
    """
    Drive the turn the way production does: through the adapter.

    Building an IntentResult by hand here is what let the executor's date
    filter pass every test while never receiving a date on a device. The
    adapter sat between them handing this intent an empty dict.
    """
    intent_result = to_intent_result(
        {
            "intent": VoxIntent.READ_DOCTOR_SUMMARY.value,
            "entities": entities,
            "confidence": 1.0,
        },
        language="en",
        raw_transcript="read me my visit summary",
    )
    result = asyncio.run(
        execute_intent(
            intent_result=intent_result,
            user_uuid="u-1",
            reminders={},
            summaries=summaries,
            timezone_name=timezone_name,
        )
    )
    spoken = build_response(
        intent_result=intent_result, action_result=result
    )["text"]
    return result, spoken


AUG25 = summary("v-25", "2026-08-25T14:00:00+00:00",
                title="Cardiology follow-up", text="Blood pressure was reviewed.")
AUG28 = summary("v-28", "2026-08-28T14:00:00+00:00",
                title="Physiotherapy", text="Shoulder exercises were discussed.")
SEP02 = summary("v-02", "2026-09-02T14:00:00+00:00",
                title="Annual check", text="Routine bloods were ordered.")

# Newest first, as the query orders them.
ROWS = [SEP02, AUG28, AUG25]


class ADateWithAMatch(unittest.TestCase):
    def test_it_reads_the_summary_from_that_day(self):
        result, spoken = run({"date": "2026-08-25"}, ROWS)
        self.assertTrue(result.success)
        self.assertIn("Blood pressure", spoken)
        self.assertNotIn("Routine bloods", spoken)

    def test_it_names_the_day_it_read(self):
        _, spoken = run({"date": "2026-08-25"}, ROWS)
        self.assertIn("25 August", spoken)


class ADateWithNoMatch(unittest.TestCase):
    def test_it_reads_nothing(self):
        result, spoken = run({"date": "2026-08-26"}, ROWS)
        self.assertFalse(result.success)
        self.assertNotIn("Blood pressure", spoken)
        self.assertNotIn("Shoulder exercises", spoken)
        self.assertNotIn("Routine bloods", spoken)

    def test_it_says_which_day_it_has_nothing_for(self):
        _, spoken = run({"date": "2026-08-26"}, ROWS)
        self.assertIn("don't have", spoken)
        self.assertIn("26 August", spoken)

    def test_it_may_offer_the_nearest_but_must_name_it(self):
        _, spoken = run({"date": "2026-08-26"}, ROWS)
        self.assertIn("25 August", spoken)


class NoDateAtAll(unittest.TestCase):
    def test_it_reads_the_most_recent(self):
        result, spoken = run({}, ROWS)
        self.assertTrue(result.success)
        self.assertIn("Routine bloods", spoken)

    def test_it_does_not_claim_a_day_that_was_never_asked_for(self):
        _, spoken = run({}, ROWS)
        self.assertNotIn(" on 2 September", spoken)


class TwoSummariesOnOneDay(unittest.TestCase):
    def test_two_visits_the_same_day_asks_which(self):
        morning = summary("v-a", "2026-08-25T13:00:00+00:00",
                          title="Cardiology follow-up", text="Blood pressure was reviewed.")
        afternoon = summary("v-b", "2026-08-25T20:00:00+00:00",
                            title="Dental check", text="A filling was replaced.")
        result, spoken = run({"date": "2026-08-25"}, [afternoon, morning])
        self.assertFalse(result.success)
        self.assertIn("Which one", spoken)
        self.assertIn("Cardiology follow-up", spoken)
        self.assertIn("Dental check", spoken)
        # It asked; it did not read either of them.
        self.assertNotIn("Blood pressure", spoken)
        self.assertNotIn("filling", spoken)

    def test_one_visit_regenerated_reads_the_newest_without_asking(self):
        # Same visit_id: this is the regenerate button, not a second
        # appointment. Eight visits in the table have more than one summary.
        old = summary("v-25", "2026-08-25T14:00:00+00:00",
                      title="Cardiology follow-up", text="An earlier wording.")
        new = summary("v-25", "2026-08-25T14:00:00+00:00",
                      title="Cardiology follow-up", text="The regenerated wording.",
                      summary_created="2026-08-27T09:00:00+00:00")
        result, spoken = run({"date": "2026-08-25"}, [new, old])
        self.assertTrue(result.success)
        self.assertIn("regenerated wording", spoken)


class ADateInTheFuture(unittest.TestCase):
    def test_it_says_the_day_has_not_happened(self):
        ahead = (datetime.now(timezone.utc) + timedelta(days=400)).date()
        result, spoken = run({"date": ahead.isoformat()}, ROWS)
        self.assertFalse(result.success)
        self.assertIn("hasn't happened yet", spoken)
        # and it certainly does not read the latest one instead
        self.assertNotIn("Routine bloods", spoken)

    def test_a_different_year_is_spoken(self):
        # "31 December" alone hides the thing that makes the answer sensible.
        _, spoken = run({"date": "2030-12-31"}, ROWS)
        self.assertIn("2030", spoken)

    def test_the_current_year_is_not_spoken(self):
        _, spoken = run({"date": "2026-08-25"}, ROWS)
        self.assertIn("25 August", spoken)
        self.assertNotIn("2026", spoken)


class AnUnresolvableDate(unittest.TestCase):
    def test_it_asks_instead_of_reading_the_latest(self):
        result, spoken = run({"date": None, "date_ambiguous": "last month"}, ROWS)
        self.assertFalse(result.success)
        self.assertNotIn("Routine bloods", spoken)
        self.assertIn("last month", spoken)
        self.assertIn("Which date", spoken)


class TheDayIsTheVisitsNotTheSummarys(unittest.TestCase):
    """
    visit_date is an alias over summaries_log.created_at and moves when a
    summary is regenerated. One row in the table already disagrees by a
    calendar day in the patient's own timezone, by two days.
    """

    def test_a_regenerated_summary_still_matches_the_visit_day(self):
        regenerated = summary(
            "v-25", "2026-08-25T14:00:00+00:00",
            title="Cardiology follow-up", text="Blood pressure was reviewed.",
            summary_created="2026-09-01T11:00:00+00:00",
        )
        result, spoken = run({"date": "2026-08-25"}, [SEP02, regenerated])
        self.assertTrue(result.success, "the visit day is 25 August, not 1 September")
        self.assertIn("Blood pressure", spoken)

    def test_and_does_not_match_the_day_it_was_regenerated(self):
        regenerated = summary(
            "v-25", "2026-08-25T14:00:00+00:00",
            title="Cardiology follow-up", text="Blood pressure was reviewed.",
            summary_created="2026-09-01T11:00:00+00:00",
        )
        result, _ = run({"date": "2026-09-01"}, [regenerated])
        self.assertFalse(result.success)


class TheDayIsTheUsersNotUTC(unittest.TestCase):
    def test_an_evening_visit_stays_on_its_local_day(self):
        # 25 August, 9 PM in Toronto is already the 26th in UTC.
        evening = summary("v-late", "2026-08-26T01:00:00+00:00",
                          title="Evening clinic", text="Seen after hours.")
        result, spoken = run({"date": "2026-08-25"}, [evening])
        self.assertTrue(result.success)
        self.assertIn("after hours", spoken)


if __name__ == "__main__":
    unittest.main()

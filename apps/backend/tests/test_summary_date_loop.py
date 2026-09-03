"""
The clarification must end.

From a device: "Read me my visit summary on 08/12/2026." -> "Which day was the
visit?" -> "August 12" -> the same question again. The question asked for
exactly what the user had just said, so cooperating reproduced the flag, and
nothing counted the attempts.
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.remivox.actions.executor import (  # noqa: E402
    MAX_SUMMARY_DATE_ASKS,
    execute_intent,
)
from services.remivox.intents.adapter import (  # noqa: E402
    SUMMARY_DATE_ROUNDS_KEY,
    to_intent_result,
)
from services.remivox.intents.models import VoxIntent  # noqa: E402
from services.remivox.response.builder import build_response  # noqa: E402

TZ = "America/Toronto"

ROWS = [
    {
        "summary_id": "s-27", "visit_id": "v-27", "title": "Pain clinic",
        "doctor_name": "Dr Example", "summary_text": "Lyrica was reviewed.",
        "summary_preview": "Lyrica was reviewed.", "structured": None,
        "visit_date": "2026-08-27T14:00:00+00:00",
        "visit_created_at": "2026-08-27T14:00:00+00:00",
    },
]


def turn(entities, *, pending=None, summaries=ROWS):
    """One turn, carrying pending state the way run_care_turn does."""
    intent_result = to_intent_result(
        {
            "intent": VoxIntent.READ_DOCTOR_SUMMARY.value,
            "entities": entities,
            "confidence": 0.95,
        },
        language="en",
        raw_transcript="read me my visit summary",
        pending_intent=(
            VoxIntent.READ_DOCTOR_SUMMARY.value if pending is not None else None
        ),
        pending_entities=pending,
    )
    action = asyncio.run(
        execute_intent(
            intent_result=intent_result, user_uuid="u-1", reminders={},
            summaries=summaries, timezone_name=TZ,
        )
    )
    spoken = build_response(intent_result=intent_result, action_result=action)["text"]
    return action, spoken


class TheQuestionCanBeAnswered(unittest.TestCase):
    def test_an_order_ambiguity_names_both_readings(self):
        _, spoken = turn({"date": "08/12/2026"})
        self.assertIn("12 August", spoken)
        self.assertIn("8 December", spoken)
        # Not the open question that invites the same answer again.
        self.assertNotIn("Which date was the visit?", spoken)

    def test_answering_it_resolves(self):
        action, _ = turn({"date": "08/12/2026"})
        answer, spoken = turn({"date": "2026-08-12"}, pending=action.pending_entities)
        self.assertFalse(answer.success)  # no summary that day, but it RESOLVED
        self.assertEqual(answer.message_key, "summary_not_on_date")
        self.assertIn("12 August", spoken)

    def test_a_vague_phrase_keeps_the_open_question(self):
        _, spoken = turn({"date_ambiguous": "a few weeks ago"})
        self.assertIn("Which date", spoken)


class TheLoopEnds(unittest.TestCase):
    def _ask_again(self, rounds):
        return turn(
            {"date": "08/12/2026"},
            pending={SUMMARY_DATE_ROUNDS_KEY: rounds},
        )

    def test_the_counter_survives_the_replay(self):
        action, _ = turn({"date": "08/12/2026"})
        self.assertEqual(action.pending_entities[SUMMARY_DATE_ROUNDS_KEY], 1)
        again, _ = self._ask_again(1)
        self.assertEqual(again.pending_entities[SUMMARY_DATE_ROUNDS_KEY], 2)

    def test_it_never_asks_a_third_time(self):
        action, spoken = self._ask_again(MAX_SUMMARY_DATE_ASKS)
        self.assertEqual(action.message_key, "summary_date_give_up")
        self.assertNotIn("Did you mean", spoken)
        self.assertNotIn("Which date", spoken)

    def test_giving_up_names_what_it_has(self):
        _, spoken = self._ask_again(MAX_SUMMARY_DATE_ASKS)
        self.assertIn("Pain clinic", spoken)
        self.assertIn("27 August", spoken)

    def test_giving_up_does_not_read_it(self):
        action, spoken = self._ask_again(MAX_SUMMARY_DATE_ASKS)
        self.assertFalse(action.success)
        self.assertNotIn("Lyrica", spoken)

    def test_giving_up_releases_the_turn(self):
        action, _ = self._ask_again(MAX_SUMMARY_DATE_ASKS)
        self.assertTrue(action.clear_pending)
        self.assertIsNone(action.pending_intent)

    def test_with_no_summaries_it_says_so_rather_than_offering(self):
        action, spoken = turn(
            {"date": "08/12/2026"},
            pending={SUMMARY_DATE_ROUNDS_KEY: MAX_SUMMARY_DATE_ASKS},
            summaries=[],
        )
        self.assertEqual(action.message_key, "summary_date_give_up")
        self.assertIn("don't have", spoken)


class TheTwoReadingsAreRight(unittest.TestCase):
    def test_an_unambiguous_numeric_date_is_not_offered_as_a_choice(self):
        # 25 can only be a day, so there is nothing to choose between.
        from datetime import date

        from services.remivox.actions.executor import _order_readings

        self.assertEqual(_order_readings("25/08/2026", date(2026, 9, 3)), [])

    def test_a_same_number_date_is_not_a_choice(self):
        from datetime import date

        from services.remivox.actions.executor import _order_readings

        self.assertEqual(_order_readings("05/05/2026", date(2026, 9, 3)), [])

    def test_words_are_not_an_order_ambiguity(self):
        from datetime import date

        from services.remivox.actions.executor import _order_readings

        self.assertEqual(_order_readings("a few weeks ago", date(2026, 9, 3)), [])


if __name__ == "__main__":
    unittest.main()

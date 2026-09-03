"""
A date the model supplied must never reach the executor as no date at all.

Asked on a device: "Read me my visit summary on 12th August 2026." Vox read a
27 August visit. The date survived the model and the coercion — VOX_NLU logs
the normalized entities and "date" was in them — and was then dropped by the
adapter, which handed READ_DOCTOR_SUMMARY an empty dict. Both-null means "no
day was mentioned", so the executor did the one thing that is right for that
input and wrong for this one.
"""

import asyncio
import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.remivox.actions.executor import execute_intent  # noqa: E402
from services.remivox.intents.adapter import to_intent_result  # noqa: E402
from services.remivox.intents.llm_extractor import (  # noqa: E402
    _coerce_date,
    _date_shape,
    normalize_extraction,
)
from services.remivox.intents.models import VoxIntent  # noqa: E402
from services.remivox.response.builder import build_response  # noqa: E402

TZ = "America/Toronto"

ROWS = [
    {
        "summary_id": "s-27", "visit_id": "v-27", "title": "Pain clinic",
        "doctor_name": "Dr Example",
        "summary_text": "Lyrica was reviewed.", "summary_preview": "Lyrica was reviewed.",
        "structured": None,
        "visit_date": "2026-08-27T14:00:00+00:00",
        "visit_created_at": "2026-08-27T14:00:00+00:00",
    },
]


def turn(model_entities, summaries=ROWS):
    """Model output -> normalize -> adapter -> executor -> spoken reply."""
    normalized = normalize_extraction(
        {
            "intent": VoxIntent.READ_DOCTOR_SUMMARY.value,
            "entities": model_entities,
            "confidence": 0.95,
        },
        timezone_name=TZ,
    )
    intent_result = to_intent_result(
        normalized, language="en", raw_transcript="read me my visit summary"
    )
    action = asyncio.run(
        execute_intent(
            intent_result=intent_result,
            user_uuid="u-1",
            reminders={},
            summaries=summaries,
            timezone_name=TZ,
        )
    )
    return intent_result, action, build_response(
        intent_result=intent_result, action_result=action
    )["text"]


class TheAdapterCarriesTheDate(unittest.TestCase):
    def test_a_resolved_date_reaches_the_executor(self):
        intent_result, _, _ = turn({"date": "2026-08-12"})
        self.assertEqual(intent_result.entities.get("date"), "2026-08-12")

    def test_an_ambiguity_reaches_the_executor(self):
        intent_result, _, _ = turn({"date_ambiguous": "last month"})
        self.assertEqual(intent_result.entities.get("date_ambiguous"), "last month")


class TheReportedTurn(unittest.TestCase):
    """"Read me my visit summary on 12th August 2026" must not read 27 August."""

    def test_it_does_not_read_the_most_recent_summary(self):
        _, action, spoken = turn({"date": "2026-08-12"})
        self.assertFalse(action.success)
        self.assertNotIn("Lyrica", spoken)

    def test_it_says_it_has_nothing_from_that_day(self):
        _, _, spoken = turn({"date": "2026-08-12"})
        self.assertIn("12 August", spoken)
        self.assertIn("don't have", spoken)


class ARejectedDateIsNotAnAbsence(unittest.TestCase):
    def test_a_slash_date_becomes_an_ambiguity(self):
        # 08/12/2026 is 12 August to most of the world and 8 December in the
        # United States. Guessing reads a different month's summary.
        result = normalize_extraction(
            {"intent": "READ_DOCTOR_SUMMARY", "entities": {"date": "08/12/2026"},
             "confidence": 1.0},
            timezone_name=TZ,
        )
        self.assertIsNone(result["entities"]["date"])
        self.assertEqual(result["entities"]["date_ambiguous"], "08/12/2026")

    def test_an_impossible_iso_date_becomes_an_ambiguity(self):
        result = normalize_extraction(
            {"intent": "READ_DOCTOR_SUMMARY", "entities": {"date": "2026-13-45"},
             "confidence": 1.0},
            timezone_name=TZ,
        )
        self.assertIsNone(result["entities"]["date"])
        self.assertEqual(result["entities"]["date_ambiguous"], "2026-13-45")

    def test_the_models_own_phrase_wins_over_the_raw_value(self):
        result = normalize_extraction(
            {"intent": "READ_DOCTOR_SUMMARY",
             "entities": {"date": "08/12/2026", "date_ambiguous": "a while back"},
             "confidence": 1.0},
            timezone_name=TZ,
        )
        self.assertEqual(result["entities"]["date_ambiguous"], "a while back")

    def test_no_date_at_all_stays_no_date(self):
        result = normalize_extraction(
            {"intent": "READ_DOCTOR_SUMMARY", "entities": {}, "confidence": 1.0},
            timezone_name=TZ,
        )
        self.assertIsNone(result["entities"]["date"])
        self.assertIsNone(result["entities"]["date_ambiguous"])

    def test_a_good_date_is_untouched(self):
        result = normalize_extraction(
            {"intent": "READ_DOCTOR_SUMMARY", "entities": {"date": "2026-08-12"},
             "confidence": 1.0},
            timezone_name=TZ,
        )
        self.assertEqual(result["entities"]["date"], "2026-08-12")
        self.assertIsNone(result["entities"]["date_ambiguous"])

    def test_a_rejected_date_asks_instead_of_reading_the_latest(self):
        _, action, spoken = turn({"date": "08/12/2026"})
        self.assertFalse(action.success)
        self.assertNotIn("Lyrica", spoken)
        self.assertIn("08/12/2026", spoken)
        self.assertIn("Which date", spoken)


class TheRejectionIsRecorded(unittest.TestCase):
    def test_it_logs_the_shape_and_never_the_value(self):
        with self.assertLogs(
            "services.remivox.intents.llm_extractor", level="INFO"
        ) as captured:
            _coerce_date("08/12/2026", today=__import__("datetime").date(2026, 9, 3))
        line = "\n".join(captured.output)
        self.assertIn("date rejected", line)
        self.assertIn("numeric-separated", line)
        # A date identifies a person. The pattern is enough to diagnose with.
        self.assertNotIn("08/12/2026", line)

    def test_shapes_are_distinguishable(self):
        self.assertEqual(_date_shape("08/12/2026"), "numeric-separated")
        self.assertEqual(_date_shape("2026-13-45"), "iso-invalid")
        self.assertEqual(_date_shape("20260812"), "bare-number")
        self.assertEqual(_date_shape("next fortnight"), "words")

    def test_a_good_date_logs_nothing(self):
        logger = logging.getLogger("services.remivox.intents.llm_extractor")
        with self.assertLogs(logger, level="INFO") as captured:
            logger.info("marker")
            _coerce_date("2026-08-12", today=__import__("datetime").date(2026, 9, 3))
        self.assertEqual(len(captured.output), 1)


if __name__ == "__main__":
    unittest.main()

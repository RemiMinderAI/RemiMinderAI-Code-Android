import unittest

from services.remivox.summary_speech import (
    MEDICATIONS_LEAD_IN,
    NEXT_STEPS_LEAD_IN,
    SPOKEN_SUMMARY_CHAR_LIMIT,
    build_summary_speech,
)

# Shaped like a real row: 112 of 129 production summaries use action_items.
REAL = {
    "summary": "The patient presented for a follow-up regarding dizziness and "
               "persistent headaches, which occur about three to four times a "
               "week, mostly in the afternoon. The doctor adjusted the dosage.",
    "medications": ["Betaseron", "Magnesium Glycinate", "CQ10", "Vitamin D3"],
    "action_items": [
        "Increase Betaseron dosage to 32 mg.",
        "Schedule a follow-up appointment in four weeks.",
    ],
}


class ThreeSectionsTests(unittest.TestCase):
    def test_all_three_sections_are_read(self):
        spoken = build_summary_speech("", REAL)
        self.assertIn("dizziness", spoken)
        self.assertIn(MEDICATIONS_LEAD_IN, spoken)
        self.assertIn("Betaseron", spoken)
        self.assertIn(NEXT_STEPS_LEAD_IN, spoken)
        self.assertIn("four weeks", spoken)

    def test_sections_are_in_the_order_the_app_shows_them(self):
        spoken = build_summary_speech("", REAL)
        self.assertLess(spoken.index("dizziness"), spoken.index(MEDICATIONS_LEAD_IN))
        self.assertLess(spoken.index(MEDICATIONS_LEAD_IN), spoken.index(NEXT_STEPS_LEAD_IN))

    def test_an_empty_section_is_skipped_not_announced(self):
        """Saying "Your medications are:" and then nothing is worse than silence."""
        spoken = build_summary_speech("", {"summary": "Overview only."})
        self.assertNotIn(MEDICATIONS_LEAD_IN, spoken)
        self.assertNotIn(NEXT_STEPS_LEAD_IN, spoken)
        self.assertEqual(spoken, "Overview only.")

    def test_both_stored_action_shapes_are_read(self):
        """action_items in 112 rows, actions in 17 — both are in production."""
        for key in ("action_items", "actions", "decisions"):
            spoken = build_summary_speech("", {"summary": "S.", key: ["Do the thing"]})
            self.assertIn("Do the thing", spoken, f"{key} was ignored")

    def test_falls_back_to_prose_when_there_is_no_structured_data(self):
        self.assertEqual(build_summary_speech("Plain prose.", None), "Plain prose.")

    def test_nothing_at_all_is_empty_not_a_dangling_lead_in(self):
        self.assertEqual(build_summary_speech("", {}), "")


class TruncationTests(unittest.TestCase):
    def test_it_never_ends_mid_word(self):
        """
        The defect being fixed: a 160-char cut left the reply on "The d".
        If this ever has to shorten, it ends on a sentence instead.
        """
        long_summary = ("This is a complete sentence about the patient. " * 200)
        spoken = build_summary_speech(long_summary, None)
        self.assertLessEqual(len(spoken), SPOKEN_SUMMARY_CHAR_LIMIT)
        self.assertTrue(spoken.rstrip().endswith("."), f"ended: {spoken[-40:]!r}")

    def test_a_realistic_summary_is_not_shortened_at_all(self):
        spoken = build_summary_speech("", REAL)
        self.assertLess(len(spoken), SPOKEN_SUMMARY_CHAR_LIMIT)
        self.assertTrue(spoken.endswith("four weeks."))

    def test_the_old_160_preview_is_no_longer_the_ceiling(self):
        spoken = build_summary_speech("", REAL)
        self.assertGreater(len(spoken), 160)


class WiringTests(unittest.TestCase):
    def test_the_executor_no_longer_caps_the_spoken_summary(self):
        with open("services/remivox/actions/executor.py", encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("summary[:500]", source)
        self.assertIn("build_summary_speech", source)

    def test_the_query_returns_the_full_text_and_the_sections(self):
        """
        The upstream cap: get_user_summaries used to return only a 160-char
        preview, so no downstream fix could have recovered the rest.
        """
        with open("services/db_service.py", encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn('"summary_text": summary_text', source)
        self.assertIn('"structured": structured_data_json', source)
        self.assertIn("s.structured_data_json", source)
        # The preview stays — the Flutter list model reads it.
        self.assertIn('"summary_preview": summary_preview', source)

    def test_preview_is_last_in_the_fallback_chain(self):
        with open("services/remivox/actions/executor.py", encoding="utf-8") as fh:
            source = fh.read()
        start = source.index("def _latest_summary(")
        body = source[start:source.index("async def execute_intent", start)]
        # Assert on the lookups themselves — the docstring above them names
        # summary_preview first precisely to explain why it comes last.
        self.assertLess(
            body.index('latest.get("summary_text")'),
            body.index('latest.get("summary_preview")'),
            "the 160-char preview must never win over the full text",
        )


if __name__ == "__main__":
    unittest.main()

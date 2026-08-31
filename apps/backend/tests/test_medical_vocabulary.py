import base64
import os
import unittest
from unittest.mock import patch

from services.medical_vocabulary import (
    DEFAULT_MEDICAL_TERMS,
    MAX_PHRASE_CHARS,
    MAX_PHRASES,
    deepgram_keyterms,
    patient_terms,
    stt_phrases,
)

# The transcriptions Paramita's testing actually produced. Betahistine treats
# vertigo; betamethasone is a corticosteroid and Betaseron is an MS therapy.
OBSERVED = ["Betahistine", "magnesium glycinate", "CoQ10"]


class VocabularyTests(unittest.TestCase):
    def test_the_terms_that_failed_are_covered(self):
        lowered = {t.lower() for t in DEFAULT_MEDICAL_TERMS}
        for term in OBSERVED:
            self.assertIn(term.lower(), lowered, f"{term} missing from the list")

    def test_patient_medications_come_first(self):
        rows = [{"title": "Betahistine", "reminder_type": "medication"}]
        mine, defaults = stt_phrases(rows)
        self.assertEqual(mine, ["Betahistine"])
        # Not repeated in the lower-boost layer.
        self.assertNotIn("betahistine", {d.lower() for d in defaults})

    def test_tasks_are_not_boosted_as_drug_names(self):
        rows = [
            {"title": "Metoprolol", "reminder_type": "medication"},
            {"title": "pick up lab results", "reminder_type": "task"},
            {"title": "Doctor appointment", "reminder_type": "appointment"},
        ]
        self.assertEqual(patient_terms(rows), ["Metoprolol"])

    def test_accepts_both_reminder_shapes(self):
        bucketed = {
            "today": [{"title": "Lyrica", "reminder_type": "medication"}],
            "upcoming": [{"title": "Metoprolol", "reminder_type": "medication"}],
            "past": [],
        }
        self.assertEqual(sorted(patient_terms(bucketed)), ["Lyrica", "Metoprolol"])

    def test_the_cap_never_drops_the_patients_own_medications(self):
        rows = [
            {"title": f"Drugzz{i}", "reminder_type": "medication"}
            for i in range(MAX_PHRASES + 40)
        ]
        mine, defaults = stt_phrases(rows)
        self.assertEqual(len(mine), MAX_PHRASES)
        self.assertEqual(defaults, [], "generic terms yield to the patient's own")

    def test_unusable_phrases_are_dropped(self):
        rows = [
            {"title": "x", "reminder_type": "medication"},          # too short
            {"title": "y" * (MAX_PHRASE_CHARS + 1), "reminder_type": "medication"},
            {"title": "  ", "reminder_type": "medication"},
            {"title": None, "reminder_type": "medication"},
        ]
        self.assertEqual(patient_terms(rows), [])

    def test_no_input_still_returns_the_default_list(self):
        mine, defaults = stt_phrases(None)
        self.assertEqual(mine, [])
        self.assertGreater(len(defaults), 50)


class GoogleSttWiringTests(unittest.TestCase):
    def test_both_configs_get_the_hints(self):
        """
        The fallback runs whenever medical_conversation is rejected — which
        happens in production — and it drops the enhanced medical model, so it
        needs the vocabulary more than the primary does, not less.
        """
        with open("services/media/audio_pipeline.py", encoding="utf-8") as fh:
            source = fh.read()
        primary = source.index('model="medical_conversation"')
        fallback = source.index('model="latest_long"')
        before_primary = source.rindex("speech_contexts=speech_contexts", 0, primary)
        before_fallback = source.rindex("speech_contexts=speech_contexts", 0, fallback)
        self.assertLess(before_primary, primary)
        self.assertNotEqual(before_primary, before_fallback)

    def test_contexts_are_built_with_separate_boosts(self):
        from services.media.audio_pipeline import _build_speech_contexts
        from services.medical_vocabulary import DEFAULT_TERM_BOOST, PATIENT_TERM_BOOST

        rows = [{"title": "Betahistine", "reminder_type": "medication"}]
        contexts = _build_speech_contexts(rows)
        self.assertEqual(len(contexts), 2)
        self.assertEqual(contexts[0].boost, PATIENT_TERM_BOOST)
        self.assertEqual(list(contexts[0].phrases), ["Betahistine"])
        self.assertEqual(contexts[1].boost, DEFAULT_TERM_BOOST)
        # v1 caps boost at 20; above that it biases without improving.
        self.assertLessEqual(PATIENT_TERM_BOOST, 20)
        self.assertGreater(PATIENT_TERM_BOOST, DEFAULT_TERM_BOOST)


class DeepgramWiringTests(unittest.TestCase):
    def _url_for(self, keyterms, env="Metoprolol:Pregabalin"):
        os.environ["DEEPGRAM_API_KEY"] = "test-key"
        os.environ["DEEPGRAM_KEYWORDS"] = env
        import services.remivox.voice as voice

        captured = {}

        class FakeResp:
            status_code = 200

            def json(self):
                return {"results": {"channels": [{"alternatives": [{"transcript": "x"}]}]}}

        def fake_post(url, headers=None, data=None, timeout=None):
            captured["url"] = url
            return FakeResp()

        with patch.object(voice.requests, "post", fake_post):
            voice.transcribe_deepgram(
                base64.b64encode(b"audio").decode(),
                language="en",
                keyterms=keyterms,
            )
        return captured["url"]

    def _terms(self, url):
        return [p.split("=", 1)[1] for p in url.split("&") if p.startswith("keyterm=")]

    def test_multi_word_terms_are_encoded(self):
        """
        Unencoded, "magnesium glycinate" breaks the query string and the term is
        silently lost — which is how a supplement stays mis-transcribed even
        after being added to the list.
        """
        url = self._url_for(["magnesium glycinate"])
        self.assertNotIn(" ", url)
        self.assertIn("magnesium%20glycinate", url)

    def test_patient_terms_lead(self):
        terms = self._terms(self._url_for(["Betahistine"]))
        self.assertEqual(terms[0], "Betahistine")

    def test_deduped_across_the_layers(self):
        terms = self._terms(self._url_for(["Metoprolol"]))
        self.assertEqual(terms.count("Metoprolol"), 1)

    def test_existing_behaviour_survives(self):
        terms = self._terms(self._url_for(None))
        self.assertIn("Pregabalin", terms, "the configured list still applies")
        self.assertIn("snooze", terms, "Vox action words still applied")

    def test_keyterm_list_stays_short(self):
        rows = [
            {"title": f"Drugzz{i}", "reminder_type": "medication"} for i in range(500)
        ]
        self.assertLessEqual(len(deepgram_keyterms(rows)), 60)


if __name__ == "__main__":
    unittest.main()

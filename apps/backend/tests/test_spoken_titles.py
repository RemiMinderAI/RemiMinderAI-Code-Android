"""Vitamin names the transcript spelled out, and title capitalisation.

"Vitamin D 3" arrives as two tokens and survives. "d3" is spoken as one, and
comes back as "d three" or the glued "btwelve". The fix has to be narrow: a
pattern loose enough to catch "btwelve" also catches "bone".
"""

import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.remivox.intents.extractors import normalize_spoken_title  # noqa: E402
from services.remivox.intents import llm_extractor  # noqa: E402
from services.remivox.legacy.handle_prompt import _normalize_number_words  # noqa: E402


class VitaminForms(unittest.TestCase):
    def test_glued_and_spelled_forms_become_the_real_name(self):
        for spoken, expected in [
            ("d3", "Vitamin D3"),
            ("b12", "Vitamin B12"),
            ("d three", "Vitamin D3"),
            ("b twelve", "Vitamin B12"),
            ("btwelve", "Vitamin B12"),
            ("b 1", "Vitamin B1"),
            ("k2", "Vitamin K2"),
            ("d2", "Vitamin D2"),
            ("B6", "Vitamin B6"),
        ]:
            self.assertEqual(normalize_spoken_title(spoken), expected, spoken)

    def test_the_form_that_already_worked_still_does(self):
        self.assertEqual(normalize_spoken_title("Vitamin D 3"), "Vitamin D3")
        self.assertEqual(normalize_spoken_title("Vitamin D3"), "Vitamin D3")
        self.assertEqual(normalize_spoken_title("vitamin b12"), "Vitamin B12")

    def test_vitamin_is_not_prefixed_twice(self):
        self.assertEqual(
            normalize_spoken_title("Vitamin D 3").lower().count("vitamin"), 1
        )

    def test_only_real_vitamin_combinations_are_rewritten(self):
        # D9 and B7 name nothing, so they are left exactly as spoken.
        self.assertEqual(normalize_spoken_title("d9"), "D9")
        self.assertEqual(normalize_spoken_title("b7 supplement"), "B7 supplement")


class NoFalsePositives(unittest.TestCase):
    """The reason the glued list is explicit rather than a pattern."""

    def test_english_words_that_look_like_glued_vitamins_survive(self):
        for phrase in [
            "bone density scan",
            "take my bone medicine",
            "done with physio",
            "bone broth",
        ]:
            self.assertEqual(normalize_spoken_title(phrase), phrase[0].upper() + phrase[1:], phrase)

    def test_bone_never_becomes_b1(self):
        self.assertNotIn("B1", normalize_spoken_title("bone density scan"))


class Capitalisation(unittest.TestCase):
    def test_first_letter_only(self):
        self.assertEqual(normalize_spoken_title("metoprolol"), "Metoprolol")
        self.assertEqual(
            normalize_spoken_title("pick up lab results"), "Pick up lab results"
        )

    def test_it_is_not_title_case(self):
        self.assertNotEqual(
            normalize_spoken_title("pick up lab results"), "Pick Up Lab Results"
        )

    def test_internal_capitals_survive(self):
        self.assertEqual(normalize_spoken_title("CoQ10"), "CoQ10")
        self.assertEqual(normalize_spoken_title("NovoLog"), "NovoLog")

    def test_empty_input_is_safe(self):
        self.assertEqual(normalize_spoken_title(""), "")


class ReachedAtRuntime(unittest.TestCase):
    """The failure this codebase keeps producing: written, tested, never called."""

    def test_the_llm_title_path_calls_it(self):
        src = inspect.getsource(llm_extractor._clean_title)
        self.assertIn("normalize_spoken_title(title)", src)

    def test_the_regex_fallback_calls_it(self):
        from services.remivox.intents import extractors
        src = inspect.getsource(extractors.extract_medication_or_title)
        self.assertIn("normalize_spoken_title(title)", src)

    def test_the_fallback_no_longer_capitalises_separately(self):
        from services.remivox.intents import extractors
        src = inspect.getsource(extractors.extract_medication_or_title)
        self.assertNotIn("title[0].upper() + title[1:]", src)


class LegacyTimeParsingUntouched(unittest.TestCase):
    """_normalize_number_words runs the other way and must not change."""

    def test_number_words_still_become_digits(self):
        self.assertEqual(_normalize_number_words("at eight PM"), "at 8 PM")
        self.assertEqual(_normalize_number_words("nine AM"), "9 AM")
        self.assertEqual(_normalize_number_words("eight thirty PM"), "8 thirty PM")

    def test_it_does_not_import_the_new_helper(self):
        from services.remivox.legacy import handle_prompt
        self.assertFalse(hasattr(handle_prompt, "normalize_spoken_title"))


if __name__ == "__main__":
    unittest.main()

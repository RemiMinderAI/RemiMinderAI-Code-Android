"""Scanned-docs and lab-results endpoints.

The bug these guard against is a silent one: a scanned-docs list that filters or
renders against the 220-char preview returns an empty list rather than an error,
which is exactly how the Firestore-era version survived unnoticed.
"""

import inspect
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import route.visit_summary as vs  # noqa: E402


def _source_of(fn):
    return inspect.getsource(fn)


class IsoUtcTests(unittest.TestCase):
    """image_metadata['uploaded_at'] was written naive; clients read it as local."""

    def test_naive_string_is_stamped_utc(self):
        out = vs._iso_utc("2026-08-31T16:08:45.123456")
        self.assertEqual(out, "2026-08-31T16:08:45.123456+00:00")

    def test_existing_offset_is_preserved(self):
        out = vs._iso_utc("2026-08-31T16:08:45+05:30")
        self.assertTrue(out.endswith("+05:30"), out)

    def test_trailing_z_is_accepted(self):
        # datetime.fromisoformat rejects 'Z' before 3.11.
        out = vs._iso_utc("2026-08-31T16:08:45Z")
        self.assertEqual(out, "2026-08-31T16:08:45+00:00")

    def test_datetime_instances_pass_through(self):
        aware = datetime(2026, 8, 31, 16, 8, 45, tzinfo=timezone.utc)
        self.assertEqual(vs._iso_utc(aware), aware.isoformat())

    def test_empty_and_garbage_are_none(self):
        for bad in (None, "", "not a date", 12345):
            self.assertIsNone(vs._iso_utc(bad), bad)

    def test_naive_value_is_not_left_ambiguous(self):
        # The whole point: the parsed result must be an absolute instant.
        parsed = datetime.fromisoformat(vs._iso_utc("2026-08-31T16:08:45"))
        self.assertIsNotNone(parsed.tzinfo)


class UploadWritesAwareTimestamp(unittest.TestCase):
    def test_upload_stamps_utc(self):
        src = _source_of(vs.upload_image_for_visit)
        self.assertIn('datetime.now(timezone.utc).isoformat()', src)
        self.assertNotIn('"uploaded_at": datetime.now().isoformat()', src)


class ScannedDocsResponse(unittest.TestCase):
    def test_both_list_endpoints_normalise_uploaded_at(self):
        for fn in (vs.get_user_scanned_docs, vs.get_caregiver_patient_scanned_docs):
            src = _source_of(fn)
            self.assertIn('"uploaded_at": _iso_utc(uploaded_at)', src, fn.__name__)

    def test_preview_is_capped(self):
        src = _source_of(vs.get_user_scanned_docs)
        self.assertIn('ocr_text[:220]', src)


class LabResultsFiltersServerSide(unittest.TestCase):
    """The change most likely to look finished and not be."""

    def test_filter_runs_against_full_ocr_text_not_a_preview(self):
        src = _source_of(vs.get_user_lab_results)
        where = src[src.index("WHERE"):src.index("ORDER BY")]
        self.assertIn("vt.ocr_text", where)
        # A preview-based filter would be the silent-empty bug all over again.
        self.assertNotIn("220", where)
        self.assertNotIn("preview", where.lower())

    def test_keywords_are_matched_in_sql(self):
        src = _source_of(vs.get_user_lab_results)
        for kw in ("cholesterol", "glucose", "hemoglobin"):
            self.assertIn(kw, src)

    def test_response_carries_the_scanned_doc_shape(self):
        src = _source_of(vs.get_user_lab_results)
        # One card widget renders both tabs, so both must expose these.
        for field in ('"ocr_preview"', '"uploaded_at"', '"visit_date"', '"visit_id"'):
            self.assertIn(field, src, field)

    def test_original_field_name_is_retained(self):
        self.assertIn('"result_preview"', _source_of(vs.get_user_lab_results))

    def test_metadata_is_selected(self):
        src = _source_of(vs.get_user_lab_results)
        select = src[src.index("SELECT"):src.index("FROM visit_transcripts")]
        self.assertIn("vt.image_metadata", select)


class OcrTextDetailEndpoint(unittest.TestCase):
    def test_route_is_registered_as_get(self):
        paths = {r.path: r for r in vs.router.routes if hasattr(r, "methods")}
        self.assertIn("/api/visits/{visit_id}/ocr-text", paths)
        self.assertEqual({"GET"}, set(paths["/api/visits/{visit_id}/ocr-text"].methods))

    def test_it_does_not_shadow_visit_metadata(self):
        paths = [r.path for r in vs.router.routes if hasattr(r, "methods")]
        self.assertIn("/api/visits/{visit_id}", paths)

    def test_full_text_is_returned_uncapped(self):
        src = _source_of(vs.get_visit_ocr_text)
        self.assertIn('"ocr_text": ocr_text', src)
        self.assertNotIn("[:220]", src)

    def test_visit_metadata_route_stays_lean(self):
        # Extending it would push OCR text onto every visit-details open.
        self.assertNotIn("ocr_text", _source_of(vs.get_visit_metadata))

    def test_query_is_scoped_to_the_caller(self):
        src = _source_of(vs.get_visit_ocr_text)
        self.assertIn("user_id::text = :user_id", src)
        self.assertIn("assert_patient_access", src)

    def test_ocr_text_is_never_logged(self):
        src = _source_of(vs.get_visit_ocr_text)
        for line in src.splitlines():
            if "logger." in line:
                self.assertNotIn("ocr_text,", line)
        self.assertIn("len(ocr_text)", src)


if __name__ == "__main__":
    unittest.main()

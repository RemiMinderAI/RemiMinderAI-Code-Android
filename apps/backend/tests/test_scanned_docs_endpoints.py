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


class DeleteScannedDoc(unittest.TestCase):
    """A visit_transcripts row carries the audio transcript alongside the scan."""

    def test_route_is_registered_as_delete(self):
        paths = {r.path: r for r in vs.router.routes if hasattr(r, "methods")}
        self.assertIn("/api/scanned-docs/{visit_id}", paths)
        self.assertEqual(
            {"DELETE"}, set(paths["/api/scanned-docs/{visit_id}"].methods)
        )

    def test_the_row_is_cleared_not_deleted(self):
        src = _source_of(vs.delete_scanned_doc)
        self.assertIn("UPDATE visit_transcripts", src)
        # Dropping the row would destroy the recording of a visit the patient
        # only wanted a scan removed from.
        self.assertNotIn("DELETE FROM visit_transcripts", src)
        self.assertNotIn("DELETE FROM visits", src)

    def test_audio_columns_survive(self):
        src = _source_of(vs.delete_scanned_doc)
        after = src[src.index("UPDATE visit_transcripts"):]
        update = after[:after.index("WHERE")]
        self.assertTrue(update.strip(), "failed to isolate the SET clause")
        for cleared in ("image_url", "image_metadata", "ocr_text", "ocr_status"):
            self.assertIn(cleared, update, cleared)
        for kept in ("transcript_text", "audio_url"):
            self.assertNotIn(kept, update, kept)

    def test_ocr_status_is_not_set_null(self):
        # The column is NOT NULL with default 'pending'.
        src = _source_of(vs.delete_scanned_doc)
        self.assertIn("ocr_status = 'pending'", src)
        self.assertNotIn("ocr_status = NULL", src)

    def test_the_object_goes_before_the_row(self):
        src = _source_of(vs.delete_scanned_doc)
        # Clearing the row first would strand the image with no way to retry.
        self.assertLess(
            src.index("delete_image(blob_name)"),
            src.index("UPDATE visit_transcripts"),
        )

    def test_it_is_scoped_to_the_owner(self):
        src = _source_of(vs.delete_scanned_doc)
        self.assertIn("user_id::text = :user_id", src)
        self.assertIn('assert_patient_access(user_uuid, user_uuid, "full")', src)

    def test_missing_document_is_404(self):
        src = _source_of(vs.delete_scanned_doc)
        self.assertIn("status_code=404", src)
        self.assertIn("image_url IS NOT NULL", src)

    def test_response_matches_what_the_client_expects(self):
        # backend_api_service.deleteScannedDoc treats anything else as a failure.
        self.assertIn('{"status": "ok"}', _source_of(vs.delete_scanned_doc))

    def test_ocr_text_is_never_logged(self):
        src = _source_of(vs.delete_scanned_doc)
        self.assertNotIn("ocr_text,", src[src.index("logger.info(\"Scanned doc deleted"):])


class DeleteImageBehaviour(unittest.TestCase):
    """delete_image must be safe to retry."""

    def setUp(self):
        import services.gcs_service as gcs
        self.gcs = gcs
        self._real_client = gcs._get_storage_client
        os.environ["GCS_BUCKET_NAME"] = "test-bucket"

    def tearDown(self):
        self.gcs._get_storage_client = self._real_client

    def _install(self, on_delete):
        calls = []

        class Blob:
            def delete(self_inner):
                calls.append("delete")
                if on_delete:
                    raise on_delete

        class Bucket:
            def blob(self_inner, name):
                calls.append(name)
                return Blob()

        class Client:
            def bucket(self_inner, name):
                return Bucket()

        self.gcs._get_storage_client = lambda: Client()
        return calls

    def test_successful_delete_returns_true(self):
        calls = self._install(None)
        self.assertTrue(self.gcs.delete_image("images/v/abc.jpg"))
        self.assertIn("images/v/abc.jpg", calls)

    def test_already_absent_is_success(self):
        from google.api_core.exceptions import NotFound
        self._install(NotFound("gone"))
        # A retried delete must not fail on the second attempt.
        self.assertTrue(self.gcs.delete_image("images/v/abc.jpg"))

    def test_other_failures_propagate(self):
        from google.api_core.exceptions import Forbidden
        self._install(Forbidden("nope"))
        # Reporting success here would leave health data in the bucket.
        with self.assertRaises(Forbidden):
            self.gcs.delete_image("images/v/abc.jpg")

    def test_missing_bucket_config_raises(self):
        os.environ.pop("GCS_BUCKET_NAME", None)
        try:
            with self.assertRaises(RuntimeError):
                self.gcs.delete_image("images/v/abc.jpg")
        finally:
            os.environ["GCS_BUCKET_NAME"] = "test-bucket"


if __name__ == "__main__":
    unittest.main()

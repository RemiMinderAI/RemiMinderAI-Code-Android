"""The OCR backfill cron.

run_ocr_for_visit had one caller — the client, once, after upload. A request
that never landed left the row at 'pending' forever: 18 of 32 scanned documents
were in that state, the oldest since January. No preview in Scanned Docs, and
nothing for Lab Results to match on.
"""

import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import route.internal_cron as cron  # noqa: E402
import services.media.image_pipeline as pipeline  # noqa: E402


class RouteIsRegistered(unittest.TestCase):
    def test_it_exists_as_post(self):
        paths = {r.path: r for r in cron.router.routes if hasattr(r, "methods")}
        self.assertIn("/api/internal/process-pending-ocr", paths)
        self.assertEqual(
            {"POST"}, set(paths["/api/internal/process-pending-ocr"].methods)
        )

    def test_it_is_behind_the_same_secret_as_the_other_jobs(self):
        src = inspect.getsource(cron.process_pending_ocr)
        self.assertIn("_require_internal_secret(x_internal_secret)", src)


class ItSelectsTheRightRows(unittest.TestCase):
    def test_only_pending_documents_that_have_an_image(self):
        src = inspect.getsource(cron.process_pending_ocr)
        self.assertIn("ocr_status = 'pending'", src)
        self.assertIn("image_url IS NOT NULL", src)

    def test_oldest_first_and_bounded(self):
        # The backlog is months deep; a run must not try to drain it at once.
        src = inspect.getsource(cron.process_pending_ocr)
        self.assertIn("ORDER BY created_at ASC", src)
        self.assertIn("LIMIT :limit", src)
        self.assertIn("OCR_CRON_MAX_DOCS", src)

    def test_it_does_not_touch_completed_or_failed_rows(self):
        src = inspect.getsource(cron.process_pending_ocr)
        where = src[src.index("WHERE"):src.index("ORDER BY")]
        for state in ("completed", "failed", "processing"):
            self.assertNotIn(state, where, state)


class ClaimingIsLeftToThePipeline(unittest.TestCase):
    """Which is what makes this safe at max-instances=3."""

    def test_the_cron_does_not_write_status_itself(self):
        src = inspect.getsource(cron.process_pending_ocr)
        # A second instance must be a no-op, not a duplicate Vision call — and
        # setting 'processing' here would make the pipeline return early.
        self.assertNotIn("SET ocr_status", src)
        self.assertNotIn("UPDATE visit_transcripts", src)

    def test_the_pipeline_still_claims_and_short_circuits(self):
        src = inspect.getsource(pipeline.run_ocr_for_visit)
        self.assertIn("SET ocr_status = 'processing'", src)
        self.assertIn("ocr_status == 'processing'", src)
        self.assertIn("ocr_status == 'completed'", src)


class FailuresLeaveThePendingSet(unittest.TestCase):
    """Otherwise an unreadable document is retried every minute forever."""

    def test_the_pipeline_records_failure(self):
        src = inspect.getsource(pipeline.run_ocr_for_visit)
        self.assertIn("ocr_status = 'failed'", src)
        self.assertIn("ocr_error", src)

    def test_the_cron_swallows_the_error_but_counts_it(self):
        src = inspect.getsource(cron.process_pending_ocr)
        self.assertIn("except Exception", src)
        self.assertIn("failed += 1", src)
        self.assertIn('"failed": failed', src)


class NothingSensitiveIsLogged(unittest.TestCase):
    def test_no_ocr_text_in_the_logs(self):
        src = inspect.getsource(cron.process_pending_ocr)
        for line in src.splitlines():
            if "logger." in line:
                self.assertNotIn("ocr_text", line)
                self.assertNotIn("text", line.replace("type(exc).__name__", ""))


if __name__ == "__main__":
    unittest.main()

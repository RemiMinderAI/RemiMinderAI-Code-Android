import logging
import os
import asyncio
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status

from services.alert_service import process_missed_reminder_caregiver_alerts
from services.reminder_due_push_service import process_reminder_due_push_notifications

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal", tags=["internal-cron"])


def _require_internal_secret(x_internal_secret: Optional[str]) -> None:
    expected = (os.getenv("INTERNAL_CRON_SECRET") or "").strip()
    if not expected or (x_internal_secret or "").strip() != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.post("/caregiver-missed-reminder-alerts", status_code=status.HTTP_200_OK)
async def caregiver_missed_reminder_alerts(
    x_internal_secret: Optional[str] = Header(None, alias="X-Internal-Secret"),
):
    """
    Cloud Scheduler (or cron) should POST here with header X-Internal-Secret
    matching env INTERNAL_CRON_SECRET. Creates caregiver alerts for overdue pending reminders.
    """
    _require_internal_secret(x_internal_secret)
    n = await process_missed_reminder_caregiver_alerts()
    logger.info("caregiver_missed_reminder_alerts processed=%s", n)
    return {"alerts_created": n}


@router.post("/reminder-due-fcm", status_code=status.HTTP_200_OK)
async def reminder_due_fcm_push(
    x_internal_secret: Optional[str] = Header(None, alias="X-Internal-Secret"),
):
    """
    Cloud Scheduler should POST every minute with X-Internal-Secret matching INTERNAL_CRON_SECRET.
    Sends due-window FCM to the patient and each care-team caregiver (idempotent per occurrence).
    """
    _require_internal_secret(x_internal_secret)
    stats = await process_reminder_due_push_notifications()
    return stats


@router.post("/process-pending-ocr", status_code=status.HTTP_200_OK)
async def process_pending_ocr(
    x_internal_secret: Optional[str] = Header(None, alias="X-Internal-Secret"),
):
    """
    Cloud Scheduler endpoint for scanned images whose OCR never ran.

    run_ocr_for_visit had exactly one caller: the client, once, straight after
    upload. If that request failed, the network dropped, or the app was closed
    before it landed, the row stayed at ocr_status='pending' for good — 18 of 32
    scanned documents were sitting like that, the oldest since January. Nothing
    retried and nothing backfilled, so those documents had no text: no preview
    in Scanned Docs, and nothing for Lab Results to match on.

    Claiming is left to the pipeline, which sets 'processing' before it starts
    and returns early when it sees 'processing' or 'completed'. That makes a
    second instance picking up the same row a no-op rather than a duplicate
    Vision call — which matters now the service runs at max-instances=3.

    Failures need no bookkeeping here either: the pipeline writes 'failed' and
    ocr_error before re-raising, so a document that cannot be read leaves the
    pending set instead of being retried every minute forever.
    """
    _require_internal_secret(x_internal_secret)

    from services.cloud_sql_engine import get_cloud_sql_engine
    from services.media.image_pipeline import run_ocr_for_visit
    from sqlalchemy import text as sa_text

    max_docs = int(os.getenv("OCR_CRON_MAX_DOCS", "3"))
    engine = get_cloud_sql_engine()

    with engine.connect() as conn:
        rows = conn.execute(
            sa_text(
                """
                SELECT visit_id::text AS visit_id
                FROM visit_transcripts
                WHERE ocr_status = 'pending'
                  AND image_url IS NOT NULL
                ORDER BY created_at ASC
                LIMIT :limit
                """
            ),
            {"limit": max_docs},
        ).fetchall()

    visit_ids = [
        str(r._mapping["visit_id"] if hasattr(r, "_mapping") else r[0]) for r in rows
    ]

    processed = 0
    failed = 0
    for visit_id in visit_ids:
        try:
            await run_ocr_for_visit(visit_id)
            processed += 1
        except Exception as exc:
            # The pipeline has already recorded 'failed' and the reason.
            failed += 1
            logger.warning(
                "Backfill OCR failed visit=%s: %s", visit_id, type(exc).__name__
            )

    if visit_ids:
        logger.info(
            "OCR backfill run: claimed=%d processed=%d failed=%d",
            len(visit_ids), processed, failed,
        )
    return {"claimed": len(visit_ids), "processed": processed, "failed": failed}


@router.post("/process-pending-stt-jobs", status_code=status.HTTP_200_OK)
async def process_pending_stt_jobs(
    x_internal_secret: Optional[str] = Header(None, alias="X-Internal-Secret"),
):
    """
    Cloud Scheduler endpoint for processing queued STT + AI summary jobs.

    This replaces the always-on worker loop when running that worker would waste credits.
    Each invocation claims a small number of pending jobs and runs them synchronously inside
    the backend Cloud Run request.
    """
    _require_internal_secret(x_internal_secret)

    from jobs.stt_job import run_stt_job
    from services.jobs_service import claim_job, mark_done, mark_failed

    max_jobs = int(os.getenv("STT_CRON_MAX_JOBS", "1"))
    processed = 0
    failed = 0
    errors: list[str] = []

    for _ in range(max_jobs):
        job = claim_job("STT_JOB")
        if not job:
            break
        try:
            await asyncio.to_thread(run_stt_job, job["payload"])
            mark_done(job["id"])
            processed += 1
        except Exception as exc:
            attempts = int(job.get("attempts", 0))
            mark_failed(job["id"], str(exc), attempts)
            failed += 1
            errors.append(str(exc))
            logger.exception("Scheduled STT job %s failed: %s", job["id"], exc)

    return {
        "processed": processed,
        "failed": failed,
        "errors": errors[:3],
    }

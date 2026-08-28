"""
Server-side due-time FCM for patients and caregivers (driven by internal cron).
"""
import logging
from typing import Dict

from services.alert_service import list_patient_caregivers_for_alerts
from services.db_reminders import (
    caregiver_alert_exists_for_context,
    create_caregiver_alert,
    get_patient_info,
    get_reminders_due_in_window,
    get_patient_fcm_tokens,
    mark_reminder_due_push_delivered,
    try_claim_reminder_due_push,
    PUSH_RETRY_WINDOW_MINUTES,
)
from services.reminder_service import send_fcm_to_tokens
from services.notification_service import send_caregiver_alert_email

logger = logging.getLogger(__name__)


async def process_reminder_due_push_notifications(
    # The lookback has to cover the retry window, not just "recently due".
    # At 2.0 a reminder dropped out of this query three minutes after it was
    # due, so a failing send got four attempts instead of ten and the
    # abandonment branch was never reached — the dose was lost silently, which
    # is precisely what the retry logic exists to prevent. Verified against a
    # real reminder on 2026-08-28: attempts 3 and 4 logged, then
    # reminders_examined dropped to 0 and MEDICATION REMINDER NOT DELIVERED
    # never fired.
    # Deliberately WIDER than the retry window. Abandonment needs at least one
    # cron pass where the reminder is still fetched but already past its
    # window — if the two boundaries coincide, the row drops out of the query
    # at the same instant it becomes abandonable and the branch never runs.
    # Verified: with both at 10 minutes, attempts stopped at 12 and
    # abandoned_at stayed null.
    past_minutes: float = float(PUSH_RETRY_WINDOW_MINUTES) + 5.0,
    future_minutes: float = 2.0,
) -> Dict[str, int]:
    """
    Find reminders due in the time window, claim idempotently, send FCM to patient + caregivers.
    """
    stats = {"reminders_examined": 0, "patient_pushes": 0, "caregiver_pushes": 0,
             "retries": 0, "abandoned": 0}
    try:
        rows = await get_reminders_due_in_window(
            past_minutes=past_minutes,
            future_minutes=future_minutes,
            limit=300,
        )
    except Exception as e:
        logger.error("get_reminders_due_in_window failed: %s", e)
        return stats

    for r in rows:
        stats["reminders_examined"] += 1
        rid = str(r.get("id") or "")
        patient_uid = str(r.get("user_id") or "")
        if not rid or not patient_uid:
            continue

        st = r.get("scheduled_time")
        try:
            claim = await try_claim_reminder_due_push(rid, st)
        except Exception as e:
            logger.warning("try_claim_reminder_due_push %s: %s", rid, e)
            continue
        if not claim.claimed:
            if claim.newly_abandoned:
                # The line to grep for when someone says "I stopped getting
                # reminders". Not "a send failed" — a dose alert never arrived,
                # and by design will not arrive now: delivering it this late
                # risks a second dose.
                stats["abandoned"] += 1
                logger.error(
                    "MEDICATION REMINDER NOT DELIVERED: reminder_id=%s "
                    "scheduled=%s attempts=%d — giving up after %d minutes, "
                    "the patient was not notified",
                    rid,
                    st.isoformat() if hasattr(st, "isoformat") else st,
                    claim.attempts,
                    PUSH_RETRY_WINDOW_MINUTES,
                )
            continue
        if claim.attempts > 1:
            stats["retries"] += 1

        title = (r.get("title") or "Reminder").strip() or "Reminder"
        body = (r.get("message") or "").strip() or f"Time for: {title}"

        ptokens = await get_patient_fcm_tokens(patient_uid)
        delivered = 0
        if ptokens:
            delivered = await send_fcm_to_tokens(
                fcm_tokens=ptokens,
                title=title,
                body=body,
                data={
                    "type": "reminder_due",
                    "reminder_id": rid,
                    "patient_id": patient_uid,
                    "deep_link": f"/patient/reminder/{rid}",
                },
            )
            stats["patient_pushes"] += delivered

        # Only a real delivery closes the row. Anything else leaves it
        # retryable until the window runs out, which is what turns a transient
        # APNs failure back into a delivered reminder instead of a silent skip.
        if delivered:
            await mark_reminder_due_push_delivered(rid, st)
        else:
            logger.warning(
                "reminder_due push not delivered yet: reminder_id=%s "
                "attempt=%d devices=%d — will retry inside the %d-minute window",
                rid,
                claim.attempts,
                len(ptokens),
                PUSH_RETRY_WINDOW_MINUTES,
            )

        try:
            caregivers = await list_patient_caregivers_for_alerts(patient_uid)
        except Exception as e:
            logger.warning("list_patient_caregivers_for_alerts %s: %s", patient_uid, e)
            caregivers = []

        cg_body = f"Patient reminder due now: {title}"
        patient = await get_patient_info(patient_uid)
        patient_name = (patient or {}).get("full_name") or "Your patient"
        ctx = f"reminder_due:{rid}:{st.isoformat() if hasattr(st, 'isoformat') else str(st)}"
        for cg in caregivers:
            cg_id = cg.get("caregiver_id")
            if not cg_id:
                continue
            cg_id = str(cg_id)
            if await caregiver_alert_exists_for_context(
                patient_uid, "reminder_due", ctx, cg_id
            ):
                continue
            row = await create_caregiver_alert(
                caregiver_id=cg_id,
                user_id=patient_uid,
                reminder_id=rid,
                alert_type="reminder_due",
                message=cg_body,
                context_id=ctx,
            )
            if not row:
                continue

            cgtokens = await get_patient_fcm_tokens(str(cg_id))
            if not cgtokens:
                await send_caregiver_alert_email(
                    to_email=(cg.get("email") or "").strip(),
                    patient_name=patient_name,
                    alert_message=cg_body,
                    alert_id=rid,
                    caregiver_firebase_uid=cg_id,
                )
                continue

            cg_delivered = await send_fcm_to_tokens(
                fcm_tokens=cgtokens,
                title="Patient reminder due",
                body=cg_body,
                data={
                    "type": "reminder_due",
                    "reminder_id": rid,
                    "patient_id": patient_uid,
                    "deep_link": "/caregiver/reminders-timeline",
                },
            )
            stats["caregiver_pushes"] += cg_delivered
            await send_caregiver_alert_email(
                to_email=(cg.get("email") or "").strip(),
                patient_name=patient_name,
                alert_message=cg_body,
                alert_id=rid,
                caregiver_firebase_uid=cg_id,
            )

    logger.info("reminder_due_push stats=%s", stats)
    return stats

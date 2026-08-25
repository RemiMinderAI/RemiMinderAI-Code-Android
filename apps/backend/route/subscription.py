"""
Subscription sync and RevenueCat webhook.

Two paths, deliberately both:

  * the client tells us after a purchase or restore — fast, but only knows what
    happens inside the app
  * RevenueCat tells us via webhook — the only way we learn about a
    cancellation, a refund, a failed renewal or a lapse, all of which happen
    when the app is not running

Never log receipts or subscriber payloads: event types, plan transitions and
booleans only.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy import text

from services.auth_gateway import get_current_user_jwt
from services.cloud_sql_engine import get_cloud_sql_engine
from services.revenuecat import (
    RevenueCatNotConfigured,
    allow_unverified,
    premium_after_event,
    secret_key,
    verify_premium,
    webhook_authorized,
)
from services.subscription_service import sync_revenuecat_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/subscription", tags=["subscription"])


class RevenueCatSyncRequest(BaseModel):
    premium: bool = False
    app_user_id: Optional[str] = None
    product_id: Optional[str] = None
    source: Optional[str] = None


@router.post("/revenuecat/sync")
async def revenuecat_sync(
    request: RevenueCatSyncRequest,
    current_user: dict = Depends(get_current_user_jwt),
):
    """
    Apply a purchase or restore the client has just completed.

    The client's `premium` field is treated as a HINT, never as authority: it is
    re-checked against RevenueCat before any upgrade is written. Without a
    configured secret key the upgrade is refused rather than trusted, unless
    REVENUECAT_ALLOW_UNVERIFIED is deliberately set.
    """
    firebase_uid = current_user.get("sub")
    if not firebase_uid:
        return {"ok": False, "reason": "unauthenticated"}

    app_user_id = (request.app_user_id or firebase_uid or "").strip()
    verified: Optional[bool] = None
    try:
        verified = verify_premium(app_user_id)
    except RevenueCatNotConfigured:
        if not allow_unverified():
            logger.error(
                "Subscription sync refused: REVENUECAT_SECRET_KEY missing and "
                "REVENUECAT_ALLOW_UNVERIFIED not set (claimed_premium=%s)",
                request.premium,
            )
            return {
                "ok": False,
                "reason": "verification_unavailable",
                "message": "Subscription could not be verified. Please try again shortly.",
            }
        logger.error(
            "UNVERIFIED subscription sync accepted on client claim "
            "(claimed_premium=%s) — REVENUECAT_ALLOW_UNVERIFIED is on",
            request.premium,
        )
        verified = bool(request.premium)
    except Exception as exc:
        logger.warning(
            "RevenueCat verification failed (%s); leaving plan unchanged",
            type(exc).__name__,
        )
        return {
            "ok": False,
            "reason": "verification_failed",
            "message": "Subscription could not be verified. Please try again shortly.",
        }

    status = await sync_revenuecat_status(
        firebase_uid,
        premium=bool(verified),
        app_user_id=app_user_id or None,
        product_id=request.product_id,
        source=request.source or "client_sync",
    )
    logger.info(
        "Subscription synced: claimed=%s verified=%s plan=%s",
        request.premium,
        verified,
        status.get("plan"),
    )
    return {"ok": True, "verified": bool(verified), "plan": status.get("plan")}


def _already_processed(
    event_id: str,
    event_type: Optional[str] = None,
    app_user_id: Optional[str] = None,
) -> bool:
    """
    Record the event id, returning True if it was already seen.

    The primary key does the work: a duplicate delivery conflicts and inserts
    nothing, so the handler can skip without a read-then-write race.
    """
    if not event_id:
        return False
    engine = get_cloud_sql_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO revenuecat_webhook_events (event_id, event_type, app_user_id)
                VALUES (:event_id, :event_type, :app_user_id)
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
            """),
            {
                "event_id": event_id,
                "event_type": event_type,
                "app_user_id": app_user_id,
            },
        )
        return result.fetchone() is None



@router.post("/revenuecat/webhook")
async def revenuecat_webhook(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    """
    Receive subscription lifecycle events from RevenueCat.

    Always returns 200 for anything it has authenticated, even a malformed body:
    RevenueCat retries non-2xx indefinitely, so a poison message that 500s would
    be redelivered forever. Only an auth failure is a non-200.
    """
    if not webhook_authorized(authorization):
        logger.warning("Rejected RevenueCat webhook: missing or bad Authorization")
        return _json_401()

    try:
        payload = await request.json()
    except Exception:
        logger.warning("RevenueCat webhook body was not JSON — acknowledging, no action")
        return {"ok": True, "handled": False, "reason": "malformed_body"}

    event = (payload or {}).get("event") or {}
    if not isinstance(event, dict):
        logger.warning("RevenueCat webhook had no event object — acknowledging")
        return {"ok": True, "handled": False, "reason": "malformed_event"}

    event_id = str(event.get("id") or "").strip()
    event_type = str(event.get("type") or "").strip().upper()
    app_user_id = str(event.get("app_user_id") or "").strip()

    try:
        if event_id and _already_processed(event_id, event_type or None, app_user_id or None):
            logger.info("RevenueCat event already processed type=%s — skipping", event_type)
            return {"ok": True, "handled": False, "reason": "duplicate"}
    except Exception as exc:
        logger.warning("Idempotency check failed (%s) — processing anyway", type(exc).__name__)

    transition = premium_after_event(event_type, event)
    if transition is None:
        logger.info("RevenueCat event type=%s recorded, no plan change", event_type or "?")
        return {"ok": True, "handled": True, "plan_changed": False}

    if not app_user_id:
        logger.warning("RevenueCat event type=%s had no app_user_id", event_type)
        return {"ok": True, "handled": False, "reason": "no_app_user_id"}

    try:
        # app_user_id is configured as the Firebase uid on the client.
        status = await sync_revenuecat_status(
            app_user_id,
            premium=transition,
            app_user_id=app_user_id,
            product_id=str(event.get("product_id") or "") or None,
            source="revenuecat_webhook",
        )
        logger.info(
            "RevenueCat webhook applied type=%s premium=%s plan=%s",
            event_type,
            transition,
            status.get("plan"),
        )
        return {"ok": True, "handled": True, "plan_changed": True}
    except Exception as exc:
        # Still 200: retrying will not fix an unknown user or a bad payload, and
        # RevenueCat would redeliver forever.
        logger.warning(
            "RevenueCat webhook could not be applied type=%s (%s)",
            event_type,
            type(exc).__name__,
        )
        return {"ok": True, "handled": False, "reason": "apply_failed"}


def _json_401():
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=401, content={"ok": False, "reason": "unauthorized"})

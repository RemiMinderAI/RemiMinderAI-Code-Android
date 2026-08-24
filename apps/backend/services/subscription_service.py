import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import text

from services.cloud_sql_engine import get_cloud_sql_engine
from services.cache_service import get, set, invalidate
from services.vox_quota import (
    PREMIUM_VOX_LIMIT,
    REASON_PREMIUM_EXHAUSTED,
    REASON_TRIAL_EXHAUSTED,
    REASON_TRIAL_EXPIRED,
    REASON_VOX_DISABLED,
    TRIAL_VOX_LIMIT,
    current_period_start,
    period_resets_at,
    premium_remaining,
    trial_remaining,
)

logger = logging.getLogger(__name__)

PLAN_TRIAL = "TRIAL"
PLAN_FREE = "FREE"
PLAN_PREMIUM = "PREMIUM"
PLAN_EXPIRED = "EXPIRED"

TRIAL_DAYS = 14
TRIAL_SUMMARY_LIMIT = 3
FREE_SUMMARY_LIMIT = 2
# Deprecated: Vox no longer uses a separate interaction quota (full trial access).
TRIAL_REMIVOX_LIMIT = 0
FREE_CAREGIVER_LIMIT = 1
TRIAL_CAREGIVER_LIMIT = 1
PREMIUM_CAREGIVER_LIMIT = 5


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_plan(raw: Optional[str]) -> str:
    value = (raw or PLAN_TRIAL).strip().upper()
    return value if value in {PLAN_TRIAL, PLAN_FREE, PLAN_PREMIUM, PLAN_EXPIRED} else PLAN_FREE


def _is_test_premium_uid(firebase_uid: Optional[str]) -> bool:
    if not firebase_uid:
        return False
    raw = os.getenv("REMIMINDER_TEST_PREMIUM_UIDS", "")
    allowed = {item.strip() for item in raw.split(",") if item.strip()}
    return firebase_uid in allowed


def _serialize_status(row) -> dict:
    plan = _normalize_plan(row.plan)
    now = _utcnow()
    trial_end = row.trial_end_date
    if trial_end and trial_end.tzinfo is None:
        trial_end = trial_end.replace(tzinfo=timezone.utc)
    trial_active = plan == PLAN_TRIAL and trial_end is not None and trial_end > now
    days_remaining = 0
    if trial_active:
        days_remaining = max(0, (trial_end.date() - now.date()).days)
    return {
        "plan": plan,
        "trial_active": trial_active,
        "trial_start_date": row.trial_start_date.isoformat() if row.trial_start_date else None,
        "trial_end_date": trial_end.isoformat() if trial_end else None,
        "trial_days_remaining": days_remaining,
        "summary_count": int(row.summary_count or 0),
        "remivox_interaction_count": int(row.remivox_interaction_count or 0),
        "subscription_source": row.subscription_source,
        "revenuecat_entitlement_active": bool(row.revenuecat_entitlement_active),
        "vox_period_start": row.vox_period_start.isoformat()
        if getattr(row, "vox_period_start", None)
        else None,
        "vox_period_count": int(getattr(row, "vox_period_count", 0) or 0),
    }


def _expire_trial_if_needed(conn, user_id: str, plan: str, trial_end) -> str:
    if plan != PLAN_TRIAL:
        return plan
    if trial_end is None:
        trial_start = _utcnow()
        trial_end = trial_start + timedelta(days=TRIAL_DAYS)
        conn.execute(
            text("""
                UPDATE users
                SET trial_start_date = COALESCE(trial_start_date, :trial_start),
                    trial_end_date = COALESCE(trial_end_date, :trial_end)
                WHERE id = CAST(:user_id AS uuid)
            """),
            {"user_id": user_id, "trial_start": trial_start, "trial_end": trial_end},
        )
        return PLAN_TRIAL
    if trial_end.tzinfo is None:
        trial_end = trial_end.replace(tzinfo=timezone.utc)
    if trial_end <= _utcnow():
        conn.execute(
            text("""
                UPDATE users
                SET plan = :plan,
                    subscription_updated_at = now()
                WHERE id = CAST(:user_id AS uuid)
            """),
            {"user_id": user_id, "plan": PLAN_FREE},
        )
        return PLAN_FREE
    return PLAN_TRIAL


def _fetch_user_subscription_row(conn, *, firebase_uid: Optional[str] = None, user_id: Optional[str] = None):
    if firebase_uid:
        result = conn.execute(
            text("""
                SELECT id, firebase_uid, plan, trial_start_date, trial_end_date, summary_count,
                       remivox_interaction_count, subscription_source,
                       revenuecat_entitlement_active,
                       vox_period_start, vox_period_count
                FROM users
                WHERE firebase_uid = :firebase_uid
                LIMIT 1
            """),
            {"firebase_uid": firebase_uid},
        )
    else:
        result = conn.execute(
            text("""
                SELECT id, firebase_uid, plan, trial_start_date, trial_end_date, summary_count,
                       remivox_interaction_count, subscription_source,
                       revenuecat_entitlement_active,
                       vox_period_start, vox_period_count
                FROM users
                WHERE id = CAST(:user_id AS uuid)
                LIMIT 1
            """),
            {"user_id": user_id},
        )
    return result.fetchone()


async def get_subscription_status(firebase_uid: str) -> dict:
    cache_key = f"subscription_status:{firebase_uid}"
    cached = get(cache_key)
    if cached is not None:
        return cached

    engine = get_cloud_sql_engine()
    with engine.begin() as conn:
        row = _fetch_user_subscription_row(conn, firebase_uid=firebase_uid)
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        plan = _expire_trial_if_needed(conn, str(row.id), _normalize_plan(row.plan), row.trial_end_date)
        if plan != _normalize_plan(row.plan):
            row = _fetch_user_subscription_row(conn, user_id=str(row.id))
        status = _serialize_status(row)
        if _is_test_premium_uid(firebase_uid):
            status["plan"] = PLAN_PREMIUM
            status["trial_active"] = False
            status["trial_days_remaining"] = 0
            status["revenuecat_entitlement_active"] = True
            status["subscription_source"] = "test_allowlist"
    set(cache_key, status, 60)
    return status


async def sync_revenuecat_status(
    firebase_uid: str,
    *,
    premium: bool,
    app_user_id: Optional[str],
    product_id: Optional[str],
    source: Optional[str],
) -> dict:
    engine = get_cloud_sql_engine()
    with engine.begin() as conn:
        row = _fetch_user_subscription_row(conn, firebase_uid=firebase_uid)
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        current_plan = _normalize_plan(row.plan)
        next_plan = PLAN_PREMIUM if premium else (PLAN_EXPIRED if current_plan == PLAN_PREMIUM else current_plan)
        if next_plan == PLAN_TRIAL:
            next_plan = _expire_trial_if_needed(conn, str(row.id), next_plan, row.trial_end_date)

        conn.execute(
            text("""
                UPDATE users
                SET plan = :plan,
                    revenuecat_app_user_id = COALESCE(:app_user_id, revenuecat_app_user_id),
                    revenuecat_entitlement_active = :premium,
                    subscription_source = COALESCE(:source, subscription_source),
                    subscription_updated_at = now()
                WHERE id = CAST(:user_id AS uuid)
            """),
            {
                "plan": next_plan,
                "app_user_id": app_user_id,
                "premium": premium,
                "source": source or ("play_store" if product_id else None),
                "user_id": str(row.id),
            },
        )
    invalidate(f"subscription_status:{firebase_uid}")
    invalidate(f"user_profile:{firebase_uid}")
    return await get_subscription_status(firebase_uid)


async def can_generate_summary(user_id: str) -> dict:
    engine = get_cloud_sql_engine()
    with engine.begin() as conn:
        row = _fetch_user_subscription_row(conn, user_id=user_id)
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        plan = _expire_trial_if_needed(conn, str(row.id), _normalize_plan(row.plan), row.trial_end_date)
        summary_count = int(row.summary_count or 0)
        allowed = (
            plan == PLAN_PREMIUM
            or (plan == PLAN_TRIAL and summary_count < TRIAL_SUMMARY_LIMIT)
            or (plan in {PLAN_FREE, PLAN_EXPIRED} and summary_count < FREE_SUMMARY_LIMIT)
        )
        limit = None if plan == PLAN_PREMIUM else (TRIAL_SUMMARY_LIMIT if plan == PLAN_TRIAL else FREE_SUMMARY_LIMIT)
        return {"allowed": allowed, "plan": plan, "summary_count": summary_count, "limit": limit}


async def enforce_summary_limit(user_id: str) -> None:
    status = await can_generate_summary(user_id)
    if status["allowed"]:
        return
    raise HTTPException(
        status_code=402,
        detail={
            "code": "premium_required",
            "feature": "summary_generation",
            "plan": status["plan"],
            "summary_count": status["summary_count"],
            "limit": status["limit"],
            "message": "Upgrade to RemiMinderAI Premium to generate more doctor visit summaries.",
        },
    )


async def increment_summary_count(user_id: str) -> None:
    engine = get_cloud_sql_engine()
    with engine.begin() as conn:
        row = _fetch_user_subscription_row(conn, user_id=user_id)
        if not row:
            return
        conn.execute(
            text("UPDATE users SET summary_count = summary_count + 1 WHERE id = CAST(:user_id AS uuid)"),
            {"user_id": user_id},
        )
        if row.firebase_uid:
            invalidate(f"subscription_status:{row.firebase_uid}")
            invalidate(f"user_profile:{row.firebase_uid}")


async def increment_remivox_interaction(firebase_uid: str) -> None:
    engine = get_cloud_sql_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE users
                SET remivox_interaction_count = remivox_interaction_count + 1
                WHERE firebase_uid = :firebase_uid
            """),
            {"firebase_uid": firebase_uid},
        )
    invalidate(f"subscription_status:{firebase_uid}")
    invalidate(f"user_profile:{firebase_uid}")


def remivox_test_mode_enabled(*, firebase_uid: Optional[str] = None) -> bool:
    """
    REMIVOX_TEST_MODE bypass helper (Stage D hardened).

    Defaults OFF. In production/prod environments the flag is ignored unless
    REMIVOX_TEST_MODE_ALLOW_IN_PROD=true is also set.

    Optional REMIVOX_TEST_MODE_UIDS=uid1,uid2 restricts bypass to approved testers.
    """
    raw = os.getenv("REMIVOX_TEST_MODE", "false").strip().lower()
    if raw not in {"1", "true", "yes"}:
        return False

    app_env = (
        os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or os.getenv("ENV")
        or ""
    ).strip().lower()
    if app_env in {"prod", "production"}:
        allow_prod = os.getenv("REMIVOX_TEST_MODE_ALLOW_IN_PROD", "false").strip().lower()
        if allow_prod not in {"1", "true", "yes"}:
            logger.error(
                "REMIVOX_TEST_MODE is set but ignored in production "
                "(set REMIVOX_TEST_MODE_ALLOW_IN_PROD=true to override explicitly)"
            )
            return False

    # Fail closed. An empty allowlist used to mean "bypass for everyone", so a
    # single cleared env var — exactly what --set-env-vars does to unlisted
    # names — would silently hand every user unlimited Vox with no error and no
    # log line. The bypass now requires someone to be named explicitly.
    allowlist = os.getenv("REMIVOX_TEST_MODE_UIDS", "").strip()
    allowed = {part.strip() for part in allowlist.split(",") if part.strip()}
    if not allowed:
        logger.error(
            "REMIVOX_TEST_MODE is set but REMIVOX_TEST_MODE_UIDS is empty — "
            "denying bypass (quotas stay enforced)"
        )
        return False
    if not firebase_uid or firebase_uid not in allowed:
        logger.warning(
            "REMIVOX_TEST_MODE active but uid not in REMIVOX_TEST_MODE_UIDS; denying bypass"
        )
        return False
    return True


def vox_globally_enabled() -> bool:
    """
    Server-side kill switch (REMIVOX_ENABLED). Defaults ON.

    Vox is about to reach elderly users at a scale we cannot rehearse, and an
    iOS review cycle has taken months before. Being able to turn it off without
    shipping a build is what makes releasing it a reversible decision, so this
    is deliberately absolute — it applies to test-mode accounts too.
    """
    raw = os.getenv("REMIVOX_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no"}


def _vox_quota_snapshot(status: dict, now: Optional[datetime] = None) -> dict:
    """
    Current usage position, derived from the already-fetched subscription status.

    Deliberately takes the status dict rather than re-reading the user row:
    get_subscription_status has already paid for that query (and cached it), and
    this runs in the voice path where a second round-trip is pure latency.
    """
    reference = now or _utcnow()
    plan = _normalize_plan(status.get("plan"))

    if plan == PLAN_PREMIUM:
        raw_anchor = status.get("vox_period_start")
        anchor = None
        if isinstance(raw_anchor, datetime):
            anchor = raw_anchor
        elif isinstance(raw_anchor, str) and raw_anchor:
            try:
                anchor = datetime.fromisoformat(raw_anchor.replace("Z", "+00:00"))
            except ValueError:
                anchor = None
        if anchor is not None and anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)

        period_start = current_period_start(anchor, reference)
        rolled = anchor is None or period_start != anchor
        used = 0 if rolled else int(status.get("vox_period_count") or 0)
        return {
            "scope": "premium_period",
            "limit": PREMIUM_VOX_LIMIT,
            "used": used,
            "remaining": premium_remaining(used),
            "period_start": period_start,
            "resets_at": period_resets_at(period_start),
            "period_rolled": rolled,
        }

    used = int(status.get("remivox_interaction_count") or 0)
    raw_end = status.get("trial_end_date")
    trial_end = None
    if isinstance(raw_end, datetime):
        trial_end = raw_end
    elif isinstance(raw_end, str) and raw_end:
        try:
            trial_end = datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
        except ValueError:
            trial_end = None
    return {
        "scope": "trial_total",
        "limit": TRIAL_VOX_LIMIT,
        "used": used,
        "remaining": trial_remaining(used),
        "period_start": status.get("trial_start_date"),
        "resets_at": trial_end,
        "period_rolled": False,
    }


def _vox_denied(reason: str, status_code: int, message: str, quota: dict, plan: str):
    """
    Build the refusal.

    upgrade_available is the field the client keys on: a trial user who has run
    out is a conversion moment, a Premium user who has run out already pays and
    must never be shown a paywall.
    """
    resets_at = quota.get("resets_at")
    return HTTPException(
        status_code=status_code,
        detail={
            "code": reason,
            "feature": "remivox",
            "plan": plan,
            "limit": quota.get("limit", 0),
            "used": quota.get("used", 0),
            "remaining": 0,
            "resets_at": resets_at.isoformat() if isinstance(resets_at, datetime) else None,
            "upgrade_available": reason in {REASON_TRIAL_EXHAUSTED, REASON_TRIAL_EXPIRED},
            "message": message,
        },
    )


async def enforce_remivox_access(firebase_uid: str) -> dict:
    """
    Gate Vox on the kill switch, then the subscription, then the usage quota.

    Returns the subscription status plus a "vox_quota" block so the caller can
    tell the cases apart without a second query. Raises HTTPException when Vox
    is not available.

    Developer bypass: REMIVOX_TEST_MODE (see remivox_test_mode_enabled) skips the
    quota entirely — unchanged.
    """
    if not vox_globally_enabled():
        raise _vox_denied(
            REASON_VOX_DISABLED,
            503,
            "Vox is temporarily unavailable. Everything else keeps working.",
            {"limit": 0, "used": 0, "resets_at": None},
            PLAN_FREE,
        )

    if remivox_test_mode_enabled(firebase_uid=firebase_uid):
        logger.warning(
            "REMIVOX_TEST_MODE enabled — bypassing remivox access gate for uid=%s",
            firebase_uid,
        )
        # Synthetic status: do not hit subscription columns (local DBs may lack them).
        return {
            "plan": PLAN_PREMIUM,
            "trial_active": False,
            "trial_days_remaining": 0,
            "trial_start_date": None,
            "trial_end_date": None,
            "summary_count": 0,
            "remivox_interaction_count": 0,
            "subscription_source": "remivox_test_mode",
            "revenuecat_entitlement_active": True,
            "vox_quota": {
                "scope": "unlimited",
                "limit": 0,
                "used": 0,
                "remaining": -1,
                "resets_at": None,
                "unlimited": True,
            },
        }

    status = await get_subscription_status(firebase_uid)
    plan = status["plan"]

    quota = _vox_quota_snapshot(status)
    status = {
        **status,
        "vox_quota": {
            "scope": quota["scope"],
            "limit": quota["limit"],
            "used": quota["used"],
            "remaining": quota["remaining"],
            "resets_at": quota["resets_at"].isoformat()
            if isinstance(quota["resets_at"], datetime)
            else None,
            "unlimited": False,
        },
    }

    if plan == PLAN_PREMIUM:
        if quota["remaining"] <= 0:
            resets = quota["resets_at"]
            when = resets.strftime("the %d")+ resets.strftime(" of %B") if isinstance(resets, datetime) else "soon"
            raise _vox_denied(
                REASON_PREMIUM_EXHAUSTED,
                429,
                f"You've reached this month's Vox limit. It resets on {when}.",
                quota,
                plan,
            )
        return status

    if plan == PLAN_TRIAL and status["trial_active"]:
        if quota["remaining"] <= 0:
            raise _vox_denied(
                REASON_TRIAL_EXHAUSTED,
                402,
                f"You've used all {TRIAL_VOX_LIMIT} Vox commands in your trial. "
                "Upgrade to keep using voice.",
                quota,
                plan,
            )
        return status

    raise _vox_denied(
        REASON_TRIAL_EXPIRED,
        402,
        "Your 14-day trial has ended. Subscribe to RemiMinderAI Premium "
        "to keep using all features, including Vox.",
        quota,
        plan,
    )


async def record_vox_use(firebase_uid: str) -> None:
    """
    Consume one Vox use. Called only for turns that completed a user intent.

    Never raises: a counter write must not turn a reply the user already heard
    into an error.
    """
    if not firebase_uid or remivox_test_mode_enabled(firebase_uid=firebase_uid):
        return
    try:
        engine = get_cloud_sql_engine()
        with engine.begin() as conn:
            row = _fetch_user_subscription_row(conn, firebase_uid=firebase_uid)
            if not row:
                return
            plan = _normalize_plan(row.plan)
            if plan == PLAN_PREMIUM:
                quota = _vox_quota_snapshot(_serialize_status(row))
                conn.execute(
                    text("""
                        UPDATE users
                        SET vox_period_start = :period_start,
                            vox_period_count = :next_count
                        WHERE id = :user_id
                    """),
                    {
                        "period_start": quota["period_start"],
                        "next_count": quota["used"] + 1,
                        "user_id": row.id,
                    },
                )
            else:
                conn.execute(
                    text("""
                        UPDATE users
                        SET remivox_interaction_count =
                            COALESCE(remivox_interaction_count, 0) + 1
                        WHERE id = :user_id
                    """),
                    {"user_id": row.id},
                )
        invalidate(f"subscription_status:{firebase_uid}")
    except Exception as exc:
        logger.warning("record_vox_use failed: %s", type(exc).__name__)


async def enforce_caregiver_invite_limit(patient_id: str) -> None:
    engine = get_cloud_sql_engine()
    with engine.begin() as conn:
        row = _fetch_user_subscription_row(conn, user_id=patient_id)
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        plan = _expire_trial_if_needed(conn, str(row.id), _normalize_plan(row.plan), row.trial_end_date)
        count_row = conn.execute(
            text("""
                SELECT
                  (SELECT COUNT(*) FROM care_team_members
                   WHERE patient_id = CAST(:patient_id AS uuid)
                     AND status IN ('active', 'accepted')) +
                  (SELECT COUNT(*) FROM care_team_invitations
                   WHERE patient_id = CAST(:patient_id AS uuid)
                     AND status = 'pending') AS total
            """),
            {"patient_id": patient_id},
        ).fetchone()
        total = int(count_row.total or 0)
        limit = PREMIUM_CAREGIVER_LIMIT if plan == PLAN_PREMIUM else (
            TRIAL_CAREGIVER_LIMIT if plan == PLAN_TRIAL else FREE_CAREGIVER_LIMIT
        )
        if total < limit:
            return
    raise HTTPException(
        status_code=402,
        detail={
            "code": "premium_required",
            "feature": "caregiver_invite",
            "plan": plan,
            "caregiver_count": total,
            "limit": limit,
            "message": "Upgrade to RemiMinderAI Premium to add more caregivers.",
        },
    )

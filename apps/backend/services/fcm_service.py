"""
Firebase Cloud Messaging (FCM) Service for Push Notifications.
Sends push notifications to patient devices when caregivers set reminders.
"""

import os
import logging
from typing import Optional
from firebase_admin import credentials, initialize_app, messaging

logger = logging.getLogger(__name__)

_fcm_initialized = False


def _load_service_account() -> Optional[dict]:
    """
    Read the service account from whichever env var this deployment provides.

    Two shapes are in use: FIREBASE_SERVICE_ACCOUNT holds base64-encoded JSON,
    while FIREBASE_SERVICE_ACCOUNT_JSON (what Cloud Run mounts from Secret
    Manager) holds raw JSON. Accept either, so FCM does not silently stay off
    because of a naming mismatch.
    """
    import base64
    import json as json_module

    for name in ("FIREBASE_SERVICE_ACCOUNT", "FIREBASE_SERVICE_ACCOUNT_JSON"):
        raw = (os.getenv(name) or "").strip()
        if not raw:
            continue
        try:
            if raw.startswith("{"):
                return json_module.loads(raw)
            return json_module.loads(base64.b64decode(raw).decode("utf-8"))
        except Exception as exc:
            logger.error("%s is set but could not be decoded: %s", name, type(exc).__name__)
    return None


def init_fcm():
    """
    Initialize Firebase Admin SDK for FCM.
    Credentials come from FIREBASE_SERVICE_ACCOUNT (base64) or
    FIREBASE_SERVICE_ACCOUNT_JSON (raw JSON).
    """
    global _fcm_initialized
    if _fcm_initialized:
        return True

    try:
        service_account_info = _load_service_account()
        if not service_account_info:
            logger.warning(
                "No Firebase service account configured "
                "(FIREBASE_SERVICE_ACCOUNT / FIREBASE_SERVICE_ACCOUNT_JSON) - FCM disabled"
            )
            return False

        import firebase_admin
        cred = credentials.Certificate(service_account_info)
        try:
            initialize_app(cred)
        except ValueError:
            # Default app already exists (e.g. hot-reload) — reuse it
            firebase_admin.get_app()

        _fcm_initialized = True
        logger.info("FCM initialized successfully")
        return True

    except Exception as e:
        logger.error(f"Failed to initialize FCM: {e}")
        return False


def send_medication_reminder(
    fcm_token: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> bool:
    """
    Send a medication reminder push notification to a device.

    Args:
        fcm_token: The device's FCM registration token
        title: Notification title
        body: Notification body text
        data: Optional data payload for deep linking

    Returns:
        True if sent successfully
    """
    if not _fcm_initialized:
        if not init_fcm():
            logger.error("FCM not initialized - cannot send notification")
            return False

    try:
        notification = messaging.Notification(
            title=title,
            body=body,
        )

        android_config = messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                channel_id="medication_reminders_v2",
                priority="high",
                default_vibrate=True,
                default_sound=True,
            ),
        )

        apns_config = messaging.APNSConfig(
            headers={"apns-priority": "10"},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    sound="default",
                    badge=1,
                )
            ),
        )

        message = messaging.Message(
            notification=notification,
            data=data or {
                "type": "medication_reminder",
                "click_action": "FLUTTER_NOTIFICATION_CLICK",
            },
            token=fcm_token,
            android=android_config,
            apns=apns_config,
        )

        response = messaging.send(message)
        logger.info(f"FCM notification sent: {response}")
        return True

    except Exception as e:
        logger.error(f"Failed to send FCM notification: {e}")
        return False


def send_to_topic(
    topic: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> bool:
    """
    Send a notification to all devices subscribed to a topic.

    Args:
        topic: The FCM topic name
        title: Notification title
        body: Notification body
        data: Optional data payload

    Returns:
        True if sent successfully
    """
    if not _fcm_initialized:
        if not init_fcm():
            return False

    try:
        notification = messaging.Notification(title=title, body=body)
        message = messaging.Message(
            notification=notification,
            data=data or {"type": "topic_message"},
            topic=topic,
        )

        response = messaging.send(message)
        logger.info(f"FCM topic message sent: {response}")
        return True

    except Exception as e:
        logger.error(f"Failed to send FCM topic message: {e}")
        return False


def subscribe_to_topic(fcm_tokens: list, topic: str) -> bool:
    """
    Subscribe devices to a topic.

    Args:
        fcm_tokens: List of device FCM tokens
        topic: Topic name to subscribe to

    Returns:
        True if successful
    """
    if not _fcm_initialized:
        if not init_fcm():
            return False

    try:
        response = messaging.subscribe_to_topic(fcm_tokens, topic)
        logger.info(f"Subscribed {response.success_count} tokens to {topic}")
        return True

    except Exception as e:
        logger.error(f"Failed to subscribe to topic: {e}")
        return False


def unsubscribe_from_topic(fcm_tokens: list, topic: str) -> bool:
    """
    Unsubscribe devices from a topic.

    Args:
        fcm_tokens: List of device FCM tokens
        topic: Topic name to unsubscribe from

    Returns:
        True if successful
    """
    if not _fcm_initialized:
        if not init_fcm():
            return False

    try:
        response = messaging.unsubscribe_from_topic(fcm_tokens, topic)
        logger.info(f"Unsubscribed {response.success_count} tokens from {topic}")
        return True

    except Exception as e:
        logger.error(f"Failed to unsubscribe from topic: {e}")
        return False
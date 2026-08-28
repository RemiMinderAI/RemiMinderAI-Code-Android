import logging
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from services.db_reminders import PushClaim
from services.reminder_due_push_service import process_reminder_due_push_notifications

SVC = "services.reminder_due_push_service"


def due_row():
    return {
        "id": "r-1",
        "user_id": "p-1",
        "title": "Take medication",
        "message": "Dose now",
        "scheduled_time": datetime.now(timezone.utc),
    }


def _mocks(*, claim, tokens, delivered, caregivers=None):
    """Everything the service touches, so no test reaches a network or a DB."""
    caregivers = caregivers if caregivers is not None else []
    return {
        f"{SVC}.get_reminders_due_in_window": AsyncMock(return_value=[due_row()]),
        f"{SVC}.try_claim_reminder_due_push": AsyncMock(return_value=claim),
        f"{SVC}.get_patient_fcm_tokens": AsyncMock(return_value=tokens),
        f"{SVC}.send_fcm_to_tokens": AsyncMock(return_value=delivered),
        f"{SVC}.mark_reminder_due_push_delivered": AsyncMock(return_value=True),
        f"{SVC}.list_patient_caregivers_for_alerts": AsyncMock(return_value=caregivers),
        f"{SVC}.caregiver_alert_exists_for_context": AsyncMock(return_value=False),
        f"{SVC}.create_caregiver_alert": AsyncMock(return_value={"id": "a1"}),
        f"{SVC}.get_patient_info": AsyncMock(return_value={"full_name": "Alex"}),
        f"{SVC}.send_caregiver_alert_email": AsyncMock(),
    }


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, **kw):
        mocks = _mocks(**kw)
        patches = [patch(target, new=m) for target, m in mocks.items()]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        stats = await process_reminder_due_push_notifications()
        return stats, mocks

    async def test_successful_send_closes_the_row(self):
        """A delivered push ends retries immediately."""
        stats, m = await self._run(
            claim=PushClaim(claimed=True, attempts=1), tokens=["t1"], delivered=1
        )
        self.assertEqual(stats["patient_pushes"], 1)
        m[f"{SVC}.mark_reminder_due_push_delivered"].assert_awaited_once()

    async def test_failed_send_leaves_the_row_retryable(self):
        """The row must NOT be marked delivered, or the retry never happens."""
        stats, m = await self._run(
            claim=PushClaim(claimed=True, attempts=1), tokens=["t1"], delivered=0
        )
        self.assertEqual(stats["patient_pushes"], 0)
        m[f"{SVC}.mark_reminder_due_push_delivered"].assert_not_awaited()

    async def test_retry_inside_the_window_delivers(self):
        """A later run claims again and gets through; that is the whole fix."""
        stats, m = await self._run(
            claim=PushClaim(claimed=True, attempts=3), tokens=["t1"], delivered=1
        )
        self.assertEqual(stats["patient_pushes"], 1)
        self.assertEqual(stats["retries"], 1)
        m[f"{SVC}.mark_reminder_due_push_delivered"].assert_awaited_once()

    async def test_already_finished_sends_nothing(self):
        """Delivered or abandoned: no claim, so no send and no log noise."""
        stats, m = await self._run(
            claim=PushClaim(claimed=False), tokens=["t1"], delivered=1
        )
        self.assertEqual(stats["patient_pushes"], 0)
        self.assertEqual(stats["abandoned"], 0)
        m[f"{SVC}.send_fcm_to_tokens"].assert_not_awaited()

    async def test_abandoned_is_reported_once_and_never_sent(self):
        """
        Past the window the dose alert is given up on — never delivered late,
        because a late medication prompt can cause a double dose.
        """
        with self.assertLogs(SVC, level="ERROR") as captured:
            stats, m = await self._run(
                claim=PushClaim(claimed=False, attempts=9, newly_abandoned=True),
                tokens=["t1"],
                delivered=1,
            )
        self.assertEqual(stats["abandoned"], 1)
        m[f"{SVC}.send_fcm_to_tokens"].assert_not_awaited()
        joined = "\n".join(captured.output)
        self.assertIn("MEDICATION REMINDER NOT DELIVERED", joined)
        self.assertIn("the patient was not notified", joined)


class FanOutTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_registered_device_is_sent_to(self):
        """A phone and a tablet both get the reminder."""
        mocks = _mocks(
            claim=PushClaim(claimed=True, attempts=1),
            tokens=["phone", "tablet"],
            delivered=2,
        )
        patches = [patch(t, new=m) for t, m in mocks.items()]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        stats = await process_reminder_due_push_notifications()

        sent = mocks[f"{SVC}.send_fcm_to_tokens"]
        sent.assert_awaited_once()
        self.assertEqual(sent.await_args.kwargs["fcm_tokens"], ["phone", "tablet"])
        self.assertEqual(stats["patient_pushes"], 2)

    async def test_caregiver_fan_out_uses_every_device_too(self):
        """The caregiver path was the second call site; it must fan out as well."""
        mocks = _mocks(
            claim=PushClaim(claimed=True, attempts=1),
            tokens=["p1"],
            delivered=1,
            caregivers=[{"caregiver_id": "cg-1", "email": "cg@example.com"}],
        )
        mocks[f"{SVC}.get_patient_fcm_tokens"] = AsyncMock(
            side_effect=[["p1"], ["cg-phone", "cg-tablet"]]
        )
        mocks[f"{SVC}.send_fcm_to_tokens"] = AsyncMock(side_effect=[1, 2])
        patches = [patch(t, new=m) for t, m in mocks.items()]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        stats = await process_reminder_due_push_notifications()

        calls = mocks[f"{SVC}.send_fcm_to_tokens"].await_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].kwargs["fcm_tokens"], ["cg-phone", "cg-tablet"])
        self.assertEqual(stats["caregiver_pushes"], 2)
        mocks[f"{SVC}.create_caregiver_alert"].assert_awaited_once()
        mocks[f"{SVC}.send_caregiver_alert_email"].assert_awaited_once()


class SendFanOutHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_dead_device_does_not_stop_the_others(self):
        from services.reminder_service import send_fcm_to_tokens

        with patch(
            "services.reminder_service.send_fcm_notification",
            new=AsyncMock(side_effect=[False, True, True]),
        ) as one:
            delivered = await send_fcm_to_tokens(
                ["dead", "phone", "tablet"], "t", "b", {}
            )
        self.assertEqual(delivered, 2)
        self.assertEqual(one.await_count, 3)

    async def test_blank_tokens_are_skipped(self):
        from services.reminder_service import send_fcm_to_tokens

        with patch(
            "services.reminder_service.send_fcm_notification",
            new=AsyncMock(return_value=True),
        ) as one:
            delivered = await send_fcm_to_tokens(["", None, "real"], "t", "b", {})
        self.assertEqual(delivered, 1)
        self.assertEqual(one.await_count, 1)


if __name__ == "__main__":
    unittest.main()

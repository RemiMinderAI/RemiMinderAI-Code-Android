"""
A refusal must end the turn.

"Mark done Aspirin" with no Aspirin reminder was declined correctly, and then
the create that had been half-collected before it carried on and scheduled a
dose of the drug the patient was saying they had already taken.

The specific wording will change. The shape is what keeps coming back, so the
invariant is asserted over every branch rather than over two utterances.
"""

import ast
import asyncio
import inspect
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.remivox.pipeline as pipeline  # noqa: E402
from services.remivox.actions.types import ActionResult  # noqa: E402
from services.remivox.intents.models import VoxIntent  # noqa: E402
from services.remivox.state.conversation import (  # noqa: E402
    clear_state,
    get_state,
    upsert_pending,
)

EXECUTOR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "services", "remivox", "actions", "executor.py",
)


class TheMechanism(unittest.TestCase):
    """The rule itself, not any one branch that relies on it."""

    def test_only_a_declared_pending_intent_holds_the_turn_open(self):
        src = inspect.getsource(pipeline.run_care_turn)
        clause = src[src.index("if action_result.clear_pending"):]
        clause = clause[: clause.index("clear_state")]
        # The old condition was "success and not pending_intent", which matched
        # neither branch for a refusal and so left the previous state alone.
        self.assertIn("not action_result.pending_intent", clause)
        self.assertNotIn("action_result.success", clause)

    def test_every_refusal_in_the_executor_is_covered_by_it(self):
        """
        No branch has to remember to set clear_pending.

        Thirteen results are declines that declare no pending intent. Under the
        rule above every one of them ends the turn; this fails if the rule is
        narrowed back to something a new branch could fall outside of.
        """
        with io.open(EXECUTOR, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        uncovered = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") == "ActionResult"):
                continue
            kw = {k.arg for k in node.keywords}
            success = next(
                (k.value for k in node.keywords if k.arg == "success"), None
            )
            is_decline = isinstance(success, ast.Constant) and success.value is False
            if is_decline and "pending_intent" not in kw:
                # Ends the turn only because the pipeline says so.
                uncovered.append(node.lineno)
        self.assertTrue(uncovered, "expected declines that rely on the rule")
        src = inspect.getsource(pipeline.run_care_turn)
        self.assertIn(
            "if action_result.clear_pending or not action_result.pending_intent:", src,
            f"{len(uncovered)} declining branches depend on this line",
        )


def _nlu(intent, entities=None, confidence=0.95):
    return {
        "intent": intent.value if hasattr(intent, "value") else intent,
        "entities": entities or {},
        "confidence": confidence,
        "source": "test",
    }


REMINDERS = {
    "today": [
        {
            "id": "r1", "title": "Metoprolol", "medication_name": "Metoprolol",
            "scheduled_time": "2026-09-03T12:00:00+00:00", "status": "pending",
            "recurrence": "daily", "timezone": "America/Toronto",
        }
    ],
    "upcoming": [],
}


class ARefusalDoesNotLeaveACreateRunning(unittest.TestCase):
    """
    Driven through run_care_turn so the adapter, the executor and the state
    store all take part. Building an IntentResult by hand starts below the
    layer that was failing, which is how this survived a previous fix.
    """

    def setUp(self):
        self.user = "u-refusal"
        self.sid = "s-1"
        clear_state(self.user, self.sid)
        self.created = []
        patcher = mock.patch(
            "services.remivox.actions.executor.create_new_reminder",
            side_effect=lambda *a, **k: self.created.append(k) or {"id": "x"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(clear_state, self.user, self.sid)

    def _turn(self, nlu):
        with mock.patch(
            "services.remivox.pipeline.extract_care_intent",
            new=mock.AsyncMock(return_value=nlu),
        ):
            return asyncio.run(
                pipeline.run_care_turn(
                    user_uuid=self.user, text="spoken", reminders=REMINDERS,
                    summaries=[], timezone_name="America/Toronto",
                    session_id=self.sid,
                )
            )

    def test_a_declined_completion_clears_a_create_in_flight(self):
        self._turn(_nlu(VoxIntent.CREATE_REMINDER, {"title": "Aspirin"}))
        self.assertEqual(
            get_state(self.user, self.sid).pending_intent,
            VoxIntent.CREATE_REMINDER.value,
            "a create should be waiting for its time",
        )

        self._turn(_nlu(VoxIntent.COMPLETE_REMINDER, {"title": "Aspirin"}))
        self.assertIsNone(
            get_state(self.user, self.sid),
            "the refusal left the create running",
        )

    def test_and_the_next_turn_cannot_finish_it(self):
        self._turn(_nlu(VoxIntent.CREATE_REMINDER, {"title": "Aspirin"}))
        self._turn(_nlu(VoxIntent.COMPLETE_REMINDER, {"title": "Aspirin"}))
        # A time, which is what the abandoned create was waiting for.
        self._turn(_nlu(VoxIntent.CREATE_REMINDER, {"time": "20:00"}))
        self.assertEqual(
            self.created, [], "a declined mark-done ended up creating a reminder"
        )

    def test_a_question_still_holds_the_turn(self):
        self._turn(_nlu(VoxIntent.CREATE_REMINDER, {"title": "Aspirin"}))
        state = get_state(self.user, self.sid)
        self.assertIsNotNone(state, "asking a question must keep the state")
        self.assertEqual(state.pending_intent, VoxIntent.CREATE_REMINDER.value)

    def test_answering_that_question_still_works(self):
        self._turn(_nlu(VoxIntent.CREATE_REMINDER, {"title": "Metoprolol"}))
        self._turn(_nlu(VoxIntent.CREATE_REMINDER, {"time": "20:00"}))
        self._turn(_nlu(VoxIntent.CREATE_REMINDER, {"frequency": "daily"}))
        self.assertEqual(len(self.created), 1, "a real create must still complete")

    def test_a_successful_completion_ends_the_turn_as_before(self):
        upsert_pending(
            user_id=self.user, session_id=self.sid, language="en",
            detected_language="en", pending_intent=VoxIntent.CREATE_REMINDER.value,
            pending_entities={"title": "Aspirin"}, missing_slots=["time"],
        )
        self._turn(_nlu(VoxIntent.READ_TODAY_MEDICATIONS))
        self.assertIsNone(get_state(self.user, self.sid))


class APlaceholderIsNotAName(unittest.TestCase):
    def test_the_plural_that_escaped(self):
        from services.remivox.intents.extractors import (
            is_generic_medication_placeholder as placeholder,
        )

        # "take my medicine" and "take my pills" were listed; the plural was
        # not, and a reminder called "Take my medicines" was created.
        for title in (
            "Take my medicines", "take my medicines", "Take my medication",
            "my medicines", "the pills", "Please take my medication",
            "take the tablets", "Medicine reminder",
        ):
            self.assertTrue(placeholder(title), title)

    def test_a_named_medication_still_passes(self):
        from services.remivox.intents.extractors import (
            is_generic_medication_placeholder as placeholder,
        )

        for title in (
            "Metoprolol", "Vitamin D3", "take Metoprolol",
            "medicine cabinet", "pick up my pills from the pharmacy",
            "my blood pressure pills",
        ):
            self.assertFalse(placeholder(title), title)


if __name__ == "__main__":
    unittest.main()

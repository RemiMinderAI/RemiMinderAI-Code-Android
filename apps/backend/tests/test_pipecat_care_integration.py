import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import TranscriptionFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection

from services.remivox.pipecat_processor import RemiVoxProcessor


class TestRemiVoxProcessorCareIntegration(unittest.IsolatedAsyncioTestCase):
    """Processor frame-flow tests with run_care_turn mocked to avoid DB calls."""

    async def _process_transcript(self, transcript: str, *, care_result=None, care_error=None):
        processor = RemiVoxProcessor(
            firebase_uid="firebase-user-123",
            timezone="America/New_York",
            session_id="test-session",
        )
        pushed_frames = []

        async def capture_push(frame, direction=FrameDirection.DOWNSTREAM):
            pushed_frames.append((frame, direction))

        processor.push_frame = capture_push
        mock_care_turn = AsyncMock()
        if care_error is not None:
            mock_care_turn.side_effect = care_error
        else:
            mock_care_turn.return_value = care_result or {}

        resolve_user = AsyncMock(return_value="internal-user-uuid")
        reminder_context = {
            "today": [{"title": "Metoprolol"}],
            "upcoming": [],
            "past": [],
        }
        summary_context = [{"title": "Cardiology", "summary": "Stable"}]
        load_reminders = AsyncMock(return_value=reminder_context)
        load_summaries = AsyncMock(return_value=summary_context)
        with (
            patch(
                "services.remivox.pipecat_processor.run_care_turn",
                mock_care_turn,
            ),
            patch(
                "services.remivox.pipecat_processor.get_user_uuid",
                resolve_user,
            ),
            patch(
                "services.remivox.pipecat_processor.list_patient_reminders",
                load_reminders,
            ),
            patch(
                "services.remivox.pipecat_processor.get_user_summaries",
                load_summaries,
            ),
        ):
            await processor.process_frame(
                TranscriptionFrame(
                    text=transcript,
                    user_id="speaker",
                    timestamp="2026-08-07T21:51:00Z",
                    finalized=True,
                ),
                FrameDirection.DOWNSTREAM,
            )

        resolve_user.assert_awaited_once_with("firebase-user-123")
        load_reminders.assert_awaited_once_with("internal-user-uuid")
        load_summaries.assert_awaited_once_with(
            "internal-user-uuid",
            firebase_uid="firebase-user-123",
        )
        self.assertEqual(reminder_context, mock_care_turn.await_args.kwargs["reminders"])
        self.assertEqual(summary_context, mock_care_turn.await_args.kwargs["summaries"])
        return mock_care_turn, pushed_frames

    def _assert_text_frame(self, pushed_frames, expected_text: str):
        self.assertEqual(1, len(pushed_frames))
        frame, direction = pushed_frames[0]
        # Must be TTSSpeakFrame, not a bare TextFrame. TTSService only allocates
        # a turn context id on LLMFullResponseStartFrame, and this pipeline has
        # no LLM — a TextFrame therefore synthesises audio into no context
        # ("unable to append audio to context: no context ID provided") and
        # nothing ever reaches the WebSocket.
        self.assertIsInstance(frame, TTSSpeakFrame)
        self.assertEqual(expected_text, frame.text)
        # No LLM context aggregator exists downstream to receive the text.
        self.assertFalse(frame.append_to_context)
        self.assertEqual(FrameDirection.DOWNSTREAM, direction)

    async def test_create_reminder_text_frame(self):
        response_text = "Done - I set a daily Metoprolol reminder for 8 PM."
        mock_care_turn, pushed_frames = await self._process_transcript(
            "Set a reminder for Metoprolol at 8 PM every day",
            care_result={
                "text": response_text,
                "intent": "CREATE_REMINDER",
                "action": "create_reminder",
                "action_payload": {"intent": "CREATE_REMINDER"},
                "success": True,
            },
        )

        mock_care_turn.assert_awaited_once()
        kwargs = mock_care_turn.await_args.kwargs
        self.assertEqual(
            "Set a reminder for Metoprolol at 8 PM every day",
            kwargs["text"],
        )
        self.assertEqual(
            "Set a reminder for Metoprolol at 8 PM every day",
            kwargs["transcript"],
        )
        self.assertEqual("internal-user-uuid", kwargs["user_uuid"])
        self.assertEqual("America/New_York", kwargs["timezone_name"])
        self.assertEqual("test-session", kwargs["session_id"])
        self._assert_text_frame(pushed_frames, response_text)

    async def test_read_today_medications_text_frame(self):
        response_text = "Today you have Metoprolol at 8 PM and Vitamin D at 9 AM."
        mock_care_turn, pushed_frames = await self._process_transcript(
            "What medicines do I have today",
            care_result={
                "text": response_text,
                "intent": "READ_TODAY_MEDICATIONS",
                "action": "read_today_medications",
                "action_payload": {"intent": "READ_TODAY_MEDICATIONS"},
                "success": True,
            },
        )

        mock_care_turn.assert_awaited_once()
        self.assertEqual("What medicines do I have today", mock_care_turn.await_args.kwargs["text"])
        self._assert_text_frame(pushed_frames, response_text)

    async def test_cancel_action_text_frame(self):
        response_text = "Okay, I canceled that action."
        mock_care_turn, pushed_frames = await self._process_transcript(
            "Cancel that",
            care_result={
                "text": response_text,
                "intent": "CANCEL_ACTION",
                "action": "cancel_action",
                "action_payload": {"intent": "CANCEL_ACTION"},
                "success": True,
            },
        )

        mock_care_turn.assert_awaited_once()
        self.assertEqual("Cancel that", mock_care_turn.await_args.kwargs["text"])
        self._assert_text_frame(pushed_frames, response_text)

    async def test_user_uuid_is_cached_but_context_refreshes_each_turn(self):
        processor = RemiVoxProcessor(
            firebase_uid="firebase-user-123",
            session_id="test-session",
        )

        async def capture_push(frame, direction=FrameDirection.DOWNSTREAM):
            return None

        processor.push_frame = capture_push
        resolve_user = AsyncMock(return_value="internal-user-uuid")
        load_reminders = AsyncMock(
            return_value={"today": [], "upcoming": [], "past": []}
        )
        load_summaries = AsyncMock(return_value=[])
        care_turn = AsyncMock(return_value={"text": "Okay.", "intent": "HELP"})

        with (
            patch(
                "services.remivox.pipecat_processor.get_user_uuid",
                resolve_user,
            ),
            patch(
                "services.remivox.pipecat_processor.list_patient_reminders",
                load_reminders,
            ),
            patch(
                "services.remivox.pipecat_processor.get_user_summaries",
                load_summaries,
            ),
            patch(
                "services.remivox.pipecat_processor.run_care_turn",
                care_turn,
            ),
        ):
            for text in ("First turn", "Second turn"):
                await processor.process_frame(
                    TranscriptionFrame(
                        text=text,
                        user_id="speaker",
                        timestamp="2026-08-08T03:27:00Z",
                        finalized=True,
                    ),
                    FrameDirection.DOWNSTREAM,
                )

        resolve_user.assert_awaited_once_with("firebase-user-123")
        self.assertEqual(2, load_reminders.await_count)
        self.assertEqual(2, load_summaries.await_count)
        self.assertEqual(2, care_turn.await_count)
        for call in care_turn.await_args_list:
            self.assertEqual("internal-user-uuid", call.kwargs["user_uuid"])

    async def test_gibberish_emits_fallback_text_frame(self):
        mock_care_turn, pushed_frames = await self._process_transcript(
            "zxqv blorf ???",
            care_error=RuntimeError("care engine could not classify transcript"),
        )

        mock_care_turn.assert_awaited_once()
        self._assert_text_frame(pushed_frames, RemiVoxProcessor.FALLBACK_RESPONSE)


class TestRemiVoxProcessorTranscriptMerging(unittest.IsolatedAsyncioTestCase):
    """Pulse can split one utterance across several TranscriptionFrames.

    Running a care turn per fragment makes the engine ask clarifying questions
    instead of acting, so fragments arriving close together must be merged into
    a single turn.
    """

    def _make_processor(self, merge_window_s: float):
        processor = RemiVoxProcessor(
            firebase_uid="firebase-user-123",
            timezone="America/New_York",
            session_id="test-session",
        )
        processor._merge_window_s = merge_window_s
        processor._task_manager = object()  # satisfy the task-manager availability check
        # Stand in for the pipeline TaskManager so the debounce path is taken
        # rather than the inline fallback.
        processor.create_task = lambda coro, name=None: asyncio.ensure_future(coro)

        async def cancel_task(task, timeout=None):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        processor.cancel_task = cancel_task
        return processor

    async def _feed(self, processor, fragments, gap_s: float):
        pushed = []

        async def capture_push(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append(frame)

        processor.push_frame = capture_push
        care_turn = AsyncMock(return_value={"response_text": "Okay, done.",
                                            "intent_type": "create_reminder"})
        with (
            patch("services.remivox.pipecat_processor.run_care_turn", care_turn),
            patch("services.remivox.pipecat_processor.get_user_uuid",
                  AsyncMock(return_value="internal-user-uuid")),
            patch("services.remivox.pipecat_processor.list_patient_reminders",
                  AsyncMock(return_value={"today": [], "upcoming": [], "past": []})),
            patch("services.remivox.pipecat_processor.get_user_summaries",
                  AsyncMock(return_value=[])),
        ):
            for text in fragments:
                await processor.process_frame(
                    TranscriptionFrame(text=text, user_id="u", timestamp="2026-01-01T00:00:00Z"),
                    FrameDirection.DOWNSTREAM,
                )
                await asyncio.sleep(gap_s)
            # Let the final debounce elapse.
            await asyncio.sleep(processor._merge_window_s * 3)
        return care_turn, pushed

    async def test_split_utterance_becomes_one_care_turn(self):
        processor = self._make_processor(merge_window_s=0.15)
        care_turn, pushed = await self._feed(
            processor,
            ["Set a reminder for vitamin B12", "at 10 AM every day"],
            gap_s=0.02,  # fragments of one utterance arrive back-to-back
        )

        care_turn.assert_awaited_once()
        merged = "Set a reminder for vitamin B12 at 10 AM every day"
        # run_care_turn receives the transcript under both kwargs; assert both so
        # this stays meaningful if either is dropped.
        self.assertEqual(merged, care_turn.await_args.kwargs["transcript"])
        self.assertEqual(merged, care_turn.await_args.kwargs["text"])
        speak = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
        self.assertEqual(1, len(speak), "exactly one spoken response per utterance")

    async def test_separate_utterances_stay_separate(self):
        processor = self._make_processor(merge_window_s=0.05)
        care_turn, pushed = await self._feed(
            processor,
            ["What are my medications today", "Cancel my 8 PM reminder"],
            gap_s=0.25,  # a real pause between two distinct commands
        )

        self.assertEqual(2, care_turn.await_count)
        self.assertEqual(
            2, len([f for f in pushed if isinstance(f, TTSSpeakFrame)])
        )


if __name__ == "__main__":
    unittest.main()

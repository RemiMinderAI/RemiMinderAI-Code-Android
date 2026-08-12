import asyncio
import json
import time
from datetime import datetime, timezone

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameProcessor

from services.db_service import get_user_summaries, get_user_uuid
from services.reminder_service import list_patient_reminders
from services.remivox.config import REMIVOX_TRANSCRIPT_MERGE_MS
from services.remivox.languages import DEFAULT_REMIVOX_LANGUAGE
from services.remivox.observability import logging
from services.remivox.pipeline import run_care_turn


logger = logging.getLogger("remivox.observability")


def _log_turn_event(event: str, **fields) -> None:
    logger.info(
        json.dumps(
            {
                "event": event,
                "severity": "INFO",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **fields,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


class RemiVoxProcessor(FrameProcessor):
    """Bridge finalized Pipecat STT transcripts into the RemiVox care engine."""

    FALLBACK_RESPONSE = "Sorry, I didn't catch that. Could you try again?"

    def __init__(
        self,
        firebase_uid: str,
        timezone: str = "UTC",
        session_id: str | None = None,
        keywords: list[tuple[str, float]] | None = None,
        session_stats: dict[str, int] | None = None,
    ):
        super().__init__()
        self.firebase_uid = firebase_uid
        self.timezone = timezone or "UTC"
        self.session_id = session_id
        self.keywords = list(keywords or [])
        self.session_stats = session_stats
        self._user_uuid: str | None = None
        self._last_care_time: float = 0.0
        # Fragments of the utterance currently being assembled, and the debounce
        # task that will flush them once transcripts stop arriving.
        self._pending_parts: list[str] = []
        self._pending_language: str = DEFAULT_REMIVOX_LANGUAGE
        self._flush_task = None
        self._merge_window_s = max(REMIVOX_TRANSCRIPT_MERGE_MS, 0) / 1000.0
        # Serialises care turns so a late fragment cannot start a second turn
        # while the previous one is still running.
        self._turn_lock = asyncio.Lock()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        # Interim vs final is decided by frame TYPE, never by a `finalized`
        # attribute. In Pipecat 1.7.0 TranscriptionFrame and
        # InterimTranscriptionFrame are siblings (both subclass TextFrame), and
        # SmallestSTT does not set finalized=True on finals — so a
        # `getattr(frame, "finalized", True)` guard silently drops every
        # transcript. Interims are forwarded rather than dropped: Pipecat's
        # TTSService explicitly excludes both transcription frame types from
        # synthesis, so passing them on is safe and keeps the frame contract
        # intact for any downstream consumer.
        if isinstance(frame, InterimTranscriptionFrame):
            await self.push_frame(frame, direction)
            return

        # Flush anything still buffered before the pipeline shuts down, so a
        # trailing fragment is not silently lost.
        if isinstance(frame, (EndFrame, CancelFrame)):
            await self._cancel_flush_task()
            if isinstance(frame, EndFrame):
                await self._flush_pending()
            else:
                self._pending_parts.clear()
            await self.push_frame(frame, direction)
            return

        if not isinstance(frame, TranscriptionFrame):
            await self.push_frame(frame, direction)
            return

        transcript = (frame.text or "").strip()
        logger.info(f"🎤 PIPECAT_PROCESSOR: received transcript fragment='{transcript}'")
        if not transcript:
            return

        frame_language = getattr(frame, "language", None)
        language = (
            getattr(frame_language, "value", frame_language)
            or DEFAULT_REMIVOX_LANGUAGE
        )

        # Pulse can split one utterance across several TranscriptionFrames.
        # Buffer the fragment and (re)arm the debounce rather than running a care
        # turn per fragment, which makes the engine ask clarifying questions
        # instead of acting on the complete sentence.
        self._pending_parts.append(transcript)
        self._pending_language = str(language)
        _log_turn_event(
            "remivox_turn_transcript_received",
            text_length=len(transcript),
            language=str(language),
            fragments_buffered=len(self._pending_parts),
        )

        await self._cancel_flush_task()
        if self._merge_window_s <= 0:
            await self._flush_pending()
            return
        # self.create_task() (from BaseObject), not asyncio.create_task(): it is
        # tied to this processor's lifecycle and its pushes reach downstream
        # processors — the same mechanism the WebSocket audio input uses.
        # It needs a TaskManager, which only exists once the processor has been
        # set up inside a running pipeline; outside one (unit tests) fall back to
        # flushing inline so behaviour stays correct, just without merging.
        # Checked explicitly rather than via try/except so that genuine errors
        # raised while scheduling still propagate instead of being swallowed.
        if getattr(self, "_task_manager", None) is None:
            await self._flush_pending()
            return
        self._flush_task = self.create_task(
            self._flush_after_debounce(),
            name="remivox-transcript-merge",
        )

    async def _flush_after_debounce(self) -> None:
        try:
            await asyncio.sleep(self._merge_window_s)
        except asyncio.CancelledError:
            raise
        await self._flush_pending()

    async def _cancel_flush_task(self) -> None:
        task, self._flush_task = self._flush_task, None
        if task is not None:
            await self.cancel_task(task)

    async def _flush_pending(self) -> None:
        """Run one care turn over the buffered fragments and speak the reply."""
        if not self._pending_parts:
            return
        transcript = " ".join(self._pending_parts).strip()
        fragments = len(self._pending_parts)
        self._pending_parts = []
        language = self._pending_language
        if not transcript:
            return

        async with self._turn_lock:
            turn_started = time.perf_counter()
            if self.session_stats is not None:
                self.session_stats["turns_count"] = (
                    int(self.session_stats.get("turns_count", 0)) + 1
                )
            logger.info(
                f"🎤 PIPECAT_PROCESSOR: care turn over {fragments} fragment(s): "
                f"'{transcript[:80]}'"
            )

            response_text, intent_type = await self._run_care_turn(
                transcript,
                detected_language=language,
            )
            _log_turn_event(
                "remivox_turn_intent_detected",
                intent_type=intent_type,
                fragments_merged=fragments,
            )

            # TTSSpeakFrame, not TextFrame. TTSService only allocates a turn
            # context id on LLMFullResponseStartFrame, and there is no LLM in
            # this pipeline — so a bare TextFrame synthesises audio with no
            # context to hold it ("unable to append audio to context: no context
            # ID provided") and nothing reaches the WebSocket. TTSSpeakFrame
            # creates its own context, force-finalises the sentence (it has no
            # following token to confirm the boundary) and completes the
            # context. append_to_context=False because no LLM aggregator exists
            # here to receive it.
            await self.push_frame(
                TTSSpeakFrame(response_text, append_to_context=False)
            )
            _log_turn_event(
                "remivox_turn_response_sent",
                response_length=len(response_text),
            )
            _log_turn_event(
                "remivox_turn_latency",
                stt_to_response_ms=round(
                    (time.perf_counter() - turn_started) * 1000,
                    2,
                ),
            )

    async def _run_care_turn(
        self,
        transcript: str,
        *,
        detected_language: str,
    ) -> tuple[str, str]:
        try:
            user_uuid = await self._resolve_user_uuid()
            reminders, summaries = await asyncio.gather(
                list_patient_reminders(user_uuid),
                get_user_summaries(
                    user_uuid,
                    firebase_uid=self.firebase_uid,
                ),
            )
            result = await run_care_turn(
                user_uuid=user_uuid,
                text=transcript,
                language=detected_language or DEFAULT_REMIVOX_LANGUAGE,
                detected_language=detected_language,
                reminders=reminders,
                summaries=summaries,
                timezone_name=self.timezone,
                session_id=self.session_id,
                transcript=transcript,
            )
            response_text = (result.get("text") or "").strip()
            intent_type = str(result.get("intent") or "UNKNOWN")
            return response_text or self.FALLBACK_RESPONSE, intent_type
        except Exception as exc:
            logger.exception(
                json.dumps(
                    {
                        "event": "remivox_pipecat_processor_error",
                        "severity": "ERROR",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "session_id": self.session_id,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return self.FALLBACK_RESPONSE, "ERROR"

    async def _resolve_user_uuid(self) -> str:
        if self._user_uuid is None:
            self._user_uuid = await get_user_uuid(self.firebase_uid)
        return self._user_uuid

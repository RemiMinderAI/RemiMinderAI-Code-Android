"""
Care pipeline: Pulse STT → raw transcript → extract → validate → executor.

Pipecat is not in this stack. Hydra is not on the care path.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from services.remivox.intents.models import IntentResult, VoxIntent
from services.remivox.intents.router import route_intent
from services.remivox.intents.validate import validate_care_critical_fields
from services.remivox.pipeline import run_care_turn
from services.remivox.transcript import looks_redacted, preserve_raw_transcript


class RawTranscriptTests(unittest.TestCase):
    def test_preserves_medication_name(self):
        spoken = "Set a reminder for Metoprolol at 8 PM every day"
        self.assertEqual(preserve_raw_transcript(spoken), spoken)
        self.assertFalse(looks_redacted(spoken))

    def test_does_not_strip_inner_tokens(self):
        spoken = "  Metoprolol and Vitamin D  "
        self.assertEqual(preserve_raw_transcript(spoken), "Metoprolol and Vitamin D")

    def test_detects_placeholder_redaction(self):
        self.assertTrue(looks_redacted("Take [REDACTED] at 8 PM"))
        self.assertTrue(looks_redacted("reminder for ***"))


class ExtractOnRawTranscriptTests(unittest.TestCase):
    def test_metoprolol_extracted_from_raw_not_assist(self):
        result = route_intent(
            text="Set a reminder for Metoprolol at 8 PM every day",
            language="hi",
            normalized_text="Set a reminder for [REDACTED] at 8 PM every day",
        )
        self.assertEqual(result.intent, VoxIntent.CREATE_REMINDER)
        self.assertEqual(result.entities.get("medication"), "Metoprolol")
        self.assertEqual(result.raw_transcript, "Set a reminder for Metoprolol at 8 PM every day")

    def test_raw_name_wins_over_translated_assist(self):
        result = route_intent(
            text="Set Metoprolol reminder at 8 PM every day",
            normalized_text="Set a reminder for the medicine at 8 PM every day",
        )
        self.assertEqual(result.entities.get("medication"), "Metoprolol")


class CareValidationTests(unittest.TestCase):
    def test_create_requires_time_and_frequency(self):
        intent = IntentResult(
            intent=VoxIntent.CREATE_REMINDER,
            entities={"medication": "Metoprolol", "title": "Metoprolol"},
            raw_transcript="Set Metoprolol reminder",
        )
        gated = validate_care_critical_fields(intent)
        self.assertEqual(gated.intent_result.intent, VoxIntent.CLARIFY)
        self.assertIn("time", gated.missing_slots)
        self.assertIn("frequency", gated.missing_slots)

    def test_complete_create_passes(self):
        intent = IntentResult(
            intent=VoxIntent.CREATE_REMINDER,
            entities={
                "medication": "Metoprolol",
                "title": "Metoprolol",
                "time": "20:00",
                "frequency": "daily",
            },
            raw_transcript="Set Metoprolol reminder at 8 PM every day",
        )
        gated = validate_care_critical_fields(intent)
        self.assertEqual(gated.intent_result.intent, VoxIntent.CREATE_REMINDER)
        self.assertFalse(gated.missing_slots)

    def test_redacted_transcript_blocks_care_action(self):
        intent = IntentResult(
            intent=VoxIntent.CREATE_REMINDER,
            entities={
                "medication": "x",
                "title": "x",
                "time": "20:00",
                "frequency": "daily",
            },
            raw_transcript="Set [REDACTED] at 8 PM every day",
        )
        gated = validate_care_critical_fields(intent)
        self.assertEqual(gated.blocked_reason, "redacted_transcript")
        self.assertNotEqual(gated.intent_result.intent, VoxIntent.CREATE_REMINDER)


class PipelineOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_uses_raw_metoprolol_before_executor(self):
        with patch(
            "services.remivox.actions.executor.create_new_reminder",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = {"id": "r1", "title": "Metoprolol"}
            out = await run_care_turn(
                user_uuid="user-1",
                text="Set a reminder for Metoprolol at 8 PM every day",
                transcript="Set a reminder for Metoprolol at 8 PM every day",
                routing_text="Set a reminder for the medication at 8 PM every day",
                reminders={"today": [], "upcoming": [], "past": []},
                summaries=[],
            )
        self.assertEqual(out["intent"], VoxIntent.CREATE_REMINDER.value)
        self.assertEqual(out["entities"].get("medication"), "Metoprolol")
        mock_create.assert_awaited()
        kwargs = mock_create.await_args.args[0]
        self.assertEqual(kwargs.title, "Metoprolol")

    async def test_redacted_stt_does_not_create_reminder(self):
        with patch(
            "services.remivox.actions.executor.create_new_reminder",
            new_callable=AsyncMock,
        ) as mock_create:
            out = await run_care_turn(
                user_uuid="user-1",
                text="Set a reminder for [REDACTED] at 8 PM every day",
                reminders={"today": [], "upcoming": [], "past": []},
                summaries=[],
            )
        mock_create.assert_not_awaited()
        self.assertEqual(out.get("action"), "blocked")


class RouteNotPipecatTests(unittest.TestCase):
    def test_ask_path_uses_run_care_turn_on_raw_text(self):
        route_path = Path(__file__).resolve().parents[1] / "route" / "remivox.py"
        source = route_path.read_text(encoding="utf-8")
        self.assertNotIn("pipecat", source.lower())
        self.assertIn("transcript=intent_prompt", source)
        self.assertIn("routing_text=routing_text", source)
        tree = ast.parse(source)
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
        self.assertIn("run_care_turn", called)
        self.assertNotIn("handle_prompt", called)


if __name__ == "__main__":
    unittest.main()

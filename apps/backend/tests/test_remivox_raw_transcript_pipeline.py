"""Pipecat / Pulse care path: raw unredacted transcript before extraction."""

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


class PulsePlaceholderTests(unittest.TestCase):
    def test_metoprolol_is_not_redacted(self):
        spoken = "Set a reminder for Metoprolol at 8 PM every day"
        self.assertEqual(preserve_raw_transcript(spoken), spoken)
        self.assertFalse(looks_redacted(spoken))

    def test_pulse_companyname_placeholder_is_redacted(self):
        self.assertTrue(looks_redacted("Set a reminder for [COMPANYNAME_1] at 8 PM"))
        self.assertTrue(looks_redacted("Take [CITY_1] every day"))


class ExtractOnRawTests(unittest.TestCase):
    def test_raw_metoprolol_wins_over_masked_assist(self):
        result = route_intent(
            text="Set a reminder for Metoprolol at 8 PM every day",
            normalized_text="Set a reminder for [COMPANYNAME_1] at 8 PM every day",
        )
        self.assertEqual(result.intent, VoxIntent.CREATE_REMINDER)
        self.assertEqual(result.entities.get("medication"), "Metoprolol")


class CareValidationTests(unittest.TestCase):
    def test_pulse_placeholder_blocks_create(self):
        intent = IntentResult(
            intent=VoxIntent.CREATE_REMINDER,
            entities={
                "medication": "[COMPANYNAME_1]",
                "title": "[COMPANYNAME_1]",
                "time": "20:00",
                "frequency": "daily",
            },
            raw_transcript="Set a reminder for [COMPANYNAME_1] at 8 PM every day",
        )
        gated = validate_care_critical_fields(intent)
        self.assertEqual(gated.blocked_reason, "redacted_transcript")


class PipelineAndRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_uses_raw_metoprolol(self):
        with patch(
            "services.remivox.actions.executor.create_new_reminder",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = {"id": "r1", "title": "Metoprolol"}
            out = await run_care_turn(
                user_uuid="user-1",
                text="Set a reminder for Metoprolol at 8 PM every day",
                transcript="Set a reminder for Metoprolol at 8 PM every day",
                reminders={"today": [], "upcoming": [], "past": []},
                summaries=[],
            )
        self.assertEqual(out["intent"], VoxIntent.CREATE_REMINDER.value)
        self.assertEqual(out["entities"].get("medication"), "Metoprolol")
        mock_create.assert_awaited()
        self.assertEqual(mock_create.await_args.args[0].title, "Metoprolol")

    async def test_placeholder_does_not_create(self):
        with patch(
            "services.remivox.actions.executor.create_new_reminder",
            new_callable=AsyncMock,
        ) as mock_create:
            out = await run_care_turn(
                user_uuid="user-1",
                text="Set a reminder for [COMPANYNAME_1] at 8 PM every day",
                reminders={"today": [], "upcoming": [], "past": []},
                summaries=[],
            )
        mock_create.assert_not_awaited()
        self.assertEqual(out.get("action"), "blocked")


class PipecatRouteContractTests(unittest.TestCase):
    def test_stream_stt_forces_redact_pii_false(self):
        route_path = Path(__file__).resolve().parents[1] / "route" / "remivox.py"
        source = route_path.read_text(encoding="utf-8")
        self.assertIn("redact_pii=False", source)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "run_care_turn":
                keys = {kw.arg for kw in node.keywords}
                self.assertIn("transcript", keys)
                self.assertIn("routing_text", keys)


if __name__ == "__main__":
    unittest.main()

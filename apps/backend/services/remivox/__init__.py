"""
RemiVox v2 — backend-controlled, deterministic care assistant package.

Production care path (Pipecat transport + Pulse STT, never redacted):
  Pulse STT → RAW unredacted transcript
  → Intent + entity extraction
  → Validate care-critical fields
  → Action Executor → Reminder / care action

Hydra remains conversational only (never merged into the care pipeline).
Legacy handle_prompt lives under remivox.legacy for rollback only.

Layers:
  Voice      → voice.py
  Intent     → intents/
  Action     → actions/
  Response   → response/
  State      → state/
  Pipeline   → pipeline.py
  Legacy     → legacy/ (retired from production routes)
"""

from services.remivox.intents.models import (
    PROTECTED_ACTIONS,
    SUPPORTED_VOX_LANGUAGES,
    CancelActionEntities,
    ClarifyingEntities,
    CompleteReminderEntities,
    ConfirmActionEntities,
    CreateReminderEntities,
    IntentResult,
    SkipReminderEntities,
    SnoozeReminderEntities,
    UpdateReminderEntities,
    VoxIntent,
    VoxRecurrence,
)

__all__ = [
    "PROTECTED_ACTIONS",
    "SUPPORTED_VOX_LANGUAGES",
    "CancelActionEntities",
    "ClarifyingEntities",
    "CompleteReminderEntities",
    "ConfirmActionEntities",
    "CreateReminderEntities",
    "IntentResult",
    "SkipReminderEntities",
    "SnoozeReminderEntities",
    "UpdateReminderEntities",
    "VoxIntent",
    "VoxRecurrence",
]

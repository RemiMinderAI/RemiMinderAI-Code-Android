"""Runtime configuration for the RemiVox v3 streaming pipeline."""

from __future__ import annotations

import os


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int((os.getenv(name) or str(default)).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float((os.getenv(name) or str(default)).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


REMIVOX_PIPELINE = (os.getenv("REMIVOX_PIPELINE") or "legacy").strip().lower()

REMIVOX_STT_MODEL = (os.getenv("REMIVOX_STT_MODEL") or "pulse").strip()

# Care STT never redacts. Pulse treats medication names as PII
# ("Metoprolol" -> "[COMPANYNAME_1]"), which breaks the intent router.
# Env REMIVOX_STT_REDACT_PII is recorded for audit but ignored at the STT call.
REMIVOX_STT_REDACT_PII_REQUESTED = _env_bool("REMIVOX_STT_REDACT_PII", False)
REMIVOX_STT_REDACT_PII = False

# Smallest's WSS TTS validates the (model, voice) pair and rejects mismatches
# with "Invalid Voice ID". Verified against wss://api.smallest.ai/waves/v1/tts/live:
#   lightning_v3.1      + sophia -> OK      | + meher  -> Invalid Voice ID
#   lightning_v3.1_pro  + meher  -> OK      | + sophia -> "not available on ... _pro"
# Note this differs from the legacy HTTP API (lightning-v3.1 + meher), so the
# two pipelines are NOT interchangeable in their voice settings.
# Default to the _pro/meher pair: it is the combination that keeps the same
# voice users already hear from the legacy HTTP pipeline, and meher is one of
# the voices supporting word timestamps.
_TTS_MODEL_DEFAULT_VOICES = {
    "lightning_v3.1": "sophia",
    "lightning_v3.1_pro": "meher",
}

REMIVOX_TTS_MODEL = (
    os.getenv("REMIVOX_TTS_MODEL") or "lightning_v3.1_pro"
).strip()
REMIVOX_TTS_VOICE = (os.getenv("REMIVOX_TTS_VOICE") or "").strip()


def resolve_tts_voice(model: str, voice: str) -> str:
    """Return a voice valid for ``model``.

    An empty voice resolves to the model's default. A voice belonging to the
    other model is corrected rather than sent, because the WSS API rejects the
    whole synthesis request instead of falling back.
    """
    default = _TTS_MODEL_DEFAULT_VOICES.get(model)
    if default is None:
        # Unknown model: pass the configured voice through untouched.
        return voice
    if not voice:
        return default
    other = {v: k for k, v in _TTS_MODEL_DEFAULT_VOICES.items()}.get(voice)
    if other is not None and other != model:
        return default
    return voice


REMIVOX_TTS_VOICE = resolve_tts_voice(REMIVOX_TTS_MODEL, REMIVOX_TTS_VOICE)
REMIVOX_TTS_SPEED = _env_float("REMIVOX_TTS_SPEED", 0.85, minimum=0.1)
REMIVOX_EOU_TIMEOUT_MS = _env_int("REMIVOX_EOU_TIMEOUT_MS", 2000)

# Pulse sometimes splits one spoken utterance across several TranscriptionFrames
# ("Set a reminder for vitamin B12" / "at 10 AM every day"). Running the care
# engine on each fragment makes it ask clarifying questions instead of acting,
# so fragments are joined if they arrive within this window of one another.
# Measured from the most recent fragment, not from the first: it must exceed the
# gap between fragments of one utterance but stay below a natural pause between
# two genuinely separate commands.
REMIVOX_TRANSCRIPT_MERGE_MS = _env_int("REMIVOX_TRANSCRIPT_MERGE_MS", 900)
REMIVOX_KEYWORD_BOOST = _env_float(
    "REMIVOX_KEYWORD_BOOST",
    3.0,
    minimum=0.0,
)

REMIVOX_PING_INTERVAL_S = _env_int("REMIVOX_PING_INTERVAL_S", 30)
REMIVOX_SILENCE_TIMEOUT_S = _env_int("REMIVOX_SILENCE_TIMEOUT_S", 15)
REMIVOX_SESSION_MAX_S = _env_int("REMIVOX_SESSION_MAX_S", 300)

REMIVOX_INPUT_SAMPLE_RATE = 16000
REMIVOX_OUTPUT_SAMPLE_RATE = 24000
REMIVOX_INPUT_NUM_CHANNELS = 1
REMIVOX_AUDIO_BYTES_PER_SAMPLE = 2
REMIVOX_AUDIO_CHUNK_MS = 200


__all__ = [
    "REMIVOX_AUDIO_BYTES_PER_SAMPLE",
    "REMIVOX_AUDIO_CHUNK_MS",
    "REMIVOX_EOU_TIMEOUT_MS",
    "REMIVOX_INPUT_NUM_CHANNELS",
    "REMIVOX_INPUT_SAMPLE_RATE",
    "REMIVOX_KEYWORD_BOOST",
    "REMIVOX_OUTPUT_SAMPLE_RATE",
    "REMIVOX_PING_INTERVAL_S",
    "REMIVOX_PIPELINE",
    "REMIVOX_SILENCE_TIMEOUT_S",
    "REMIVOX_SESSION_MAX_S",
    "REMIVOX_STT_MODEL",
    "REMIVOX_STT_REDACT_PII",
    "REMIVOX_TTS_MODEL",
    "REMIVOX_TRANSCRIPT_MERGE_MS",
    "REMIVOX_TTS_SPEED",
    "REMIVOX_TTS_VOICE",
]

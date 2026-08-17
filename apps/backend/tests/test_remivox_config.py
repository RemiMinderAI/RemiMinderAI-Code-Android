import importlib
import os
import unittest
from unittest.mock import patch

import services.remivox.config as remivox_config
from services.remivox.languages import (
    DEFAULT_REMIVOX_LANGUAGE,
    HINDI_REMIVOX_LANGUAGE,
    SUPPORTED_LANGUAGES,
)


class RemiVoxConfigTests(unittest.TestCase):
    def test_default_streaming_configuration(self):
        try:
            with patch.dict(os.environ, {}, clear=True):
                config = importlib.reload(remivox_config)
                self.assertEqual("meher", config.REMIVOX_TTS_VOICE)
                self.assertEqual(0.85, config.REMIVOX_TTS_SPEED)
                self.assertEqual(2000, config.REMIVOX_EOU_TIMEOUT_MS)
                # lightning_v3.1_pro is the model that pairs with the "meher"
                # voice on the WSS API; lightning_v3.1 only accepts "sophia".
                self.assertEqual("lightning_v3.1_pro", config.REMIVOX_TTS_MODEL)
                # PII redaction must stay off: Pulse redacts medication names
                # ("pregabalin" -> "[CITY_1]"), which breaks the intent router.
                self.assertFalse(config.REMIVOX_STT_REDACT_PII)
                self.assertEqual(3.0, config.REMIVOX_KEYWORD_BOOST)
                self.assertEqual(30, config.REMIVOX_PING_INTERVAL_S)
                self.assertEqual(15, config.REMIVOX_SILENCE_TIMEOUT_S)
                self.assertEqual(300, config.REMIVOX_SESSION_MAX_S)
                self.assertEqual(16000, config.REMIVOX_INPUT_SAMPLE_RATE)
                self.assertEqual(24000, config.REMIVOX_OUTPUT_SAMPLE_RATE)
                self.assertEqual(200, config.REMIVOX_AUDIO_CHUNK_MS)
        finally:
            importlib.reload(remivox_config)

    def test_tts_voice_is_corrected_to_match_model(self):
        """A voice belonging to the other model is corrected, not sent.

        Smallest's WSS API rejects a mismatched (model, voice) pair outright
        with "Invalid Voice ID", failing every synthesis for the session, so an
        inherited env like lightning_v3.1 + meher must resolve to a valid pair.
        """
        cases = [
            # (env model, env voice, expected model, expected voice)
            ("lightning_v3.1", "meher", "lightning_v3.1", "sophia"),
            ("lightning_v3.1", "", "lightning_v3.1", "sophia"),
            ("lightning_v3.1_pro", "sophia", "lightning_v3.1_pro", "meher"),
            ("lightning_v3.1_pro", "meher", "lightning_v3.1_pro", "meher"),
            # An unknown model keeps whatever voice was configured.
            ("custom-model", "custom-voice", "custom-model", "custom-voice"),
        ]
        try:
            for model, voice, want_model, want_voice in cases:
                env = {"REMIVOX_TTS_MODEL": model}
                if voice:
                    env["REMIVOX_TTS_VOICE"] = voice
                with self.subTest(model=model, voice=voice):
                    with patch.dict(os.environ, env, clear=True):
                        config = importlib.reload(remivox_config)
                        self.assertEqual(want_model, config.REMIVOX_TTS_MODEL)
                        self.assertEqual(want_voice, config.REMIVOX_TTS_VOICE)
        finally:
            importlib.reload(remivox_config)

    def test_redact_pii_cannot_enable_care_stt_masking(self):
        try:
            with patch.dict(os.environ, {"REMIVOX_STT_REDACT_PII": "true"}, clear=True):
                config = importlib.reload(remivox_config)
                self.assertTrue(config.REMIVOX_STT_REDACT_PII_REQUESTED)
                self.assertFalse(config.REMIVOX_STT_REDACT_PII)
            with patch.dict(os.environ, {"REMIVOX_STT_REDACT_PII": "0"}, clear=True):
                config = importlib.reload(remivox_config)
                self.assertFalse(config.REMIVOX_STT_REDACT_PII)
                self.assertFalse(config.REMIVOX_STT_REDACT_PII_REQUESTED)
        finally:
            importlib.reload(remivox_config)

    def test_environment_overrides(self):
        overrides = {
            "REMIVOX_TTS_VOICE": "custom-voice",
            "REMIVOX_TTS_SPEED": "0.9",
            "REMIVOX_EOU_TIMEOUT_MS": "2500",
            "REMIVOX_TTS_MODEL": "custom-model",
            "REMIVOX_KEYWORD_BOOST": "4.5",
            "REMIVOX_PING_INTERVAL_S": "45",
            "REMIVOX_SILENCE_TIMEOUT_S": "20",
            "REMIVOX_SESSION_MAX_S": "420",
        }
        try:
            with patch.dict(os.environ, overrides, clear=False):
                config = importlib.reload(remivox_config)
                self.assertEqual("custom-voice", config.REMIVOX_TTS_VOICE)
                self.assertEqual(0.9, config.REMIVOX_TTS_SPEED)
                self.assertEqual(2500, config.REMIVOX_EOU_TIMEOUT_MS)
                self.assertEqual("custom-model", config.REMIVOX_TTS_MODEL)
                self.assertEqual(4.5, config.REMIVOX_KEYWORD_BOOST)
                self.assertEqual(45, config.REMIVOX_PING_INTERVAL_S)
                self.assertEqual(20, config.REMIVOX_SILENCE_TIMEOUT_S)
                self.assertEqual(420, config.REMIVOX_SESSION_MAX_S)
        finally:
            importlib.reload(remivox_config)

    def test_streaming_languages_are_canonical(self):
        self.assertEqual(
            {DEFAULT_REMIVOX_LANGUAGE, HINDI_REMIVOX_LANGUAGE},
            set(SUPPORTED_LANGUAGES),
        )


if __name__ == "__main__":
    unittest.main()

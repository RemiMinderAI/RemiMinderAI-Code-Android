import 'environment.dart';
import 'supported_languages.dart';

abstract final class VoxConfig {
  static const String streamPath = '/api/remivox/stream';
  static const String livePath = '/api/remivox/live';
  static const String todayPath = '/api/remivox/today';
  static const String askPath = '/api/remivox/ask';
  static const String askStreamPath = '/api/remivox/ask/stream';
  static const String availabilityPath = '/api/remivox/availability';
  static const String translateTurnPath = '/api/remivox/translate-turn';

  static const String defaultLanguage = kDefaultLanguageCode;
  static const String defaultTimezone = 'UTC';
  static const String defaultLiveMode = 'translate';
  static const String defaultTranslateTargetLanguage = 'bn';
  static const String wavContentType = 'audio/wav';

  static bool get usePipecatStream =>
      Environment.remivoxUsePipecatStream;
  static Duration get silenceTimeout => Duration(
    seconds: Environment.remivoxSilenceTimeoutSeconds,
  );

  static const int inputSampleRate = 16000;
  static const int outputSampleRate = 24000;
  static const int hydraOutputSampleRate = 48000;
  static const int inputChannels = 1;
  static const int bytesPerSample = 2;
  static const int chunkDurationMilliseconds = 200;
  static const int inputChunkBytes =
      inputSampleRate * bytesPerSample * chunkDurationMilliseconds ~/ 1000;

  /// Elderly users often start speaking a beat late.
  static const Duration initialListenTimeout = Duration(seconds: 10);
  static const Duration followUpListenTimeout = Duration(seconds: 15);
  /// Long enough to survive a mid-sentence pause without cutting the user off.
  static const Duration speechEndDebounce = Duration(milliseconds: 2000);
  static const Duration playbackChunkTimeout = Duration(seconds: 10);
  /// How long to wait for playback to finish before giving up on the
  /// completion event and letting the turn end.
  ///
  /// This is not a cosmetic bound: while it is short, the turn finishes while
  /// Vox is still talking, so the button returns to idle mid-sentence and the
  /// next tap starts the recorder over the top of the reply.
  ///
  /// Measured against the TTS voice actually in use (Lightning v3.1, olivia,
  /// speed 0.95): 160 chars -> 8.8s, 500 -> 23.9s, 891 -> 43.4s, 1300 -> 62.4s.
  /// That is ~20.8 characters per second, so the old 45s covered only about
  /// 940 characters. A visit summary read as overview + medications + next
  /// steps runs past that on any reasonably long visit — which is why it only
  /// showed up sometimes.
  ///
  /// The server caps a spoken summary at SPOKEN_SUMMARY_CHAR_LIMIT = 2500
  /// characters (services/remivox/summary_speech.py), so 2500 / 20.8 ~= 120s
  /// is the longest reply that can exist. 150s covers that with headroom.
  /// The timeout only matters when the completion event is missed, so a
  /// generous bound costs nothing in the normal case.
  static const Duration ttsCompletionTimeout = Duration(seconds: 150);
  static const Duration connectivityPollInterval = Duration(seconds: 3);
  static const Duration textResponseDelay = Duration(milliseconds: 900);

  /// Lowered so quieter voices still register as speech.
  static const int speechEnergyThreshold = 300;

  /// Mic amplitude (dBFS) above which we count the user as speaking.
  /// Phone-mic room silence sits near -50 dBFS; conversational speech is
  /// well above -40, so this still triggers for a quiet, frail voice.
  static const double speechAmplitudeThresholdDb = -40.0;

  /// How often the amplitude stream is sampled while recording a prompt.
  static const Duration amplitudePollInterval = Duration(milliseconds: 200);
  static const int maxCareAskTurns = 6;
}

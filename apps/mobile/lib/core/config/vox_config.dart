import 'environment.dart';
import 'supported_languages.dart';

abstract final class VoxConfig {
  static const String streamPath = '/api/remivox/stream';
  static const String livePath = '/api/remivox/live';
  static const String todayPath = '/api/remivox/today';
  static const String askPath = '/api/remivox/ask';
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
  static const Duration ttsCompletionTimeout = Duration(seconds: 45);
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

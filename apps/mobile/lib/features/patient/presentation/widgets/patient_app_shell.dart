import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../../../core/config/supported_languages.dart';
import '../../../../core/config/vox_config.dart';
import '../../../../core/providers/locale_provider.dart';
import '../../../../core/services/auth_service.dart';
import '../../../../core/services/backend_api_service.dart';
import '../../../../core/widgets/upgrade_prompt_sheet.dart';
import '../services/vox_live_session.dart';
import 'rounded_navigation_bar.dart';

/// App shell that wraps all patient screens with a floating bottom navigation bar
class PatientAppShell extends StatefulWidget {
  final Widget child;
  final NavigationItem currentItem;
  final Map<NavigationItem, String>? routes;

  const PatientAppShell({
    super.key,
    required this.child,
    required this.currentItem,
    this.routes,
  });

  @override
  State<PatientAppShell> createState() => _PatientAppShellState();
}

class _PatientAppShellState extends State<PatientAppShell> {
  @override
  Widget build(BuildContext context) {
    final bottomInset = MediaQuery.of(context).padding.bottom;

    return Scaffold(
      backgroundColor: Theme.of(context).scaffoldBackgroundColor,
      body: Stack(
        children: [
          Padding(
            padding: EdgeInsets.only(
              bottom: 70 + 12 + 16 + bottomInset,
            ),
            child: widget.child,
          ),

          if (widget.routes == null && widget.currentItem.index == 0) // Vox re-enabled for testing
            Positioned(
              right: 56,
              bottom: bottomInset + 102,
              child: const _VoxFloatingButton(),
            ),

          // Floating navigation bar
          Positioned(
            left: 0,
            right: 0,
            bottom: bottomInset,
            child: RoundedNavigationBar(
              currentItem: widget.currentItem,
              routes: widget.routes,
            ),
          ),
        ],
      ),
    );
  }
}

class _VoxFloatingButton extends StatelessWidget {
  const _VoxFloatingButton();

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: 'Vox',
      child: const _VoxButtonBody(),
    );
  }
}

class _VoxButtonBody extends StatefulWidget {
  const _VoxButtonBody();

  @override
  State<_VoxButtonBody> createState() => _VoxButtonBodyState();
}

class _VoxButtonBodyState extends State<_VoxButtonBody> {
  static const Duration _initialListenFor =
      VoxConfig.initialListenTimeout;
  static const Duration _followUpListenFor =
      VoxConfig.followUpListenTimeout;
  static final Duration _pipecatSilenceTimeout =
      VoxConfig.silenceTimeout;
  static const int _pipecatMicChunkBytes = VoxConfig.inputChunkBytes;

  final AudioPlayer _player = AudioPlayer();
  /// Separate from [_player] so the acknowledgement cue can never interrupt or
  /// be interrupted by the spoken response.
  final AudioPlayer _cuePlayer = AudioPlayer();
  bool _voxRecording = false;
  /// Server-controlled: the kill switch or an exhausted quota hides the button.
  /// Starts true so a slow first fetch does not flash the FAB out of existence.
  bool _voxAvailable = true;
  String? _voxUnavailableReason;
  String? _voxUnavailableMessage;
  bool _voxUpgradeAvailable = false;
  bool _voxThinking = false;
  final AudioRecorder _recorder = AudioRecorder();
  final BackendApiService _backendApi = BackendApiService();
  bool _usePipecatStream = VoxConfig.usePipecatStream;
  bool _busy = false;
  bool _liveActive = false;
  bool _pipecatActive = false;
  bool _pipecatSpeaking = false;
  bool _pipecatPlaying = false;
  final BytesBuilder _pipecatAudioBuffer = BytesBuilder();
  Timer? _pipecatPlaybackDebounce;
  bool _pipecatWaiting = false;
  bool _handlingPipecatDisconnect = false;
  bool _awaitingFollowUp = false;
  VoxLiveSession? _live;
  String? _voxSessionId;
  DateTime? _voxSessionStartedAt;
  /// Backend conversation state (pending confirmations) lives for 5 minutes.
  static const Duration _voxSessionTtl = Duration(minutes: 5);
  StreamSubscription<Uint8List>? _pipecatMicSubscription;
  StreamSubscription<Uint8List>? _pipecatAudioSubscription;
  Timer? _pipecatSilenceTimer;
  Timer? _pipecatSpeechResetTimer;
  final BytesBuilder _pipecatMicBuffer = BytesBuilder(copy: false);
  Future<void> _pipecatPlaybackQueue = Future<void>.value();
  Completer<void>? _pipecatPlaybackInterrupted;
  int _pipecatPlaybackGeneration = 0;
  int _pipecatQueuedAudioChunks = 0;

  bool get _voiceLiveActive => _liveActive || _pipecatActive;
  String get _voxStatusTop {
    if (_pipecatWaiting) return 'Thinking';
    if (_pipecatActive) return 'Listening';
    if (_liveActive) return 'Live';
    if (_voxRecording) return 'Listening';
    if (_voxThinking) return 'Thinking';
    if (_awaitingFollowUp) return 'Wait';
    return 'Ask';
  }

  String get _voxStatusBottom {
    if (_pipecatWaiting) return 'Please wait';
    if (_voiceLiveActive) return 'Tap stop';
    if (_voxRecording) return 'speak now';
    if (_voxThinking) return 'one moment';
    if (_awaitingFollowUp) return 'reply';
    return 'me';
  }

  @override
  @override
  void initState() {
    super.initState();
    // Fire and forget: the button renders immediately and corrects itself if
    // the server says Vox is off or the allowance is spent.
    unawaited(_refreshVoxAvailability());
  }

  Future<void> _refreshVoxAvailability() async {
    final info = await BackendApiService().fetchVoxAvailability();
    if (!mounted) return;
    setState(() {
      _voxAvailable = info['available'] != false;
      _voxUnavailableReason = info['reason']?.toString();
      _voxUnavailableMessage = info['message']?.toString();
      _voxUpgradeAvailable = info['upgrade_available'] == true;
    });
  }

  /// Trial exhausted is a conversion moment; a paying customer at their monthly
  /// cap must never be shown a paywall — there is nothing for them to buy.
  Future<void> _handleVoxUnavailable() async {
    final message = _voxUnavailableMessage ??
        'Vox is not available right now.';
    if (_voxUpgradeAvailable) {
      await showUpgradePromptSheet(
        context,
        reason: UpgradePromptReason.trialExpired,
        screen: 'patient_shell_vox_quota',
      );
      return;
    }
    _snack(message, duration: const Duration(seconds: 6));
  }

  void dispose() {
    _disposeCuePlayer();
    _pipecatSilenceTimer?.cancel();
    _pipecatSpeechResetTimer?.cancel();
    final micSubscription = _pipecatMicSubscription;
    if (micSubscription != null) {
      unawaited(micSubscription.cancel());
    }
    final audioSubscription = _pipecatAudioSubscription;
    if (audioSubscription != null) {
      unawaited(audioSubscription.cancel());
    }
    unawaited(_backendApi.closeVoxStream());
    _live?.dispose();
    _player.dispose();
    _recorder.dispose();
    super.dispose();
  }

  Future<String> _timezone() async {
    try {
      return await FlutterTimezone.getLocalTimezone();
    } catch (_) {
      return VoxConfig.defaultTimezone;
    }
  }

  Future<String> _preferredLanguage() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      return normalizeLanguageCode(
        prefs.getString(kPreferredLanguagePrefsKey) ??
            kDefaultLanguageCode,
      );
    } catch (_) {
      return kDefaultLanguageCode;
    }
  }

  void _snack(String message, {Duration? duration}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        duration: duration ?? const Duration(seconds: 4),
      ),
    );
  }

  /// Keeps the listening notice on screen for the whole recording window.
  void _snackListening(String message, Duration listenFor) {
    _snack(message, duration: listenFor + const Duration(seconds: 1));
  }

  String _ensureSessionId() {
    final existing = _voxSessionId?.trim();
    final startedAt = _voxSessionStartedAt;
    final stillFresh = startedAt != null &&
        DateTime.now().difference(startedAt) < _voxSessionTtl;
    if (existing != null && existing.isNotEmpty && stillFresh) return existing;
    final created =
        'vox_${DateTime.now().millisecondsSinceEpoch}_${UniqueKey().hashCode.abs()}';
    _voxSessionId = created;
    _voxSessionStartedAt = DateTime.now();
    return created;
  }

  Map<String, dynamic>? _pendingFromResponse(Map<String, dynamic> data) {
    final top = data['pending'];
    if (top is Map && top['intent'] != null) {
      return Map<String, dynamic>.from(top);
    }
    final payload = data['action_payload'];
    if (payload is Map) {
      final nested = payload['pending'];
      if (nested is Map && nested['intent'] != null) {
        return Map<String, dynamic>.from(nested);
      }
    }
    return null;
  }

  Future<void> _handleTap() async {
    if (!_voxAvailable) {
      await _handleVoxUnavailable();
      return;
    }
    if (_pipecatActive) {
      await _stopPipecatStream();
      return;
    }
    if (_liveActive) {
      await _stopLive();
      return;
    }
    if (_busy) return;

    if (_usePipecatStream) {
      setState(() => _busy = true);
      try {
        await _startPipecatStream();
        return;
      } catch (e) {
        debugPrint('VOX_STREAM_ERROR: $e');
        await _stopPipecatStream(showStatus: false);
        if (mounted) {
          setState(() => _busy = false);
          _snack('Live Vox unavailable. Using standard Vox.');
        }
      }
    }

    setState(() => _busy = true);
    try {
      await _runCareAskLoop(isFollowUp: false);
    } catch (e) {
      final message = e.toString();
      debugPrint('VOX_ERROR: $message');
      if (!mounted) return;
      if (message.toLowerCase().contains('premium') ||
          message.toLowerCase().contains('trial')) {
        await showUpgradePromptSheet(
          context,
          reason: UpgradePromptReason.trialExpired,
          screen: 'patient_shell',
        );
      } else {
        _snack(message);
      }
      // A refusal may mean the allowance just ran out — re-read availability so
      // the button reflects it without needing an app restart.
      unawaited(_refreshVoxAvailability());
      // Keep the session id when a clarification/confirmation is still pending.
      _clearPendingSession(keepSession: _awaitingFollowUp);
    } finally {
      if (mounted && !_voiceLiveActive) {
        setState(() {
          _busy = false;
          _awaitingFollowUp = false;
        });
      }
    }
  }

  Future<void> _startPipecatStream() async {
    final token = await AuthService().getAccessToken();
    if (token == null || token.isEmpty) {
      throw Exception('Authentication required. Please log in again.');
    }
    if (!await _recorder.hasPermission()) {
      throw Exception('Microphone permission is required for Vox.');
    }

    final preferredLanguage = await _preferredLanguage();
    final language = normalizeVoxLanguageCode(preferredLanguage);
    _pipecatMicBuffer.takeBytes();
    _pipecatQueuedAudioChunks = 0;
    _pipecatWaiting = false;
    _pipecatPlaying = false;
    await _backendApi.connectVoxStream(token: token, language: language);

    _pipecatAudioSubscription = _backendApi.voxAudioStream.listen(
      _handlePipecatAudioChunk,
      onError: (Object error) {
        debugPrint('VOX_STREAM_AUDIO_ERROR: $error');
        unawaited(_handlePipecatConnectionLost());
      },
      onDone: () {
        unawaited(_handlePipecatConnectionLost());
      },
    );

    final micStream = await _recorder.startStream(
      const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: VoxConfig.inputSampleRate,
        numChannels: VoxConfig.inputChannels,
      ),
    );

    if (!mounted) {
      await _backendApi.closeVoxStream();
      return;
    }
    setState(() {
      _pipecatActive = true;
      _busy = false;
    });
    _snack('Vox is listening... tap Vox to stop.');
    _resetPipecatTimeout();

    _pipecatMicSubscription = micStream.listen(
      _handlePipecatMicChunk,
      onError: (Object error) {
        debugPrint('VOX_STREAM_MIC_ERROR: $error');
        unawaited(_handlePipecatConnectionLost());
      },
    );
  }

  void _handlePipecatMicChunk(Uint8List chunk) {
    if (!_pipecatActive || chunk.isEmpty) return;
    _pipecatMicBuffer.add(chunk);
    final buffered = _pipecatMicBuffer.takeBytes();
    var offset = 0;
    while (buffered.length - offset >= _pipecatMicChunkBytes) {
      _backendApi.sendAudioChunk(
        Uint8List.fromList(
          buffered.sublist(offset, offset + _pipecatMicChunkBytes),
        ),
      );
      offset += _pipecatMicChunkBytes;
    }
    if (offset < buffered.length) {
      _pipecatMicBuffer.add(buffered.sublist(offset));
    }

    if (_pcmChunkHasSpeech(chunk)) {
      _resetPipecatTimeout();
      if (!_pipecatSpeaking) {
        _pipecatSpeaking = true;
        _pipecatPlaybackGeneration++;
        _pipecatPlaying = false;
        _pipecatWaiting = false;
        final interrupted = _pipecatPlaybackInterrupted;
        if (interrupted != null && !interrupted.isCompleted) {
          interrupted.complete();
        }
        if (mounted) {
          setState(() => _busy = false);
        }
        unawaited(_player.stop());
      }
      _pipecatSpeechResetTimer?.cancel();
      _pipecatSpeechResetTimer = Timer(
        VoxConfig.speechEndDebounce,
        () {
          _pipecatSpeaking = false;
    _pipecatPlaybackDebounce?.cancel();
    _pipecatPlaybackDebounce = null;
    _pipecatAudioBuffer.clear();
          if (_pipecatActive && !_pipecatPlaying && mounted) {
            setState(() {
              _pipecatWaiting = true;
              _busy = true;
            });
          }
        },
      );
    }
  }

  void _handlePipecatAudioChunk(Uint8List chunk) {
    if (!_pipecatActive || chunk.isEmpty) return;
    _pipecatSilenceTimer?.cancel();
    _pipecatWaiting = false;
    if (mounted) {
      setState(() => _busy = false);
    }
    final generation = _pipecatPlaybackGeneration;
    _pipecatAudioBuffer.add(chunk);
    _pipecatPlaybackDebounce?.cancel();
    _pipecatPlaybackDebounce = Timer(
      const Duration(milliseconds: 300),
      () {
        if (!_pipecatActive || generation != _pipecatPlaybackGeneration) {
          _pipecatAudioBuffer.clear();
          return;
        }
        final buffered = _pipecatAudioBuffer.takeBytes();
        if (buffered.isEmpty) return;
        _pipecatPlaybackQueue = _pipecatPlaybackQueue.then((_) async {
          try {
            _pipecatPlaying = true;
            await _playPipecatPcmChunk(buffered);
          } finally {
            _pipecatPlaying = false;
            if (_pipecatActive) {
              _pipecatWaiting = false;
              if (mounted) {
                setState(() => _busy = false);
              }
              _resetPipecatTimeout();
            }
          }
        });
      },
    );
  }

  void _resetPipecatTimeout() {
    _pipecatSilenceTimer?.cancel();
    _pipecatSilenceTimer = Timer(_pipecatSilenceTimeout, () {
      unawaited(_stopPipecatStream());
    });
  }

  bool _pcmChunkHasSpeech(
    Uint8List bytes, {
    int threshold = VoxConfig.speechEnergyThreshold,
  }) {
    for (var i = 0; i + 1 < bytes.length; i += 4) {
      final sample = bytes[i] | (bytes[i + 1] << 8);
      final signed = sample > 32767 ? sample - 65536 : sample;
      if (signed.abs() >= threshold) return true;
    }
    return false;
  }

  Future<void> _playPipecatPcmChunk(Uint8List pcm) async {
    final wav = _pcm16ToWav(
      pcm,
      sampleRate: VoxConfig.outputSampleRate,
    );
    final done = _player.onPlayerComplete.first;
    final interrupted = Completer<void>();
    _pipecatPlaybackInterrupted = interrupted;
    _pipecatPlaying = true;
    _pipecatWaiting = false;
    if (mounted) {
      setState(() => _busy = false);
    }
    await _player.play(
      BytesSource(wav, mimeType: VoxConfig.wavContentType),
    );
    try {
      await Future.any<void>([
        done,
        interrupted.future,
      ]).timeout(VoxConfig.playbackChunkTimeout);
    } on TimeoutException {
      await _player.stop();
    } finally {
      if (identical(_pipecatPlaybackInterrupted, interrupted)) {
        _pipecatPlaybackInterrupted = null;
      }
    }
  }

  Future<void> _handlePipecatConnectionLost() async {
    if (_handlingPipecatDisconnect) return;
    _handlingPipecatDisconnect = true;
    try {
      await _stopPipecatStream(showStatus: false);
      if (mounted) {
        _snack('Connection lost. Tap Vox to reconnect.');
      }
    } finally {
      _handlingPipecatDisconnect = false;
    }
  }

  Future<void> _stopPipecatStream({bool showStatus = true}) async {
    final wasActive = _pipecatActive;
    final remainingAudio = _pipecatMicBuffer.takeBytes();
    if (wasActive && remainingAudio.isNotEmpty) {
      try {
        _backendApi.sendAudioChunk(remainingAudio);
      } catch (_) {}
    }
    _pipecatActive = false;
    _pipecatPlaying = false;
    _pipecatWaiting = false;
    _pipecatQueuedAudioChunks = 0;
    _pipecatPlaybackGeneration++;
    final interrupted = _pipecatPlaybackInterrupted;
    if (interrupted != null && !interrupted.isCompleted) {
      interrupted.complete();
    }
    _pipecatSilenceTimer?.cancel();
    _pipecatSilenceTimer = null;
    _pipecatSpeechResetTimer?.cancel();
    _pipecatSpeechResetTimer = null;
    _pipecatSpeaking = false;

    await _pipecatMicSubscription?.cancel();
    _pipecatMicSubscription = null;
    await _pipecatAudioSubscription?.cancel();
    _pipecatAudioSubscription = null;
    try {
      if (await _recorder.isRecording()) {
        await _recorder.stop();
      }
    } catch (_) {}
    await _backendApi.closeVoxStream();
    await _player.stop();

    if (mounted) {
      setState(() => _busy = false);
      if (showStatus && wasActive) _snack('Talk to me anytime!');
    }
  }

  Uint8List _pcm16ToWav(Uint8List pcm, {required int sampleRate}) {
    final header = BytesBuilder();

    void writeString(String value) => header.add(ascii.encode(value));
    void writeUint32(int value) {
      header.add([
        value & 0xff,
        (value >> 8) & 0xff,
        (value >> 16) & 0xff,
        (value >> 24) & 0xff,
      ]);
    }

    void writeUint16(int value) {
      header.add([value & 0xff, (value >> 8) & 0xff]);
    }

    writeString('RIFF');
    writeUint32(36 + pcm.length);
    writeString('WAVE');
    writeString('fmt ');
    writeUint32(16);
    writeUint16(1);
    writeUint16(1);
    writeUint32(sampleRate);
    writeUint32(sampleRate * 2);
    writeUint16(2);
    writeUint16(16);
    writeString('data');
    writeUint32(pcm.length);
    return Uint8List.fromList([...header.toBytes(), ...pcm]);
  }

  /// Sequential POST /api/remivox/ask turns. Reopens mic while [pending] is set.
  Future<void> _runCareAskLoop({required bool isFollowUp}) async {
    final tz = await _timezone();
    final lang = await _preferredLanguage();
    var sessionId = _ensureSessionId();

    final listenFor = isFollowUp ? _followUpListenFor : _initialListenFor;
    _snackListening(
      isFollowUp
          ? 'Vox is still listening…'
          : 'Vox is listening… speak in English or Hindi',
      listenFor,
    );
    if (isFollowUp && mounted) {
      setState(() => _awaitingFollowUp = true);
    }

    if (mounted) setState(() => _voxRecording = true);
    final initialClip = await _recordPromptClip(
      listenFor: listenFor,
      requireSpeech: isFollowUp,
    );
    if (mounted) setState(() => _voxRecording = false);
    if (initialClip != null) {
      // Recording has stopped and the network round-trip is about to begin —
      // this is the exact moment the silence used to start.
      _playAckTone();
      if (mounted) setState(() => _voxThinking = true);
    }

    if (initialClip == null) {
      if (isFollowUp) {
        // A pending confirmation may still be waiting server-side.
        _clearPendingSession(keepSession: true);
        _snack('No worries, you can try again anytime');
        return;
      }
      final briefing = await BackendApiService().getVoxTodayBriefing(
        replyLanguage: lang,
      );
      await _playResponse(briefing);
      _clearPendingSession();
      return;
    }

    var clip = initialClip;
    for (var turn = 0; turn < VoxConfig.maxCareAskTurns; turn++) {
      // Prefer the streaming turn so speech starts as soon as Lightning emits
      // its first bytes. Any failure falls back to /ask, which is unchanged.
      Map<String, dynamic> data;
      VoxStreamTurn? streamed;
      try {
        // Streaming TTS playback produces distorted audio on device —
        // MediaPlayer is created/torn down ~15x per reply, suggesting
        // segments overlap rather than queue. Backend streaming works
        // (200 OK, TTFB 382ms); the defect is client-side playback.
        // Flip to true once playback queueing is fixed.
        const bool kVoxStreamingEnabled = false;
        if (!kVoxStreamingEnabled) {
          throw StateError('streaming disabled pending playback fix');
        }
        streamed = await BackendApiService().askVoxStream(
          null,
          replyLanguage: lang,
          timezone: tz,
          audioBase64: clip.base64,
          contentType: clip.contentType,
          autoDetectLanguage: true,
          sessionId: sessionId,
        );
        data = streamed.data;
      } catch (e) {
        debugPrint('VOX_STREAM_FALLBACK: $e');
        streamed = null;
        data = await BackendApiService().askVox(
          null,
          replyLanguage: lang,
          timezone: tz,
          audioBase64: clip.base64,
          contentType: clip.contentType,
          autoDetectLanguage: true,
          sessionId: sessionId,
        );
      }

      final returnedSession = data['session_id']?.toString().trim();
      if (returnedSession != null && returnedSession.isNotEmpty) {
        sessionId = returnedSession;
        _voxSessionId = returnedSession;
        _voxSessionStartedAt = DateTime.now();
      }

      // Wait for TTS to finish before reopening the mic.
      if (streamed != null) {
        await _playResponse(data, streamed: streamed);
      } else {
        await _playResponse(data);
      }

      final action = data['action']?.toString();
      if (action == 'start_live_translate') {
        _clearPendingSession();
        final detected = normalizeLanguageCode(
          data['detected_language']?.toString() ?? lang,
        );
        final payload = data['action_payload'];
        final target = payload is Map
            ? normalizeLanguageCode(
                payload['target_language']?.toString() ?? detected,
              )
            : detected;
        await _startLiveInPlace(
          sourceLanguage: detected,
          targetLanguage: target == detected
              ? (detected == kDefaultLanguageCode
                    ? VoxConfig.defaultTranslateTargetLanguage
                    : kDefaultLanguageCode)
              : target,
        );
        return;
      }

      final pending = _pendingFromResponse(data);
      if (pending == null) {
        _clearPendingSession();
        return;
      }

      // Clarification needed — keep session_id and auto-reopen mic.
      if (mounted) setState(() => _awaitingFollowUp = true);
      _snackListening('Vox is still listening…', _followUpListenFor);
      if (mounted) setState(() => _voxRecording = true);
      final followUp = await _recordPromptClip(
        listenFor: _followUpListenFor,
        requireSpeech: true,
      );
      if (mounted) setState(() => _voxRecording = false);
      if (followUp != null) {
        _playAckTone();
        if (mounted) setState(() => _voxThinking = true);
      }
      if (followUp == null) {
        // Server still holds this pending action; keep the session so the next
        // Vox press can answer it instead of starting a fresh conversation.
        _clearPendingSession(keepSession: true);
        _snack('No worries, you can try again anytime');
        return;
      }
      clip = followUp;
    }

    _clearPendingSession();
  }

  /// Ends the local turn loop.
  ///
  /// [keepSession] preserves `_voxSessionId` so the next Vox press rejoins the
  /// same backend conversation — required whenever the server still has a
  /// pending confirmation (e.g. "Should I delete your Pregabalin reminder?")
  /// and the mic closed before the user answered.
  void _clearPendingSession({bool keepSession = false}) {
    _voxRecording = false;
    _voxThinking = false;
    if (!keepSession) {
      _voxSessionId = null;
      _voxSessionStartedAt = null;
    }
    _awaitingFollowUp = false;
  }

  Future<void> _playResponse(
    Map<String, dynamic> data, {
    VoxStreamTurn? streamed,
  }) async {
    if (mounted) setState(() => _voxThinking = false);
    if (streamed != null) {
      final detected = data['detected_language']?.toString();
      if (detected != null && detected.isNotEmpty) {
        _snack('Heard ${languageLabel(normalizeLanguageCode(detected))}');
      }
      try {
        await _playStreamedAudio(streamed.audio, streamed.sampleRate);
        return;
      } catch (e) {
        debugPrint('VOX_STREAM_PLAYBACK_ERROR: $e');
        // Fall through to the text path below rather than losing the reply.
      }
    }
    final text =
        data['text']?.toString() ?? 'Vox has nothing to read right now.';
    final audio = data['audio_base64'] as String?;
    final detected = data['detected_language']?.toString();
    if (detected != null && detected.isNotEmpty) {
      _snack('Heard ${languageLabel(normalizeLanguageCode(detected))}');
    }
    if (audio != null && audio.isNotEmpty) {
      await _playAudio(base64Decode(audio));
    } else {
      _snack(text);
      // Brief pause so the user can read the clarification before mic reopens.
      await Future<void>.delayed(VoxConfig.textResponseDelay);
    }
  }

  /// True when 16-bit mono WAV PCM has enough energy to count as speech.
  bool _wavHasSpeech(
    Uint8List bytes, {
    int threshold = VoxConfig.speechEnergyThreshold,
  }) {
    if (bytes.length <= 44) return false;
    var peak = 0;
    // Skip 44-byte WAV header; sample every other frame for speed.
    for (var i = 44; i + 1 < bytes.length; i += 4) {
      final sample = bytes[i] | (bytes[i + 1] << 8);
      final signed = sample > 32767 ? sample - 65536 : sample;
      final abs = signed.abs();
      if (abs > peak) peak = abs;
      if (peak >= threshold) return true;
    }
    return false;
  }

  /// Records until the user stops talking, or [maxDuration] elapses.
  ///
  /// Waiting out the full window cut people off mid-sentence and made short
  /// answers ("yes") feel broken. We watch mic amplitude instead: once speech
  /// has been heard, [VoxConfig.speechEndDebounce] of quiet ends the turn.
  /// If the amplitude stream is unavailable we fall back to the fixed window.
  Future<void> _waitForSpeechEnd({required Duration maxDuration}) async {
    final done = Completer<void>();
    Timer? silenceTimer;
    StreamSubscription<Amplitude>? amplitudes;
    var heardSpeech = false;

    void finish() {
      if (!done.isCompleted) done.complete();
    }

    final hardStop = Timer(maxDuration, finish);
    try {
      amplitudes = _recorder
          .onAmplitudeChanged(VoxConfig.amplitudePollInterval)
          .listen(
            (amplitude) {
              if (amplitude.current > VoxConfig.speechAmplitudeThresholdDb) {
                heardSpeech = true;
                silenceTimer?.cancel();
                silenceTimer = null;
                return;
              }
              if (heardSpeech && silenceTimer == null) {
                silenceTimer = Timer(VoxConfig.speechEndDebounce, finish);
              }
            },
            onError: (_) {},
          );
    } catch (_) {
      // No amplitude stream on this device — the hard stop still applies.
    }

    await done.future;
    hardStop.cancel();
    silenceTimer?.cancel();
    await amplitudes?.cancel();
  }

  Future<({String base64, String contentType})?> _recordPromptClip({
    Duration listenFor = _initialListenFor,
    bool requireSpeech = false,
  }) async {
    try {
      final allowed = await _recorder.hasPermission();
      if (!allowed) return null;
      final dir = await getTemporaryDirectory();
      final path =
          '${dir.path}/vox_${DateTime.now().millisecondsSinceEpoch}.wav';
      await _recorder.start(
        const RecordConfig(
          encoder: AudioEncoder.wav,
          sampleRate: VoxConfig.inputSampleRate,
          numChannels: VoxConfig.inputChannels,
        ),
        path: path,
      );
      await _waitForSpeechEnd(maxDuration: listenFor);
      final saved = await _recorder.stop();
      final filePath = saved ?? path;
      final file = File(filePath);
      if (!await file.exists()) return null;
      final bytes = await file.readAsBytes();
      if (bytes.isEmpty) return null;
      try {
        await file.delete();
      } catch (_) {}
      if (requireSpeech && !_wavHasSpeech(Uint8List.fromList(bytes))) {
        return null;
      }
      return (
        base64: base64Encode(bytes),
        contentType: VoxConfig.wavContentType,
      );
    } catch (_) {
      try {
        if (await _recorder.isRecording()) await _recorder.stop();
      } catch (_) {}
      return null;
    }
  }

  Future<void> _startLiveInPlace({
    required String sourceLanguage,
    required String targetLanguage,
  }) async {
    final session = VoxLiveSession(
      sourceLanguage: sourceLanguage,
      targetLanguage: targetLanguage,
      mode: VoxConfig.defaultLiveMode,
      timezone: await _timezone(),
    );
    setState(() {
      _live = session;
      _liveActive = true;
      _busy = true;
    });
    _snack(
      'Vox live on (${languageLabel(sourceLanguage)} ↔ '
      '${languageLabel(targetLanguage)}). Tap Vox to stop.',
    );
    try {
      await session.start(
        onStatus: (s) {
          if (mounted) _snack(s);
        },
        onError: (m) {
          if (mounted) _snack(m);
        },
      );
    } catch (e) {
      _snack(e.toString());
      await _stopLive();
    }
  }

  Future<void> _stopLive() async {
    await _live?.stop();
    _live = null;
    if (mounted) {
      setState(() {
        _liveActive = false;
        _busy = false;
      });
      _snack('Vox stopped.');
    }
  }

  /// A short two-note cue, generated rather than bundled so there is no asset
  /// to ship or localise.
  ///
  /// Played the instant recording stops. It does not make anything faster — it
  /// replaces ambiguous silence with "I heard you", which is what stops an
  /// elderly user repeating themselves into the next recording.
  static Uint8List _buildAckTone() {
    const sampleRate = 16000;
    const notes = <double>[587.33, 783.99]; // D5 then G5 — soft, rising
    const noteMs = 90;
    const gain = 0.18; // deliberately quiet; this is a nudge, not an alert
    final samplesPerNote = sampleRate * noteMs ~/ 1000;

    final data = BytesBuilder();
    for (var n = 0; n < notes.length; n++) {
      for (var i = 0; i < samplesPerNote; i++) {
        // Raised-cosine envelope: without it the note edges click.
        final envelope = 0.5 * (1 - math.cos(2 * math.pi * i / samplesPerNote));
        final value = math.sin(2 * math.pi * notes[n] * i / sampleRate) *
            envelope *
            gain;
        final sample = (value * 32767).round().clamp(-32768, 32767);
        data.addByte(sample & 0xFF);
        data.addByte((sample >> 8) & 0xFF);
      }
    }
    final pcm = data.takeBytes();

    final header = BytesBuilder();
    void str(String v) => header.add(v.codeUnits);
    void u32(int v) => header.add([
          v & 0xFF,
          (v >> 8) & 0xFF,
          (v >> 16) & 0xFF,
          (v >> 24) & 0xFF,
        ]);
    void u16(int v) => header.add([v & 0xFF, (v >> 8) & 0xFF]);
    str('RIFF');
    u32(36 + pcm.length);
    str('WAVE');
    str('fmt ');
    u32(16);
    u16(1); // PCM
    u16(1); // mono
    u32(sampleRate);
    u32(sampleRate * 2);
    u16(2);
    u16(16);
    str('data');
    u32(pcm.length);
    // Spreading into a List<int> boxes every byte — 310k allocations for a
    // long reply. Build the buffer directly instead.
    final head = header.takeBytes();
    final out = Uint8List(head.length + pcm.length);
    out.setRange(0, head.length, head);
    out.setRange(head.length, out.length, pcm);
    return out;
  }

  static final Uint8List _ackTone = _buildAckTone();

  /// Fire-and-forget: the turn must never wait on the cue.
  void _playAckTone() {
    unawaited(() async {
      try {
        await _cuePlayer.stop();
        await _cuePlayer.play(BytesSource(_ackTone, mimeType: 'audio/wav'));
      } catch (_) {
        // A missing cue is cosmetic — never let it break the turn.
      }
    }());
  }

  void _disposeCuePlayer() {
    unawaited(_cuePlayer.dispose());
  }

  /// Wraps raw PCM in a WAV header so it can be handed to the player as a
  /// self-contained segment.
  static Uint8List _wavFromPcm(Uint8List pcm, int sampleRate) {
    final header = BytesBuilder();
    void str(String v) => header.add(v.codeUnits);
    void u32(int v) => header.add(
        [v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF]);
    void u16(int v) => header.add([v & 0xFF, (v >> 8) & 0xFF]);
    str('RIFF');
    u32(36 + pcm.length);
    str('WAVE');
    str('fmt ');
    u32(16);
    u16(1);
    u16(1);
    u32(sampleRate);
    u32(sampleRate * 2);
    u16(2);
    u16(16);
    str('data');
    u32(pcm.length);
    return Uint8List.fromList([...header.takeBytes(), ...pcm]);
  }

  /// Plays a Vox reply as it arrives.
  ///
  /// Audio is buffered into segments and played back to back rather than
  /// sample-by-sample: the player needs a complete container per play() call.
  /// The first segment is deliberately small so speech starts as early as
  /// possible; later ones are larger to keep the joins from being audible.
  /// Plays one PCM segment and returns only when it has finished sounding.
  int _segPlayCount = 0;

  /// Writes a segment to disk and loads it into its own player, without
  /// starting playback. The caller resumes it when the previous segment ends.
  Future<_PreparedSegment> _prepareSegment(
    Uint8List pcm,
    int sampleRate,
  ) async {
    final wav = _wavFromPcm(pcm, sampleRate);
    final dir = await getTemporaryDirectory();
    final file = File(
      '${dir.path}/vox_seg_${DateTime.now().microsecondsSinceEpoch}.wav',
    );
    await file.writeAsBytes(wav, flush: true);

    final player = AudioPlayer();
    final done = player.onPlayerComplete.first;
    await player.setSource(DeviceFileSource(file.path));
    return _PreparedSegment(player: player, file: file, done: done);
  }

  Future<void> _awaitSegment(_PreparedSegment seg) async {
    try {
      await seg.done.timeout(VoxConfig.ttsCompletionTimeout);
    } on TimeoutException {
      debugPrint('VOX_STREAM_SEGMENT_TIMEOUT');
    }
  }

  Future<void> _disposeSegment(_PreparedSegment seg) async {
    try {
      await seg.player.dispose();
    } catch (_) {}
    try {
      await seg.file.delete();
    } catch (_) {}
  }

  /// Plays a Vox reply as it arrives, one segment at a time.
  ///
  /// Pull-based, not push-based, and that distinction is the whole fix. The
  /// earlier version cut a segment every ~2s of audio as bytes arrived; since
  /// Lightning delivers ~11.4s of speech in ~950ms (about 12x faster than it
  /// plays), every segment after the first was queued long before its
  /// predecessor could finish. Ordering then depended on a chain of
  /// per-segment completion events on one shared player, and when those did not
  /// arrive in order the player was re-sourced mid-playback — the distortion,
  /// and the many MediaPlayer instances in the device log.
  ///
  /// Here a segment is cut only when the player is free, so at most one is ever
  /// in flight. The first is deliberately small (~0.5s) because that is what
  /// buys the latency; each later one takes whatever has buffered while the
  /// previous was sounding, which keeps the boundary count low without ever
  /// leaving a silent gap waiting for the download.
  Future<void> _playStreamedAudio(Stream<List<int>> audio, int sampleRate) async {
    final leadInBytes = sampleRate; // ~0.5s at 16-bit mono

    final buffer = BytesBuilder();
    var streamDone = false;
    Object? streamError;
    var dataArrived = Completer<void>();

    void wake() {
      if (!dataArrived.isCompleted) dataArrived.complete();
    }

    final subscription = audio.listen(
      (chunk) {
        buffer.add(chunk);
        wake();
      },
      onDone: () {
        streamDone = true;
        wake();
      },
      onError: (Object e) {
        streamError = e;
        streamDone = true;
        wake();
      },
      cancelOnError: true,
    );

    await _player.stop();
    var isFirst = true;
    var segments = 0;
    var totalBytes = 0;
    _PreparedSegment? playing;

    try {
      while (true) {
        final need = isFirst ? leadInBytes : 1;
        // Assignment and re-check are synchronous, so no callback can slip in
        // between them and be missed.
        while (!streamDone && buffer.length < need) {
          dataArrived = Completer<void>();
          if (streamDone || buffer.length >= need) break;
          await dataArrived.future;
        }
        if (buffer.length == 0) {
          if (streamDone) break;
          continue;
        }
        // Cap each segment at ~1s. A visit summary otherwise arrives faster
        // than it plays and lands as one 6.5s buffer, which the player
        // mangles; small segments play cleanly.
        final maxSegBytes = sampleRate * 2;
        var pending = buffer.takeBytes();
        if (pending.length > maxSegBytes) {
          buffer.add(pending.sublist(maxSegBytes));
          pending = pending.sublist(0, maxSegBytes);
        }
        final pcm = Uint8List.fromList(pending);
        isFirst = false;
        segments++;
        totalBytes += pcm.length;

        // Prepare this segment's player NOW, while the previous one is still
        // sounding. setSource does the expensive decode/prepare; by the time
        // the previous segment ends, only resume() remains, so the join is
        // inaudible. A fresh player per segment is required — reusing one
        // corrupts every segment after the first.
        final prepared = await _prepareSegment(pcm, sampleRate);

        // Now wait for the previous segment to finish, then start this one.
        if (playing != null) {
          await _awaitSegment(playing);
          await _disposeSegment(playing);
        }
        await prepared.player.resume();
        playing = prepared;
      }

      // Drain the last segment.
      if (playing != null) {
        await _awaitSegment(playing);
        await _disposeSegment(playing);
        playing = null;
      }
    } finally {
      if (playing != null) {
        await _disposeSegment(playing);
      }
      await subscription.cancel();
    }

    debugPrint('VOX_STREAM_PLAYBACK: segments=$segments bytes=$totalBytes '
        'rate=$sampleRate');
    final err = streamError;
    if (err != null) throw err;
  }

  Future<void> _playAudio(Uint8List bytes) async {
    await _player.stop();
    // Wait until TTS finishes so follow-up mic reopen does not overlap playback.
    final done = _player.onPlayerComplete.first;
    await _player.play(BytesSource(bytes, mimeType: 'audio/mpeg'));
    try {
      await done.timeout(VoxConfig.ttsCompletionTimeout);
    } on TimeoutException {
      // Fall through — reopen mic even if completion event was missed.
    }
  }

  @override
  Widget build(BuildContext context) {
    // Kill switch: Vox is off server-side, so there is nothing to explain and
    // nothing to buy — remove it. An exhausted allowance is different: the
    // button stays so tapping it can say why, and offer the upgrade.
    if (!_voxAvailable && _voxUnavailableReason == 'vox_disabled') {
      return const SizedBox.shrink();
    }
    return GestureDetector(
      onTap: _handleTap,
      child: Stack(
        alignment: Alignment.center,
        children: [
          Container(
            width: 72,
            height: 72,
            decoration: BoxDecoration(
              color: _voiceLiveActive
                  ? const Color(0xFF2E7D32)
                  : Theme.of(context).colorScheme.primary,
              shape: BoxShape.circle,
              border: Border.all(
                color: const Color(0xFFC9A84C),
                width: 3,
              ),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.22),
                  blurRadius: 16,
                  offset: const Offset(0, 6),
                ),
              ],
            ),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Text(
                  'Vox',
                  style: TextStyle(
                    color: Colors.white,
                    fontFamily: 'Poppins',
                    fontSize: 20,
                    fontWeight: FontWeight.w800,
                    height: 1,
                  ),
                ),
                const SizedBox(height: 3),
                if (_pipecatPlaying) ...[
                  const Icon(
                    Icons.graphic_eq,
                    color: Colors.white,
                    size: 13,
                  ),
                  const Text(
                    'Speaking',
                    style: TextStyle(
                      color: Colors.white,
                      fontFamily: 'Poppins',
                      fontSize: 10,
                      fontWeight: FontWeight.w500,
                      height: 1,
                    ),
                  ),
                ] else ...[
                  Text(
                    _voxStatusTop,
                    style: const TextStyle(
                      color: Colors.white,
                      fontFamily: 'Poppins',
                      fontSize: 10,
                      fontWeight: FontWeight.w500,
                      height: 1,
                    ),
                  ),
                  Text(
                    _voxStatusBottom,
                    style: const TextStyle(
                      color: Colors.white,
                      fontFamily: 'Poppins',
                      fontSize: 10,
                      fontWeight: FontWeight.w500,
                      height: 1,
                    ),
                  ),
                ],
              ],
            ),
          ),
          if (_busy)
            Container(
              width: 72,
              height: 72,
              decoration: BoxDecoration(
                color: Colors.black.withValues(alpha: 0.18),
                shape: BoxShape.circle,
              ),
              child: const Center(
                child: SizedBox(
                  width: 22,
                  height: 22,
                  child: CircularProgressIndicator(
                    strokeWidth: 2.5,
                    color: Colors.white,
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// Helper function to determine current navigation item based on route
NavigationItem getCurrentNavigationItem(String location) {
  if (location.startsWith('/patient/home')) {
    return NavigationItem.home;
  } else if (location.startsWith('/patient/overview')) {
    return NavigationItem.overview;
  } else if (location.startsWith('/patient/care-team')) {
    return NavigationItem.careTeam;
  } else if (location.startsWith('/patient/profile') ||
      location.startsWith('/patient/language-settings')) {
    return NavigationItem.profile;
  } else if (location.startsWith('/profile')) {
    return NavigationItem.profile;
  } else if (location.startsWith('/caregiver/home')) {
    return NavigationItem.home;
  } else if (location.startsWith('/caregiver/patients')) {
    return NavigationItem.visits;
  } else if (location.startsWith('/caregiver/alerts')) {
    return NavigationItem.overview;
  } else if (location.startsWith('/caregiver/accept-invitations')) {
    return NavigationItem.careTeam;
  } else {
    return NavigationItem.home;
  }
}

class _PreparedSegment {
  _PreparedSegment({
    required this.player,
    required this.file,
    required this.done,
  });

  final AudioPlayer player;
  final File file;
  final Future<void> done;
}

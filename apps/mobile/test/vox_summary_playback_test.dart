import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:RemiMinder/core/config/vox_config.dart';

/// A visit summary is read as overview + medications + next steps, which on a
/// long visit runs well over a minute of speech. The turn used to stop waiting
/// for playback after 45 seconds, so the button returned to idle while Vox was
/// still talking and the next tap recorded over the reply.
///
/// Measured against the voice actually in use (Lightning v3.1, olivia, speed
/// 0.95): 160 chars -> 8.8s, 500 -> 23.9s, 891 -> 43.4s, 1300 -> 62.4s.
void main() {
  // Characters of speech per second, from the measurements above.
  const charsPerSecond = 20.8;

  late String shell;
  late String summarySpeech;

  setUpAll(() {
    shell = File('lib/features/patient/presentation/widgets/patient_app_shell.dart')
        .readAsStringSync();
    summarySpeech =
        File('../backend/services/remivox/summary_speech.py').readAsStringSync();
  });

  int serverCharCeiling() {
    final m = RegExp(r'SPOKEN_SUMMARY_CHAR_LIMIT\s*=\s*(\d+)')
        .firstMatch(summarySpeech);
    expect(m, isNotNull, reason: 'the server ceiling must still be declared');
    return int.parse(m!.group(1)!);
  }

  group('the turn outlasts the reply', () {
    test('the timeout covers the longest reply the server can produce', () {
      final ceiling = serverCharCeiling();
      final longestSeconds = ceiling / charsPerSecond;
      expect(
        VoxConfig.ttsCompletionTimeout.inSeconds,
        greaterThan(longestSeconds),
        reason: 'a $ceiling-char reply is ~${longestSeconds.round()}s of audio; '
            'the turn must not end before it finishes',
      );
    });

    test('it is longer than the 45s that was too short', () {
      // 45s covered ~940 characters — less than half the server ceiling.
      expect(VoxConfig.ttsCompletionTimeout.inSeconds, greaterThan(45));
    });

    test('a realistic three-section summary fits', () {
      // 891 chars measured at 43.4s — the old value cleared this by 1.6s, which
      // is why it looked fine on short visits and failed on long ones.
      const realisticLongReply = 1300;
      expect(
        VoxConfig.ttsCompletionTimeout.inSeconds,
        greaterThan(realisticLongReply / charsPerSecond),
      );
    });
  });

  group('the fix is on the path that runs', () {
    test('_playAudio uses the timeout', () {
      final start = shell.indexOf('Future<void> _playAudio(');
      expect(start, greaterThan(-1));
      final body = shell.substring(start, shell.indexOf('\n  }', start));
      expect(body.contains('VoxConfig.ttsCompletionTimeout'), isTrue);
      expect(body.contains('onPlayerComplete'), isTrue);
    });

    test('_playAudio is reached by the non-streaming reply path', () {
      // The written-but-never-called failure: assert the call site exists
      // where audio_base64 comes back, not merely that the method compiles.
      expect(shell.contains("data['audio_base64']"), isTrue);
      final at = shell.indexOf("final audio = data['audio_base64']");
      expect(at, greaterThan(-1));
      final after = shell.substring(at, at + 400);
      expect(after.contains('_playAudio(base64Decode(audio))'), isTrue,
          reason: 'the decoded reply audio must go through _playAudio');
    });

    test('nothing on this path stops the player before it finishes', () {
      // The three _player.stop() calls outside _playAudio are Pipecat paths,
      // which are off (kVoxStreamingEnabled == false). If a stop appears on the
      // plain reply path, raising the timeout would not be enough.
      final at = shell.indexOf("final audio = data['audio_base64']");
      final segment = shell.substring(at, at + 400);
      expect(segment.contains('_player.stop()'), isFalse);
    });
  });
}

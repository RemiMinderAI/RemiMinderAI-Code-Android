import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// A consultation recording must survive the app being backgrounded.
///
/// Both halves of this are configuration, not logic, so nothing at runtime
/// fails loudly if one goes missing — the recording just quietly stops when
/// the screen locks, which is the defect this guards. These assert the wiring
/// is present rather than re-testing the plugin.
void main() {
  group('Android keeps recording in a foreground service', () {
    late String manifest;

    setUpAll(() {
      manifest =
          File('android/app/src/main/AndroidManifest.xml').readAsStringSync();
    });

    test('declares the foreground service permissions', () {
      expect(manifest.contains('android.permission.FOREGROUND_SERVICE'), isTrue);
      // Required from API 34; the app targets 36, so startForeground throws
      // without it.
      expect(
        manifest.contains('android.permission.FOREGROUND_SERVICE_MICROPHONE'),
        isTrue,
      );
    });

    test('registers the plugin service with the microphone type', () {
      expect(
        manifest.contains('com.llfbandit.record.service.AudioRecordingService'),
        isTrue,
        reason: 'the plugin ships the service but the app must declare it',
      );
      expect(manifest.contains('android:foregroundServiceType="microphone"'),
          isTrue);
    });

    test('startRecording actually asks for the service', () {
      final source =
          File('lib/core/services/audio_service.dart').readAsStringSync();
      final start = source.indexOf('Future<bool> startRecording(');
      final end = source.indexOf('Future<String?> stopRecording(', start);
      final body = source.substring(start, end);
      expect(body.contains('AndroidService('), isTrue,
          reason: 'the manifest alone does nothing without this config');
      expect(body.contains('_markRecordingInProgress'), isTrue,
          reason: 'an interrupted recording must be findable afterwards');
    });
  });

  group('interrupted recordings are recoverable', () {
    test('the marker is cleared on both deliberate endings', () {
      final source =
          File('lib/core/services/audio_service.dart').readAsStringSync();
      // Once in stop, once in cancel — otherwise a completed recording is
      // offered back to the user as if it had crashed.
      expect(
        RegExp(r'_clearRecordingInProgress\(\)').allMatches(source).length,
        greaterThanOrEqualTo(3), // declaration + stop + cancel
      );
      expect(source.contains('Future<File?> findInterruptedRecording('), isTrue);
    });
  });
}

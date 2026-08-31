import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// Document scanning was broken end to end, and none of it failed at compile
/// time: the client read a response key the server never sent, and posted to a
/// route the server does not register. Both are contracts between two halves of
/// this repo, so both can be checked here.
void main() {
  late String uploadRoute;
  late String apiService;
  late String cameraScreen;

  setUpAll(() {
    uploadRoute = File('../backend/route/visit_summary.py').readAsStringSync();
    apiService =
        File('lib/core/services/backend_api_service.dart').readAsStringSync();
    cameraScreen =
        File('lib/features/patient/presentation/screens/camera_screen.dart')
            .readAsStringSync();
  });

  group('upload response contract', () {
    test('the client reads a key the endpoint actually returns', () {
      final start = apiService.indexOf('Future<String> uploadImage(');
      final end = apiService.indexOf('Future<void> triggerOcr(', start);
      final body = apiService.substring(start, end);

      expect(body.contains("data['image_url']"), isTrue,
          reason: 'image_url is what /image/upload returns');
      expect(uploadRoute.contains('"image_url": image_result["signed_url"]'),
          isTrue,
          reason: 'if the endpoint stops returning image_url, fix the client');
    });

    test('it no longer reads the key that was never sent', () {
      // This cast threw "type 'Null' is not a subtype of type 'String'" on
      // every scan, because the endpoint has never returned that key. Assert on
      // the read itself — the name still appears in the comment explaining why.
      expect(apiService.contains("data['gcs_file_path']"), isFalse);
      expect(uploadRoute.contains('"gcs_file_path"'), isFalse,
          reason: 'the endpoint still does not return it');
    });
  });

  group('OCR route contract', () {
    test('the client calls a route the backend registers', () {
      expect(apiService.contains(r'/api/visits/$visitId/ocr'), isTrue);
      expect(uploadRoute.contains('@router.post("/visits/{visit_id}/ocr")'),
          isTrue);
    });

    test('nothing targets the route that does not exist', () {
      // /api/scan-document is not registered anywhere in the backend.
      expect(apiService.contains('scan-document'), isFalse);
      expect(cameraScreen.contains('scanDocument('), isFalse);
      expect(cameraScreen.contains('triggerOcr('), isTrue);
    });
  });

  group('success is not claimed before it happens', () {
    test('completed is set after the upload, not before it', () {
      final start = cameraScreen.indexOf('Future<void> _completeScan()');
      final end = cameraScreen.indexOf('void _saveScan()', start);
      final body = cameraScreen.substring(start, end);

      final upload = body.indexOf('uploadImage(');
      final completed = body.indexOf('ScanState.completed');
      expect(upload, greaterThan(-1));
      expect(completed, greaterThan(upload),
          reason: '"Scan Successful!" must not render above an error bar');
    });

    test('a failed upload returns the screen to ready', () {
      final start = cameraScreen.indexOf('Future<void> _completeScan()');
      final end = cameraScreen.indexOf('void _saveScan()', start);
      final body = cameraScreen.substring(start, end);
      expect(body.contains('ScanState.ready'), isTrue);
      expect(body.contains('failedToUploadImage'), isTrue);
    });

    test('no fabricated confidence score survives', () {
      expect(cameraScreen.contains("'confidence': 0.95"), isFalse);
      expect(cameraScreen.contains('_generateMockResults'), isFalse);
    });
  });
}

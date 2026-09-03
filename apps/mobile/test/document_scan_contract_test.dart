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
  late String overviewScreen;

  setUpAll(() {
    uploadRoute = File('../backend/route/visit_summary.py').readAsStringSync();
    apiService =
        File('lib/core/services/backend_api_service.dart').readAsStringSync();
    cameraScreen =
        File('lib/features/patient/presentation/screens/camera_screen.dart')
            .readAsStringSync();
    overviewScreen =
        File('lib/features/patient/presentation/screens/overview_screen.dart')
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

  group('the list reads the datastore the feature writes to', () {
    test('scanned docs come from the endpoint, not Firestore', () {
      expect(overviewScreen.contains('FirebaseFirestore'), isFalse,
          reason: 'nothing has written users/{uid}/scanned_docs since the '
              'Postgres migration, so the list was always going to be empty');
      expect(overviewScreen.contains("collection('scanned_docs')"), isFalse);
      expect(apiService.contains("'/api/scanned-docs'"), isTrue);
      expect(uploadRoute.contains('@router.get("/scanned-docs")'), isTrue);
    });

    test('the unused Firestore import went with it', () {
      expect(overviewScreen.contains('cloud_firestore'), isFalse);
    });

    test('the Firestore-era field names are gone from the screen', () {
      // parsed_text and timestamp were Firestore document keys. The endpoint
      // sends neither, so reading them renders blank rather than failing.
      expect(overviewScreen.contains("doc['parsed_text']"), isFalse);
      expect(overviewScreen.contains("doc['timestamp']"), isFalse);
      expect(overviewScreen.contains('is Timestamp'), isFalse);
    });

    test('the card reads keys the endpoint actually sends', () {
      for (final key in ["doc['ocr_preview']", "doc['visit_id']"]) {
        expect(overviewScreen.contains(key), isTrue, reason: key);
      }
      expect(uploadRoute.contains('"ocr_preview": ocr_text[:220]'), isTrue);
      expect(uploadRoute.contains('"visit_id": str(m["visit_id"])'), isTrue);
    });
  });

  group('the date renders', () {
    test('it parses an ISO string from the endpoint', () {
      final start = overviewScreen.indexOf('DateTime? _scannedDocDate(');
      expect(start, greaterThan(-1));
      final body = overviewScreen.substring(
          start, overviewScreen.indexOf('\n  }', start));
      expect(body.contains("'uploaded_at'"), isTrue);
      expect(body.contains("'visit_date'"), isTrue);
      expect(body.contains('DateTime.tryParse'), isTrue);
    });

    test('upload time wins, visit date is the fallback', () {
      final start = overviewScreen.indexOf('DateTime? _scannedDocDate(');
      final body = overviewScreen.substring(
          start, overviewScreen.indexOf('\n  }', start));
      expect(body.indexOf("'uploaded_at'"), lessThan(body.indexOf("'visit_date'")),
          reason: 'the card is labelled "Scanned on"');
    });

    test('the endpoint sends both keys', () {
      expect(uploadRoute.contains('"uploaded_at": _iso_utc(uploaded_at)'), isTrue);
      expect(uploadRoute.contains('"visit_date": m["visit_date"].isoformat()'),
          isTrue);
    });

    test('a naive timestamp is not left for the client to misread', () {
      // datetime.now() is naive and the container runs UTC; Dart would parse
      // it as local time and, near midnight, show the wrong day.
      expect(uploadRoute.contains('"uploaded_at": datetime.now().isoformat()'),
          isFalse);
      expect(uploadRoute.contains('def _iso_utc('), isTrue);
    });
  });

  group('lab results are filtered where the text is', () {
    test('the tab calls its own endpoint', () {
      expect(apiService.contains("'/api/lab-results'"), isTrue);
      expect(uploadRoute.contains('@router.get("/lab-results")'), isTrue);
    });

    test('the screen does not keyword-filter the preview', () {
      // The preview is 220 chars. "cholesterol" is very unlikely to appear
      // that early in a lab report, so a client-side filter would return an
      // empty tab — which reads as "no lab results", not as a bug.
      for (final kw in ["'cholesterol'", "'glucose'", "'blood'"]) {
        expect(overviewScreen.contains(kw), isFalse, reason: kw);
      }
      expect(overviewScreen.contains("contains('lab')"), isFalse);
    });

    test('_labResults is fetched, not derived from _scannedDocs', () {
      final start = overviewScreen.indexOf('Future<void> _fetchScannedDocs()');
      final body = overviewScreen.substring(
          start, overviewScreen.indexOf('\n  }', start));
      expect(body.contains('getLabResults()'), isTrue);
      expect(body.contains('_scannedDocs.where'), isFalse);
    });

    test('the two tabs fail independently', () {
      expect(overviewScreen.contains('_labResultsError'), isTrue,
          reason: 'a lab-results outage must not blank the Scanned tab');
    });
  });

  group('the detail view shows the whole document', () {
    test('it fetches full text rather than reusing the preview', () {
      expect(apiService.contains('Future<String> getVisitOcrText('), isTrue);
      expect(apiService.contains(r'/api/visits/$visitId/ocr-text'), isTrue);
      expect(uploadRoute.contains('@router.get("/visits/{visit_id}/ocr-text")'),
          isTrue);
    });

    test('the sheet is handed a visit id, not a 220-char stub', () {
      expect(overviewScreen.contains('getVisitOcrText('), isTrue);
      expect(
          overviewScreen.contains(
              'void _showScannedDocDetail(\n      BuildContext context, String visitId, String dateStr)'),
          isTrue);
    });

    test('the list endpoint preview cap was not raised to compensate', () {
      expect(uploadRoute.contains('ocr_text[:220]'), isTrue,
          reason: 'every list fetch would otherwise carry every document');
    });
  });

  group('an unreadable scan is not reported as success', () {
    test('the no-text case is its own exception, not a generic failure', () {
      // The backend maps ValueError("No text detected in image") to a 400.
      expect(apiService.contains('class OcrFoundNoTextException'), isTrue);
      final start = apiService.indexOf('Future<void> triggerOcr(');
      final body = apiService.substring(start, apiService.indexOf('\n  }', start));
      expect(body.contains("response.body.contains('No text detected')"), isTrue);
      expect(body.contains('throw const OcrFoundNoTextException()'), isTrue);
    });

    test('the camera screen handles it separately', () {
      expect(cameraScreen.contains('on OcrFoundNoTextException'), isTrue);
      expect(cameraScreen.contains('l10n.scanNoTextFound') ||
             cameraScreen.contains('.scanNoTextFound'), isTrue);
    });

    test('a failed scan does not navigate away', () {
      // It used to show the error and then go to Overview regardless, so the
      // message flashed during the transition and the document was listed with
      // no text.
      final start = cameraScreen.indexOf('void _saveScan()');
      final body = cameraScreen.substring(start, cameraScreen.indexOf('\n  }', start));
      final noText = body.indexOf('on OcrFoundNoTextException');
      final nav = body.indexOf("context.go('/patient/overview')");
      expect(noText, greaterThan(-1));
      expect(nav, greaterThan(noText),
          reason: 'navigation must come after, and be skipped by, the failure paths');
      // The branch must return AFTER telling the user — not merely contain a
      // return, which its own `if (!mounted) return;` would satisfy, and not
      // merely precede one belonging to the generic catch.
      final genericCatch = body.indexOf('} catch (e)', noText);
      expect(genericCatch, greaterThan(noText));
      final noTextBlock = body.substring(noText, genericCatch);
      final afterMessage = noTextBlock.substring(
          noTextBlock.indexOf('showSnackBar'));
      expect(afterMessage.contains('return;'), isTrue,
          reason: 'the no-text branch must return after showing the message, '
              'not fall through and navigate to Overview');

      // Bounded at the catch's own closing brace: reading as far as the
      // navigation would pick up the `if (!mounted) return;` that guards it.
      final genericEnd = body.indexOf('\n    }', genericCatch);
      expect(genericEnd, greaterThan(genericCatch));
      final genericBlock = body.substring(genericCatch, genericEnd);
      final afterGenericMessage =
          genericBlock.substring(genericBlock.indexOf('showSnackBar'));
      expect(afterGenericMessage.contains('return;'), isTrue,
          reason: 'the generic failure branch must return too');
    });

    test('the user is returned to a state they can retake from', () {
      final start = cameraScreen.indexOf('void _saveScan()');
      final body = cameraScreen.substring(start, cameraScreen.indexOf('\n  }', start));
      expect(body.contains('ScanState.ready'), isTrue);
    });

    test('the message exists in every supported locale', () {
      for (final code in ['en','bn','de','es','fr','gu','hi','pa','pt','ta']) {
        final arb = File('lib/l10n/app_$code.arb').readAsStringSync();
        expect(arb.contains('"scanNoTextFound"'), isTrue, reason: code);
      }
    });
  });
}

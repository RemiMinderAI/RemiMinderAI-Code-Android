import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// The caregiver roster read Firestore's users/{uid}/connectedPatients, which
/// no backend code writes. Nothing crashed: an empty collection is an empty
/// result, so the screen rendered a finished-looking zero. These assertions
/// cover the client/server contract and, just as importantly, that an empty
/// roster and a failed fetch no longer look the same.
void main() {
  late String apiService;
  late String homeScreen;
  late String overviewScreen;
  late String dbService;
  late String careTeamRoute;

  setUpAll(() {
    apiService =
        File('lib/core/services/backend_api_service.dart').readAsStringSync();
    homeScreen = File(
            'lib/features/caregiver/presentation/screens/caregiver_home_screen.dart')
        .readAsStringSync();
    overviewScreen = File(
            'lib/features/caregiver/presentation/screens/patient_overview_screen.dart')
        .readAsStringSync();
    dbService = File('../backend/services/db_service.py').readAsStringSync();
    careTeamRoute = File('../backend/route/care_team.py').readAsStringSync();
  });

  String rosterRow() {
    // The dict the endpoint actually serialises, not the SELECT above it.
    final start = dbService.indexOf('async def get_my_patients_for_caregiver');
    final end = dbService.indexOf('async def get_symptom_journal_for_patient');
    return dbService.substring(start, end);
  }

  group('the roster comes from the datastore the backend writes', () {
    test('the screen no longer reads Firestore', () {
      expect(homeScreen.contains('FirebaseFirestore'), isFalse);
      expect(homeScreen.contains("collection('connectedPatients')"), isFalse);
      expect(homeScreen.contains('cloud_firestore'), isFalse);
    });

    test('nothing in the backend writes connectedPatients', () {
      // The reason the collection is empty in the first place.
      expect(dbService.contains('connectedPatients'), isFalse);
      expect(careTeamRoute.contains('connectedPatients'), isFalse);
    });

    test('the client calls the endpoint the backend registers', () {
      expect(apiService.contains("'/api/care-team/my-patients'"), isTrue);
      expect(careTeamRoute.contains('@router.get("/my-patients"'), isTrue);
      expect(careTeamRoute.contains('APIRouter(prefix="/api/care-team"'), isTrue);
      expect(homeScreen.contains('getMyPatients()'), isTrue);
    });
  });

  group('the screen reads the keys the endpoint sends', () {
    test('identity and name keys exist in the response', () {
      final row = rosterRow();
      for (final key in ['"patient_id"', '"firebase_uid"', '"full_name"']) {
        expect(row.contains(key), isTrue, reason: key);
      }
    });

    test('the Firestore-era key names are gone from the screen', () {
      // None of these were ever sent by the endpoint; reading them would have
      // rendered every alert as the generic "Patient" label.
      for (final dead in [
        "patient['patientId']",
        "patient['name']",
        "patient['patientName']",
        "patient['fullName']",
      ]) {
        expect(homeScreen.contains(dead), isFalse, reason: dead);
      }
    });

    test('alerts resolve against both ids, because alerts carry either', () {
      expect(homeScreen.contains("patient['patient_id']"), isTrue);
      expect(homeScreen.contains("patient['firebase_uid']"), isTrue);
      expect(homeScreen.contains("patient['full_name']"), isTrue);
      // The SQL matches an alert on either column, so the client must too.
      final row = rosterRow();
      expect(row.contains('ca.user_id::text = u.id::text'), isTrue);
      expect(row.contains('ca.user_id = u.firebase_uid'), isTrue);
    });
  });

  group('an empty roster and a failed fetch look different', () {
    test('the fetch surfaces failure instead of swallowing it', () {
      final start = homeScreen.indexOf('Future<void> _loadConnectedPatients()');
      final body =
          homeScreen.substring(start, homeScreen.indexOf('\n  }', start));
      expect(body.contains('_patientsError'), isTrue);
      expect(body.contains('// silent fail'), isFalse,
          reason: 'the previous version said exactly this');
    });

    test('the count is unknown rather than zero when it is not known', () {
      // A zero here reads as "no patients", which is the confusion itself.
      expect(homeScreen.contains('required int? patientCount'), isTrue);
      expect(homeScreen.contains("value == null ? '—'"), isTrue);
      expect(
          homeScreen.contains(
              '(_isLoadingPatients || _patientsError != null)'),
          isTrue);
    });

    test('a failure is visible and retryable', () {
      expect(homeScreen.contains('_buildPatientsErrorBanner'), isTrue);
      expect(homeScreen.contains('l10n.failedToLoadPatients'), isTrue);
      expect(homeScreen.contains('onPressed: _loadConnectedPatients'), isTrue);
    });

    test('the error string exists in every supported locale', () {
      for (final code in [
        'en', 'bn', 'de', 'es', 'fr', 'gu', 'hi', 'pa', 'pt', 'ta'
      ]) {
        final arb = File('lib/l10n/app_$code.arb').readAsStringSync();
        expect(arb.contains('"failedToLoadPatients"'), isTrue, reason: code);
      }
    });
  });

  group('accepting an invitation refreshes the roster', () {
    test('the roster reloads when the invitations provider changes', () {
      // Loading only in initState is why the accepted patient did not appear
      // until the app was restarted.
      expect(homeScreen.contains('ref.listen(receivedInvitationsProvider'),
          isTrue);
      final start = homeScreen.indexOf('ref.listen(receivedInvitationsProvider');
      final body = homeScreen.substring(start, start + 220);
      expect(body.contains('_loadConnectedPatients()'), isTrue);
    });
  });

  group('the patient name', () {
    test('comes from the roster, not a Firestore user document', () {
      expect(overviewScreen.contains('FirebaseFirestore'), isFalse);
      expect(overviewScreen.contains('cloud_firestore'), isFalse);
      expect(overviewScreen.contains("doc.data()?['displayName']"), isFalse);
      expect(overviewScreen.contains('getMyPatients()'), isTrue);
    });

    test('it matches on either id, like the alert lookup', () {
      final start = overviewScreen.indexOf('Future<void> _loadPatientName()');
      expect(start, greaterThan(-1));
      final body =
          overviewScreen.substring(start, overviewScreen.indexOf('\n  }', start));
      expect(body.contains("p['patient_id']"), isTrue);
      expect(body.contains("p['firebase_uid']"), isTrue);
      expect(body.contains("p['full_name']"), isTrue);
    });

    test('a failed lookup is distinguishable from a real name', () {
      final start = overviewScreen.indexOf('Future<void> _loadPatientName()');
      final body =
          overviewScreen.substring(start, overviewScreen.indexOf('\n  }', start));
      // The flag must be set where the failure happens — merely declaring it
      // and rendering it is what the old code effectively did with the
      // generic label.
      final catchAt = body.indexOf('} catch (');
      expect(catchAt, greaterThan(-1));
      expect(body.indexOf('_patientNameFailed = true'), greaterThan(catchAt),
          reason: 'the catch branch must record the failure');
      expect(overviewScreen.contains('onPressed: _loadPatientName'), isTrue);
      expect(overviewScreen.contains('if (_patientNameFailed)'), isTrue);
    });

    test('no health data reaches the log', () {
      final start = overviewScreen.indexOf('Future<void> _loadPatientName()');
      final body =
          overviewScreen.substring(start, overviewScreen.indexOf('\n  }', start));
      for (final line in body.split('\n')) {
        if (line.contains('debugPrint')) {
          expect(line.contains('_patientName'), isFalse);
          expect(line.contains('full_name'), isFalse);
        }
      }
    });

    test('the write to the dead collection is gone', () {
      // It created connectedPatients docs holding only lastSyncedAt — no
      // joinedAt, so MyPatientsRepository's orderBy excluded them anyway.
      expect(overviewScreen.contains('_markPatientSynced'), isFalse);
      expect(overviewScreen.contains('lastSyncedAt'), isFalse);
    });
  });
}

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// Item 3: appointments and tasks were stored and returned correctly all the
/// way to the card's icon — there was simply no way to ask for them.
///
/// Item 7: the care team list merged two Firestore queries and appended the
/// second without deduplicating against the first, so one caregiver could
/// appear twice. The database cannot produce that; the duplicate only existed
/// in the merge.
void main() {
  late String service;
  late String screen;
  late String reminders;

  setUpAll(() {
    service = File(
            'lib/features/care_team/data/services/care_team_api_service.dart')
        .readAsStringSync();
    screen = File(
            'lib/features/patient/presentation/screens/care_team_screen.dart')
        .readAsStringSync();
    reminders = File(
            'lib/features/patient/presentation/screens/reminders_screen.dart')
        .readAsStringSync();
  });

  /// The method body only — bounded at the next member declaration, so an
  /// assertion cannot accidentally read the method that follows.
  String bodyOf(String src, String start) {
    final at = src.indexOf(start);
    expect(at, greaterThan(-1), reason: 'missing: $start');
    final end = src.indexOf('\n  }', at);
    expect(end, greaterThan(at), reason: 'unterminated: $start');
    return src.substring(at, end);
  }

  group('the care team reads one source', () {
    test('getCareTeam calls the API, not Firestore', () {
      final body = bodyOf(service, 'Future<List<CareTeamMember>> getCareTeam()');
      expect(body.contains("/api/care-team"), isTrue);
      expect(body.contains('_db'), isFalse,
          reason: 'the Firestore half is what produced the duplicate');
      expect(body.contains('CareTeamMember.fromJson'), isTrue);
    });

    test('the two Firestore converters are gone', () {
      // inviteCaregiver still writes Firestore — the invitation lifecycle is a
      // separate migration. What mattered here is that the *read* no longer
      // merges two collections, so these converters have no caller left.
      expect(service.contains('_invitationAcceptedToMember'), isFalse);
      expect(service.contains('_careTeamDocToMember'), isFalse);
      expect(service.contains(".where('status', isEqualTo: 'accepted')"), isFalse,
          reason: 'both halves of the duplicating merge queried on this');
    });

    test('a failed fetch is not an empty care team', () {
      final body = bodyOf(service, 'Future<List<CareTeamMember>> getCareTeam()');
      expect(body.contains('throw StateError'), isTrue);
      expect(body.contains('throw Exception'), isTrue);
      expect(body.contains('return [];'), isFalse);
    });
  });

  group('the screen finishes loading', () {
    test('loading ends whether or not the data changed', () {
      // The spinner used to be cleared only inside the changed branch, so a
      // reload returning the same care team left it up for good.
      final body = bodyOf(screen, 'final changed = _membersChanged');
      final setStateAt = body.indexOf('setState(() {');
      final loadingAt = body.indexOf('_isLoading = false;');
      final ifAt = body.indexOf('if (changed) {');
      expect(loadingAt, greaterThan(setStateAt));
      expect(loadingAt, greaterThan(ifAt),
          reason: '_isLoading must sit after the changed branch, outside it');
      // and it must not be inside the guarded assignment block
      final guarded = body.substring(ifAt, body.indexOf('}', ifAt));
      expect(guarded.contains('_isLoading'), isFalse);
    });

    test('an error still renders differently from an empty team', () {
      expect(screen.contains('else if (_error != null)'), isTrue);
    });
  });

  group('there is a way back', () {
    test('the screen has an app bar with a back action', () {
      expect(screen.contains('appBar: AppBar('), isTrue);
      // context.go replaces the stack, so popping would have nowhere to land.
      expect(screen.contains("context.go('/patient/home')"), isTrue);
    });
  });

  group('appointments and tasks can be found', () {
    test('the list can be filtered by reminder type', () {
      expect(reminders.contains('String? _typeFilter'), isTrue);
      final body = bodyOf(reminders, 'List<Map<String, dynamic>> get _filteredReminders');
      expect(body.contains('_typeFilter'), isTrue);
    });

    test('the filter offers appointment and task, not just medication', () {
      final body = bodyOf(reminders, 'Widget _buildTypeFilter(');
      for (final t in ["'medication'", "'appointment'", "'task'"]) {
        expect(body.contains(t), isTrue, reason: t);
      }
    });

    test('the type still reaches the card, which is where it always did', () {
      // Not lost in the model, the mapping or the fetch — this pins that.
      expect(reminders.contains("'type': r['reminder_type']?.toString()"), isTrue);
      expect(reminders.contains('_getTypeIcon(type)'), isTrue);
    });
  });
}

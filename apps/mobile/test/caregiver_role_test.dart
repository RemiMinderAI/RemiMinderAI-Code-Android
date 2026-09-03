import 'dart:io';
import 'package:flutter_test/flutter_test.dart';

/// Someone who accepts an invitation was stored as a patient and bounced off
/// every caregiver route. The role lived only in SecureStorage, defaulted to
/// patient, and the invite path never asks.
void main() {
  late String router;
  late String service;
  late String accept;
  late String authProvider;
  late String errorFormatter;

  setUpAll(() {
    router = File('lib/router/app_router.dart').readAsStringSync();
    service = File(
            'lib/features/care_team/data/services/care_team_api_service.dart')
        .readAsStringSync();
    accept = File(
            'lib/features/caregiver/presentation/screens/accept_invitations_screen.dart')
        .readAsStringSync();
    authProvider = File(
            'lib/features/auth/presentation/providers/auth_provider.dart')
        .readAsStringSync();
    errorFormatter =
        File('lib/core/utils/reminder_action_error.dart').readAsStringSync();
  });

  group('the role comes from the server', () {
    test('the router prefers the profile role over device storage', () {
      final start = router.indexOf('if (authState.status == AuthStatus.authenticated)');
      final body = router.substring(start, start + 700);
      expect(body.contains("serverRole == 'caregiver'"), isTrue);
      // The old comment said to use SecureStorage and NOT the backend.
      expect(router.contains('Use user.role (from SecureStorage) NOT profile.role'),
          isFalse);
    });

    test('device storage remains the offline fallback, not the source', () {
      final start = router.indexOf('if (authState.status == AuthStatus.authenticated)');
      final body = router.substring(start, start + 700);
      expect(body.contains('authState.user?.role == UserRole.caregiver'), isTrue);
      expect(body.contains('serverRole != null'), isTrue);
    });

    test('accepting an invitation re-reads the role', () {
      expect(authProvider.contains('Future<void> refreshProfile()'), isTrue);
      expect(accept.contains('refreshProfile()'), isTrue);
      // and it must happen on the accept path, not somewhere unrelated
      final at = accept.indexOf('acceptInvitationByToken');
      final refresh = accept.indexOf('refreshProfile()');
      expect(refresh, greaterThan(at));
    });
  });

  group('removing a caregiver reaches the server', () {
    test('it calls the endpoint rather than deleting a Firestore doc', () {
      final start = service.indexOf('Future<void> removeMember(');
      final body = service.substring(start, service.indexOf('\n  }', start));
      expect(body.contains('/api/care-team/'), isTrue);
      expect(body.contains('http.delete'), isTrue);
      expect(body.contains('_db'), isFalse,
          reason: 'the Firestore delete never changed the care_team_members row');
    });

    test('a failed removal is surfaced, not swallowed', () {
      final start = service.indexOf('Future<void> removeMember(');
      final body = service.substring(start, service.indexOf('\n  }', start));
      expect(body.contains('throw Exception'), isTrue);
    });

    test('rows without a member are never rendered as caregivers', () {
      expect(service.contains("m['member_user_id']"), isTrue);
    });
  });

  group('the snooze refusal talks about snoozing', () {
    test('the card path has its own wording with the due time', () {
      expect(errorFormatter.contains("detail['error'] == 'not_due_yet'"), isTrue);
      final at = errorFormatter.indexOf("detail['error'] == 'not_due_yet'");
      final body = errorFormatter.substring(at, at + 500);
      expect(body.contains('nothing to snooze'), isTrue);
      expect(body.contains('_whenLabel'), isTrue);
    });

    test('mark-done keeps its own branch', () {
      expect(errorFormatter.contains("detail['error'] == 'reminder_not_due'"), isTrue);
    });
  });
}

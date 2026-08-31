import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:RemiMinder/features/care_team/data/models/care_team_member.dart';
import 'package:RemiMinder/features/care_team/data/services/care_team_api_service.dart';

/// Accepting a caregiver invitation succeeded but the screen never moved, and
/// the workaround was to kill the app and sign in again.
///
/// Nothing here throws when it regresses — the accept still "works", the UI
/// just shows the team from before it. That is why it needs guarding.
void main() {
  const member = CareTeamMember(
    id: 'm1',
    patientId: 'p1',
    memberUserId: 'u1',
    role: 'caregiver',
    permission: 'full',
    status: 'active',
  );

  group('the care team cache can be invalidated', () {
    tearDown(CareTeamApiService.clearCache);

    test('clearCache empties what was cached', () {
      CareTeamApiService.setCachedMembers([member]);
      expect(CareTeamApiService.getCachedMembers(), isNotNull);

      CareTeamApiService.clearCache();

      // Killing the app used to be the only way to reach this state.
      expect(CareTeamApiService.getCachedMembers(), isNull);
      expect(CareTeamApiService.getCachedPendingInvites(), isNull);
    });

    test('it is safe to call when nothing is cached', () {
      CareTeamApiService.clearCache();
      expect(CareTeamApiService.clearCache, returnsNormally);
    });
  });

  group('accepting an invitation refreshes what the caregiver sees', () {
    late String source;

    setUpAll(() {
      source = File(
        'lib/features/caregiver/presentation/screens/accept_invitations_screen.dart',
      ).readAsStringSync();
    });

    String afterAccept() {
      final start = source.indexOf('acceptInvitationByToken(token)');
      final end = source.indexOf('} catch', start);
      return source.substring(start, end);
    }

    test('the stale team cache is dropped', () {
      expect(afterAccept().contains('CareTeamApiService.clearCache()'), isTrue,
          reason: 'the screen reads the cache first, so a reload alone is not enough');
    });

    test('the accepted invitation stops showing as pending', () {
      expect(afterAccept().contains('ref.invalidate(receivedInvitationsProvider)'),
          isTrue);
    });

    test('the care team tab is actually rebuilt', () {
      // CareTeamTab loads once in its own initState and exposes no reload, so
      // a changing key is what forces the fresh load.
      expect(afterAccept().contains('_reloadToken++'), isTrue);
      expect(source.contains('CareTeamTab(key: ValueKey(_reloadToken))'), isTrue);
      expect(source.contains('const CareTeamTab()'), isFalse,
          reason: 'a const tab can never be rebuilt');
    });

    test('none of it runs when the accept failed', () {
      // All three sit inside the try, above the catch.
      final catchBlock = source.substring(source.indexOf('} catch'));
      expect(catchBlock.contains('clearCache'), isFalse);
      expect(catchBlock.contains('_reloadToken++'), isFalse);
    });
  });
}

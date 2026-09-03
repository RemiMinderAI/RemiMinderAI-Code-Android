import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../care_team/care_team_tab.dart';
import '../../../care_team/data/services/care_team_api_service.dart';
import '../../../../providers/invitation_provider.dart';
import '../../../auth/presentation/providers/auth_provider.dart';
import '../../../../services/invitation_service.dart';

/// Caregiver shell route: Care Team tab (Firestore invitations + API team).
class AcceptInvitationsScreen extends ConsumerStatefulWidget {
  final String? inviteToken;
  const AcceptInvitationsScreen({super.key, this.inviteToken});

  @override
  ConsumerState<AcceptInvitationsScreen> createState() =>
      _AcceptInvitationsScreenState();
}

class _AcceptInvitationsScreenState
    extends ConsumerState<AcceptInvitationsScreen> {
  /// Bumped after a successful accept so CareTeamTab is rebuilt from scratch.
  ///
  /// The tab loads once in its own initState and exposes no way to reload, so
  /// accepting an invitation left it showing the team from before the accept —
  /// the screen "not moving" that was reported. Changing the key discards that
  /// State and runs the load again.
  int _reloadToken = 0;

  @override
  void initState() {
    super.initState();
    if (widget.inviteToken != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        _handleInviteToken(widget.inviteToken!);
      });
    }
  }

  Future<void> _handleInviteToken(String token) async {
    final confirmed = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (context) => AlertDialog(
        title: const Text('Accept Invitation'),
        content: const Text('You have been invited to join a care team on RemiMinderAI. Would you like to accept?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Decline'),
          ),
          ElevatedButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Accept'),
          ),
        ],
      ),
    );

    if (confirmed != true) return;

    try {
      await InvitationService().acceptInvitationByToken(token);
      // The team just changed, so nothing cached about it is true any more.
      CareTeamApiService.clearCache();
      // And the invitation is no longer pending — without this it stays in the
      // received list until the next cold start.
      ref.invalidate(receivedInvitationsProvider);
      // This account is now somebody's caregiver. The role is derived from that
      // membership server-side, so re-read it — otherwise the router keeps
      // treating an invited user as a patient until the next cold start, which
      // is what left them with nowhere to go after accepting.
      await ref.read(authNotifierProvider.notifier).refreshProfile();
      if (mounted) {
        setState(() => _reloadToken++);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('You have joined the care team!')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to accept invitation: $e')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return CareTeamTab(key: ValueKey(_reloadToken));
  }
}

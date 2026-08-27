import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../../core/services/vox_consent_service.dart';
import '../../../../l10n/app_localizations.dart';

final Uri _privacyPolicyUri = Uri.parse('https://remiminderai.com/privacy');

/// Ask once, before the first Vox turn, and remember the answer.
///
/// Returns true when Vox may proceed. A false means the patient declined or
/// dismissed the dialog, and the caller must record nothing, send nothing and
/// charge nothing — a decline has to cost the same as never tapping the button.
Future<bool> ensureVoxConsent(
  BuildContext context, {
  VoxConsentService? service,
}) async {
  final consent = service ?? VoxConsentService();
  if (await consent.hasAccepted()) return true;
  if (!context.mounted) return false;

  final accepted = await showDialog<bool>(
        context: context,
        // The patient has to answer this one way or the other; a stray tap
        // outside is not agreement to send health information off the device.
        barrierDismissible: false,
        builder: (_) => const _VoxConsentDialog(),
      ) ??
      false;

  if (accepted) await consent.recordAcceptance();
  return accepted;
}

class _VoxConsentDialog extends StatefulWidget {
  const _VoxConsentDialog();

  @override
  State<_VoxConsentDialog> createState() => _VoxConsentDialogState();
}

class _VoxConsentDialogState extends State<_VoxConsentDialog> {
  bool _understood = false;

  Future<void> _openPrivacyPolicy() async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final launched = await launchUrl(
        _privacyPolicyUri,
        mode: LaunchMode.inAppBrowserView,
      );
      if (!launched && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.unableToOpenPrivacyPolicy)),
        );
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.unableToOpenPrivacyPolicy)),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final l10n = AppLocalizations.of(context)!;

    return AlertDialog(
      title: Text(
        l10n.voxConsentTitle,
        style: const TextStyle(fontWeight: FontWeight.w700),
      ),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              l10n.voxConsentBody,
              style: const TextStyle(fontSize: 15, height: 1.4),
            ),
            const SizedBox(height: 12),
            Text(
              l10n.voxConsentHealthNote,
              style: const TextStyle(fontSize: 15, height: 1.4),
            ),
            const SizedBox(height: 8),
            // Same shape as the recording and scanning consents: an explicit
            // tick, a 44pt target, and the policy one tap away.
            Material(
              color: Colors.transparent,
              child: InkWell(
                onTap: () => setState(() => _understood = !_understood),
                borderRadius: BorderRadius.circular(14),
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SizedBox(
                        width: 44,
                        height: 44,
                        child: Center(
                          child: Checkbox(
                            value: _understood,
                            onChanged: (checked) =>
                                setState(() => _understood = checked ?? false),
                          ),
                        ),
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Padding(
                          padding: const EdgeInsets.only(top: 12),
                          child: Text(
                            l10n.voxConsentCheckbox,
                            style: TextStyle(
                              color: theme.colorScheme.primary,
                              fontSize: 14,
                              fontWeight: FontWeight.w500,
                              height: 1.35,
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
            TextButton(
              onPressed: _openPrivacyPolicy,
              style: TextButton.styleFrom(
                alignment: Alignment.centerLeft,
                foregroundColor: theme.colorScheme.primary,
                minimumSize: const Size(44, 44),
                padding: EdgeInsets.zero,
                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
              ),
              child: Text(
                l10n.privacyPolicy,
                style: const TextStyle(
                  fontWeight: FontWeight.w600,
                  decoration: TextDecoration.underline,
                ),
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: Text(l10n.voxConsentNotNow),
        ),
        // Stays disabled until the box is ticked: dismissal is not agreement,
        // which is how the recording consent behaves too.
        FilledButton(
          onPressed:
              _understood ? () => Navigator.of(context).pop(true) : null,
          child: Text(l10n.voxConsentContinue),
        ),
      ],
    );
  }
}

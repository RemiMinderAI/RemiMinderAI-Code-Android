import 'dart:io';

import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'auth_service.dart';

/// Whether this patient has agreed to their speech leaving the device.
///
/// Vox sends the recording to three external services — one to transcribe it,
/// one to work out what was asked, one to speak the reply — and that speech
/// routinely carries medication names, physician names and conditions. Visit
/// recording and document scanning both ask first; Vox did not.
///
/// Scoped per user, never per device. A shared family tablet with a patient and
/// a caregiver on it is a realistic setup, and one of them consenting must not
/// consent on the other's behalf. The stored key carries the uid for that
/// reason, and a user who has never answered is treated as not having agreed.
class VoxConsentService {
  VoxConsentService({AuthService? authService, FirebaseFirestore? firestore})
      : _injectedAuth = authService,
        _injectedStore = firestore;

  // Resolved on use, not in the constructor: FirebaseFirestore.instance throws
  // when Firebase is not initialised, and constructing this type must stay
  // possible in a test that never touches Firebase.
  final AuthService? _injectedAuth;
  final FirebaseFirestore? _injectedStore;

  AuthService get _authService => _injectedAuth ?? AuthService();
  FirebaseFirestore get _firestore =>
      _injectedStore ?? FirebaseFirestore.instance;

  /// Bumping this re-asks everyone — use it when the disclosure itself changes.
  static const String consentVersion = 'v1';

  static String prefsKeyFor(String uid) => 'vox_consent_${consentVersion}_$uid';

  Future<String?> _uid() async {
    try {
      final user = await _authService.getCurrentUser();
      final uid = user?.authUid ?? user?.id;
      return (uid == null || uid.isEmpty) ? null : uid;
    } catch (e) {
      debugPrint('VOX_CONSENT: could not read current user ($e)');
      return null;
    }
  }

  /// True only for a signed-in user who has already agreed.
  ///
  /// Anything unknown — no session, unreadable storage — answers false, so the
  /// failure mode is asking a second time rather than sending speech that was
  /// never agreed to.
  Future<bool> hasAccepted() async {
    final uid = await _uid();
    if (uid == null) return false;
    try {
      final prefs = await SharedPreferences.getInstance();
      return prefs.getBool(prefsKeyFor(uid)) ?? false;
    } catch (e) {
      debugPrint('VOX_CONSENT: could not read stored consent ($e)');
      return false;
    }
  }

  /// Remember the agreement, and leave an audit record.
  ///
  /// The local flag is what gates the next turn: it has to work offline and
  /// before the first network call. The Firestore write mirrors the existing
  /// recording and scanning consents so all three live in one place, and it is
  /// deliberately not awaited-into-failure — losing the audit row must not
  /// block a patient who has just agreed.
  Future<void> recordAcceptance() async {
    final uid = await _uid();
    if (uid == null) {
      debugPrint('VOX_CONSENT: no signed-in user; consent not persisted');
      return;
    }
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool(prefsKeyFor(uid), true);
    } catch (e) {
      debugPrint('VOX_CONSENT: could not store consent ($e)');
    }
    try {
      final sessionId =
          DateTime.now().toUtc().millisecondsSinceEpoch.toString();
      await _firestore
          .collection('consents')
          .doc(uid)
          .collection('vox_sessions')
          .doc(sessionId)
          .set({
        'voxConsentGiven': true,
        'consentVersion': consentVersion,
        'platform': _platformLabel,
        'timestamp': FieldValue.serverTimestamp(),
      });
    } catch (e) {
      debugPrint('VOX_CONSENT: audit record failed ($e)');
    }
  }

  String get _platformLabel {
    if (Platform.isIOS) return 'ios';
    if (Platform.isAndroid) return 'android';
    return Platform.operatingSystem;
  }
}

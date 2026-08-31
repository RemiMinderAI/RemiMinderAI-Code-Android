import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

/// One row of `GET /api/care-team/my-patients`.
///
/// This used to merge two Firestore documents. The roster now comes from
/// care_team_members in Postgres, which is what the backend maintains; the
/// Firestore link documents were only ever written by this app.
class ConnectedPatient {
  final String patientId;
  final String displayName;
  final String role;
  final String invitationId;
  final DateTime? joinedAt;

  final String? profileName;
  final String? dob;
  final String? gender;
  final String? primaryCondition;
  final String? allergies;
  final List<String> medications;
  final List<String> alerts;
  final DateTime? lastActivityAt;

  const ConnectedPatient({
    required this.patientId,
    required this.displayName,
    required this.role,
    required this.invitationId,
    this.joinedAt,
    this.profileName,
    this.dob,
    this.gender,
    this.primaryCondition,
    this.allergies,
    this.medications = const [],
    this.alerts = const [],
    this.lastActivityAt,
  });

  /// Fields the roster endpoint does not carry (dob, gender, conditions,
  /// medications, alerts) stay empty; the card omits those rows rather than
  /// rendering blanks.
  factory ConnectedPatient.fromRoster(Map<String, dynamic> row) {
    final name = (row['full_name'] as String?)?.trim() ?? '';
    return ConnectedPatient(
      patientId: (row['patient_id'] ?? row['firebase_uid'] ?? '').toString(),
      displayName: name.isNotEmpty ? name : 'Patient',
      role: (row['relationship'] as String?)?.trim() ?? '',
      invitationId: '',
      joinedAt: _readTimestamp(row['joined_at']),
      profileName: name.isNotEmpty ? name : null,
      lastActivityAt: _readTimestamp(row['last_activity_at']),
    );
  }

  static DateTime? _readTimestamp(dynamic v) {
    if (v == null) return null;
    if (v is DateTime) return v.toLocal();
    if (v is String && v.isNotEmpty) return DateTime.tryParse(v)?.toLocal();
    return null;
  }

  static const _avatarPalettes = [
    (Color(0xFFE6F0FA), Color(0xFF1A3A5C)),
    (Color(0xFFE6F1FB), Color(0xFF185FA5)),
    (Color(0xFFEEEDFE), Color(0xFF534AB7)),
    (Color(0xFFFAEEDA), Color(0xFF854F0B)),
  ];

  (Color bg, Color fg) get avatarColors {
    final i = patientId.hashCode.abs() % 4;
    return _avatarPalettes[i];
  }

  String get initials {
    final parts = displayName
        .trim()
        .split(RegExp(r'\s+'))
        .where((s) => s.isNotEmpty)
        .toList();
    if (parts.isEmpty) return '?';
    if (parts.length == 1) {
      final s = parts.first;
      return s.length >= 2
          ? s.substring(0, 2).toUpperCase()
          : s.toUpperCase();
    }
    return (parts.first[0] + parts.last[0]).toUpperCase();
  }

  String get lastSyncLabel {
    final t = lastActivityAt;
    if (t == null) return 'Not loaded yet';
    final diff = DateTime.now().difference(t);
    final minutes = diff.inMinutes;
    if (minutes < 60) {
      final m = minutes < 1 ? 1 : minutes;
      return '${m}m ago';
    }
    final hours = diff.inHours;
    if (hours < 24) return '${hours}h ago';
    return '${diff.inDays}d ago';
  }

  String get joinedLabel {
    final j = joinedAt;
    if (j == null) return '';
    return 'Joined ${DateFormat('MMM d').format(j)}';
  }

  bool get isNew {
    final j = joinedAt;
    if (j == null) return false;
    return DateTime.now().difference(j) <= const Duration(days: 3);
  }
}

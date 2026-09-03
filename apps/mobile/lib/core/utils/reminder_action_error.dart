import 'dart:convert';

import 'package:flutter/material.dart';

import 'locale_format.dart';

/// Turns a failed reminder-action response into something a patient can read.
///
/// Lives here rather than in one screen because the same 409 was already
/// handled in reminders_screen and still fell through to "Action Failed" on
/// reminder_detail_screen — two screens, two behaviours, for one backend
/// contract. Any screen that posts complete/snooze/skip should use this so a
/// third cannot regress it again.
String reminderActionMessage(
  BuildContext context, {
  required int statusCode,
  required String body,
  String fallback = 'Could not update reminder',
}) {
  // 409 is the backend refusing on purpose: the dose is not due yet. It is not
  // a failure the user caused, so it must not read like one.
  if (statusCode == 409) {
    try {
      final decoded = jsonDecode(body);
      final detail = decoded is Map ? decoded['detail'] : null;
      // Snooze refuses through the same guard but must not borrow the
      // mark-done wording: the patient asked to postpone, not to complete.
      if (detail is Map && detail['error'] == 'not_due_yet') {
        final parsed =
            DateTime.tryParse(detail['scheduled_time']?.toString() ?? '');
        if (parsed != null) {
          return 'Not due until ${_whenLabel(context, parsed.toLocal())} — '
              'nothing to snooze yet.';
        }
        return "This isn't due yet, so there's nothing to snooze.";
      }
      if (detail is Map && detail['error'] == 'reminder_not_due') {
        final raw = detail['scheduled_time']?.toString();
        final parsed = raw != null ? DateTime.tryParse(raw) : null;
        if (parsed != null) {
          return 'Not due until ${_whenLabel(context, parsed.toLocal())}.';
        }
        final message = detail['message']?.toString();
        if (message != null && message.isNotEmpty) return message;
      }
    } catch (_) {
      // Fall through to the generic not-due wording below.
    }
    return "This isn't due yet.";
  }
  return fallback;
}

/// "3:00 PM" for today, "Thursday at 3:00 PM" otherwise — a bare time is
/// ambiguous when the appointment is days away.
String _whenLabel(BuildContext context, DateTime when) {
  final now = DateTime.now();
  final time = LocaleFormat.time(context, when);
  final sameDay =
      when.year == now.year && when.month == now.month && when.day == now.day;
  if (sameDay) return time;

  final tomorrow = now.add(const Duration(days: 1));
  final isTomorrow = when.year == tomorrow.year &&
      when.month == tomorrow.month &&
      when.day == tomorrow.day;
  if (isTomorrow) return 'tomorrow at $time';

  const names = [
    'Monday',
    'Tuesday',
    'Wednesday',
    'Thursday',
    'Friday',
    'Saturday',
    'Sunday',
  ];
  return '${names[when.weekday - 1]} at $time';
}

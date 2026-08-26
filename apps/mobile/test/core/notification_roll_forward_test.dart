import 'package:flutter_test/flutter_test.dart';
import 'package:RemiMinder/core/services/notification_service.dart';

/// BUG 1: a reminder created for a time earlier today threw
/// "Invalid argument (scheduledDate): Must be a date in the future", so the row
/// saved and the patient got no alert.
void main() {
  final now = DateTime(2026, 8, 26, 14, 0); // 2pm

  group('next occurrence', () {
    test('time earlier today rolls to tomorrow, does not throw', () {
      final earlierToday = DateTime(2026, 8, 26, 9, 0); // 9am, already gone
      final next = NotificationService.nextOccurrenceForTest(
        earlierToday,
        isRecurring: false,
        recurrencePattern: 'once',
        now: now,
      );
      expect(next, isNotNull);
      expect(next!.isAfter(now), isTrue);
      expect(next, DateTime(2026, 8, 27, 9, 0));
    });

    test('future time today is left alone', () {
      final laterToday = DateTime(2026, 8, 26, 20, 0);
      final next = NotificationService.nextOccurrenceForTest(
        laterToday,
        isRecurring: false,
        recurrencePattern: 'once',
        now: now,
      );
      expect(next, laterToday);
    });

    test('daily recurring advances one day', () {
      final next = NotificationService.nextOccurrenceForTest(
        DateTime(2026, 8, 26, 8, 0),
        isRecurring: true,
        recurrencePattern: 'daily',
        now: now,
      );
      expect(next, DateTime(2026, 8, 27, 8, 0));
    });

    test('weekly recurring advances a week, not a day', () {
      final next = NotificationService.nextOccurrenceForTest(
        DateTime(2026, 8, 26, 8, 0),
        isRecurring: true,
        recurrencePattern: 'weekly',
        now: now,
      );
      expect(next, DateTime(2026, 9, 2, 8, 0));
    });

    test('every-other-day steps 2 days, not 1', () {
      // The over-medication case: a daily step here would fire twice as often
      // as prescribed.
      final next = NotificationService.nextOccurrenceForTest(
        DateTime(2026, 8, 26, 8, 0),
        isRecurring: true,
        recurrencePattern: 'daily',
        now: now,
        intervalCount: 2,
      );
      expect(next, DateTime(2026, 8, 28, 8, 0));
    });

    test('every-other-week steps 14 days', () {
      final next = NotificationService.nextOccurrenceForTest(
        DateTime(2026, 8, 26, 8, 0),
        isRecurring: true,
        recurrencePattern: 'weekly',
        now: now,
        intervalCount: 2,
      );
      expect(next, DateTime(2026, 9, 9, 8, 0));
    });

    test('intervalCount 1 is unchanged from before', () {
      final next = NotificationService.nextOccurrenceForTest(
        DateTime(2026, 8, 26, 8, 0),
        isRecurring: true,
        recurrencePattern: 'daily',
        now: now,
        intervalCount: 1,
      );
      expect(next, DateTime(2026, 8, 27, 8, 0));
    });

    test('a long-past time still lands in the future', () {
      final next = NotificationService.nextOccurrenceForTest(
        DateTime(2026, 1, 1, 8, 0),
        isRecurring: true,
        recurrencePattern: 'daily',
        now: now,
      );
      expect(next!.isAfter(now), isTrue);
    });
  });
}

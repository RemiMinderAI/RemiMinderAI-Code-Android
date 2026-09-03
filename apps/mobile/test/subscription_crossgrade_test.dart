import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:RemiMinder/core/services/revenuecat_service.dart';

/// A monthly subscriber had no way to reach annual. The guard refused every
/// purchase while any entitlement was active, so it blocked the cross-grade it
/// was never meant to catch — a lost upgrade, which costs more than the
/// duplicate charge it was written to prevent.
///
/// ActiveSubscription.matches existed the whole time and was never called on
/// the purchase path.
void main() {
  ActiveSubscription sub(String id) => ActiveSubscription(
        productIdentifier: id,
        willRenew: true,
        isSandbox: false,
      );

  late String screen;
  late String service;

  setUpAll(() {
    screen = File(
            'lib/features/patient/presentation/screens/upgrade_screen.dart')
        .readAsStringSync();
    service =
        File('lib/core/services/revenuecat_service.dart').readAsStringSync();
  });

  group('matches only the plan actually held', () {
    test('the same product matches', () {
      expect(sub('remi_monthly').matches('remi_monthly'), isTrue);
    });

    test("Play's base-plan suffix still matches its product", () {
      expect(sub('remi_monthly:base-plan').matches('remi_monthly'), isTrue);
    });

    test('the other plan does not match', () {
      expect(sub('remi_monthly').matches('remi_annual'), isFalse);
      expect(sub('remi_annual').matches('remi_monthly'), isFalse);
    });

    test('an unknown product does not match', () {
      expect(sub('remi_monthly').matches(null), isFalse);
      expect(sub('remi_monthly').matches(''), isFalse);
    });
  });

  group('the guard consults it', () {
    test('the purchase path refuses only a duplicate of the same plan', () {
      expect(
        screen.contains(
            'active.matches(package.storeProduct.identifier)'),
        isTrue,
        reason: 'blocking on `active != null` alone is what broke the upgrade',
      );
      expect(screen.contains('if (active != null) {\n'), isFalse);
    });

    test('the cross-grade tells the service what it replaces', () {
      expect(screen.contains('replacing: active?.productIdentifier'), isTrue);
    });

    test('the button is dead only for the plan already held', () {
      expect(screen.contains('_selectedPlanIsCurrent'), isTrue);
      expect(
        screen.contains('_active != null ||\n                                _packageFor'),
        isFalse,
        reason: 'the old condition disabled the button for both plans',
      );
    });

    test('the label distinguishes the two cases', () {
      expect(screen.contains('You already have this plan'), isTrue);
      expect(screen.contains('Switch to this plan'), isTrue);
    });
  });

  group('what the stores actually need', () {
    test('Play is told which subscription is being replaced', () {
      expect(service.contains('GoogleProductChangeInfo'), isTrue);
      expect(service.contains('Platform.isAndroid'), isTrue);
    });

    test('iOS is not, because StoreKit handles the group itself', () {
      final start = service.indexOf('Future<bool> purchase(');
      final body = service.substring(start, service.indexOf('\n  }', start));
      // The change info is Android-gated; nothing extra is passed on iOS.
      expect(body.contains('Platform.isAndroid && oldProductId != null'), isTrue);
    });

    test('the base-plan suffix is stripped for Play', () {
      final start = service.indexOf('Future<bool> purchase(');
      final body = service.substring(start, service.indexOf('\n  }', start));
      expect(body.contains("replacing?.split(':').first"), isTrue);
    });
  });
}

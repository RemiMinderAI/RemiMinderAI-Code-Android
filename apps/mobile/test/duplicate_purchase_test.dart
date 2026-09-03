import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:RemiMinder/core/services/revenuecat_service.dart';

/// A user on the annual plan could buy the monthly plan on top, and the store
/// took the second payment. Nothing errored — that is the point.
void main() {
  ActiveSubscription sub(String productId, {bool renew = true}) =>
      ActiveSubscription(
        productIdentifier: productId,
        willRenew: renew,
        isSandbox: false,
        expiresAt: DateTime(2027, 8, 30),
      );

  group('identifying which plan is running', () {
    test('matches the configured product id', () {
      expect(sub('remiminder_annual').matches('remiminder_annual'), isTrue);
    });

    test('matches a base id with a store variant suffix', () {
      // Play appends ":base-plan"; the offering lookup already allows for it,
      // so the entitlement check has to as well or an annual subscriber reads
      // as "some other plan" and the message goes vague.
      expect(sub('remiminder_annual:p1y').matches('remiminder_annual'), isTrue);
    });

    test('does not match a different plan', () {
      expect(sub('remiminder_annual').matches('remiminder_monthly'), isFalse);
    });

    test('an empty or missing configured id never matches', () {
      expect(sub('remiminder_annual').matches(''), isFalse);
      expect(sub('remiminder_annual').matches(null), isFalse);
    });
  });

  group('the upgrade screen cannot take a second payment', () {
    late String source;
    late String purchaseBody;

    setUpAll(() {
      source = File(
        'lib/features/patient/presentation/screens/upgrade_screen.dart',
      ).readAsStringSync();
      final start = source.indexOf('Future<void> _purchaseSelectedPlan()');
      purchaseBody = source.substring(
        start,
        source.indexOf('Future<void> _showAlreadySubscribed(', start),
      );
    });

    test('it checks the live entitlement before charging', () {
      final check = purchaseBody.indexOf('activeSubscription()');
      final charge = purchaseBody.indexOf('RevenueCatService().purchase(');
      expect(check, greaterThan(-1), reason: 'no entitlement check at all');
      expect(charge, greaterThan(check),
          reason: 'the check must come before the charge');
    });

    test('it does not trust the cached isPremium flag', () {
      // isPremium is a value from app start; a purchase decision needs the
      // store's current answer.
      expect(purchaseBody.contains('RevenueCatService().isPremium'), isFalse);
    });

    test('an unreadable entitlement refuses instead of charging', () {
      final catchIndex = purchaseBody.indexOf('} catch (_) {');
      final charge = purchaseBody.indexOf('RevenueCatService().purchase(');
      expect(catchIndex, greaterThan(-1));
      expect(catchIndex, lessThan(charge),
          reason: '"unknown" must not be read as "not subscribed"');
      expect(purchaseBody.contains('could not check your current subscription'),
          isTrue);
    });

    test('an active subscription returns before the charge', () {
      expect(purchaseBody.contains('_showAlreadySubscribed(active)'), isTrue);
      final guard = purchaseBody.indexOf('_showAlreadySubscribed(active)');
      final charge = purchaseBody.indexOf('RevenueCatService().purchase(');
      expect(guard, lessThan(charge));
    });
  });

  group('the screen does not invite a second purchase', () {
    late String source;

    setUpAll(() {
      source = File(
        'lib/features/patient/presentation/screens/upgrade_screen.dart',
      ).readAsStringSync();
    });

    test('the buy button is dead only for the plan already held', () {
      // This asserted `_active != null`, which disabled the button for BOTH
      // plans and left a monthly subscriber unable to reach annual. Refusing a
      // duplicate is right; refusing the cross-grade was the bug.
      final btn = source.indexOf('onPressed: (_loading ||');
      expect(btn, greaterThan(-1));
      final condition = source.substring(btn, btn + 200);
      expect(condition.contains('_selectedPlanIsCurrent'), isTrue);
      expect(condition.contains('_active != null'), isFalse);
    });

    test('the other plan is still offered, as a switch', () {
      expect(source.contains('Switch to this plan'), isTrue);
      expect(source.contains('You already have this plan'), isTrue);
    });

    test('the current plan and its renewal date are shown', () {
      expect(source.contains('_currentPlanBanner('), isTrue);
      // The store's own record — the number worth trusting, given the purchase
      // sheet showed a date that could not be reconciled.
      expect(source.contains('LocaleFormat.dateMedium(context, renews)'), isTrue);
      expect(source.contains("'Renews'"), isTrue);
    });

    test('there is a way to change the plan, not just a refusal', () {
      expect(source.contains('Manage subscription'), isTrue);
      expect(source.contains('_openManagement('), isTrue);
    });
  });
}

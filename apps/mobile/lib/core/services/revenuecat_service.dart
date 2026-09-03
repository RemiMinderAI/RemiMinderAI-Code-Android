import 'dart:io' show Platform;
import 'dart:async';
import 'package:flutter/foundation.dart';

import 'backend_api_service.dart';
import 'package:purchases_flutter/purchases_flutter.dart';
import '../config/environment.dart';

/// What the store says the user is currently paying for.
///
/// The cached [RevenueCatService.isPremium] bool was never enough to stop a
/// second purchase: it says whether Premium is on, not which plan is running or
/// when it renews, so an annual subscriber could be sold a monthly plan on top
/// with nothing in the way.
class ActiveSubscription {
  const ActiveSubscription({
    required this.productIdentifier,
    required this.willRenew,
    required this.isSandbox,
    this.expiresAt,
    this.managementUrl,
  });

  final String productIdentifier;
  final bool willRenew;

  /// True for a TestFlight / sandbox purchase, where the store compresses a
  /// year into minutes. Worth surfacing: it is the difference between "the
  /// renewal date looks wrong" and "the product duration IS wrong".
  final bool isSandbox;

  final DateTime? expiresAt;

  /// Where the store lets the user change or cancel. We cannot change a plan
  /// for them — only send them to the place that can.
  final String? managementUrl;

  bool matches(String? productId) {
    if (productId == null || productId.isEmpty) return false;
    return productIdentifier == productId ||
        productIdentifier.startsWith('$productId:');
  }
}

class RevenueCatService {
  static final RevenueCatService _instance = RevenueCatService._();
  factory RevenueCatService() => _instance;
  RevenueCatService._();

  bool _initialized = false;
  bool _isPremium = false;

  bool get isPremium => _isPremium;

  Future<void> initialize() async {
    if (_initialized) return;
    try {
      final apiKey = Environment.revenueCatApiKey;
      if (apiKey.isEmpty) {
        if (kDebugMode) print('⚠️ RevenueCat: No API key, skipping init');
        return;
      }
      final config = PurchasesConfiguration(apiKey);
      await Purchases.configure(config);
      _initialized = true;
      if (kDebugMode) print('✅ RevenueCat initialized');
    } catch (e) {
      if (kDebugMode) print('❌ RevenueCat init failed: $e');
    }
  }

  Future<void> login(String firebaseUid) async {
    if (!_initialized) return;
    try {
      await Purchases.logIn(firebaseUid);
      await refreshPremiumStatus();
      if (kDebugMode) print('✅ RevenueCat login: $firebaseUid');
    } catch (e) {
      if (kDebugMode) print('❌ RevenueCat login failed: $e');
    }
  }

  Future<void> logout() async {
    if (!_initialized) return;
    try {
      await Purchases.logOut();
      _isPremium = false;
    } catch (e) {
      if (kDebugMode) print('❌ RevenueCat logout failed: $e');
    }
  }

  /// Report entitlement state to the backend.
  ///
  /// The server treats this as a claim and verifies it against RevenueCat — it
  /// is what stops a paid subscription from living only in this process's
  /// memory while users.plan stays TRIAL.
  Future<void> _syncToBackend(String source, {String? productId}) async {
    try {
      final appUserId = await Purchases.appUserID;
      await BackendApiService().syncSubscription(
        premium: _isPremium,
        appUserId: appUserId,
        productId: productId,
        source: source,
      );
    } catch (e) {
      if (kDebugMode) print('❌ Backend subscription sync failed: $e');
    }
  }

  Future<void> refreshPremiumStatus() async {
    if (!_initialized) return;
    try {
      final info = await Purchases.getCustomerInfo();
      _isPremium = info.entitlements.active.containsKey('Premium');
      if (kDebugMode) print('🔑 Premium status: $_isPremium');
      // Self-healing path: covers a sync that failed at purchase time, and a
      // reinstall where the entitlement is restored silently.
      if (_isPremium) await _syncToBackend('app_start');
    } catch (e) {
      if (kDebugMode) print('❌ RevenueCat status check failed: $e');
    }
  }


  /// The live subscription, straight from the store — never the cached bool.
  ///
  /// Read before any purchase. Trusting [isPremium] would let a stale value
  /// from app start authorise a second charge.
  Future<ActiveSubscription?> activeSubscription() async {
    if (!_initialized) return null;
    try {
      final info = await Purchases.getCustomerInfo();
      _isPremium = info.entitlements.active.containsKey('Premium');
      final entitlement = info.entitlements.active['Premium'];
      if (entitlement == null) return null;
      return ActiveSubscription(
        productIdentifier: entitlement.productIdentifier,
        willRenew: entitlement.willRenew,
        isSandbox: entitlement.isSandbox,
        expiresAt: DateTime.tryParse(entitlement.expirationDate ?? '')?.toLocal(),
        managementUrl: info.managementURL,
      );
    } catch (e) {
      if (kDebugMode) print('❌ RevenueCat active subscription check failed: $e');
      // Unknown is not "no subscription" — the caller must not read a failure
      // here as permission to charge again.
      rethrow;
    }
  }

  Future<List<Package>> getOfferings() async {
    if (!_initialized) return [];
    try {
      final offerings = await Purchases.getOfferings();
      if (offerings.current != null) {
        return offerings.current!.availablePackages;
      }
    } catch (e) {
      if (kDebugMode) print('❌ RevenueCat offerings failed: $e');
    }
    return [];
  }

  /// [replacing] is the product the user is already subscribed to, when this
  /// purchase is a plan change rather than a first subscription.
  ///
  /// The two stores differ. StoreKit treats a purchase inside the same
  /// subscription group as an upgrade and handles proration itself, so iOS
  /// needs nothing extra. Play needs to be told which subscription is being
  /// replaced — without it a plan change can stack a second subscription
  /// beside the first instead of swapping it.
  Future<bool> purchase(Package package, {String? replacing}) async {
    if (!_initialized) return false;
    try {
      // Play identifies the subscription, not the base plan, so strip any
      // ":base-plan" suffix RevenueCat reports.
      final oldProductId = replacing?.split(':').first;
      final result = await Purchases.purchasePackage(
        package,
        googleProductChangeInfo:
            (Platform.isAndroid && oldProductId != null && oldProductId.isNotEmpty)
                // ignore: deprecated_member_use
                ? GoogleProductChangeInfo(oldProductId)
                : null,
      );
      _isPremium = result.customerInfo.entitlements.active.containsKey('Premium');
      await _syncToBackend('purchase',
          productId: package.storeProduct.identifier);
      return _isPremium;
    } on PurchasesErrorCode catch (e) {
      if (e == PurchasesErrorCode.purchaseCancelledError) {
        if (kDebugMode) print('Purchase cancelled');
      } else {
        if (kDebugMode) print('❌ Purchase failed: $e');
      }
      return false;
    } catch (e) {
      if (kDebugMode) print('❌ Purchase failed: $e');
      return false;
    }
  }

  Future<void> restorePurchases() async {
    if (!_initialized) return;
    try {
      final info = await Purchases.restorePurchases();
      _isPremium = info.entitlements.active.containsKey('Premium');
      await _syncToBackend('restore');
    } catch (e) {
      if (kDebugMode) print('❌ Restore failed: $e');
    }
  }
}

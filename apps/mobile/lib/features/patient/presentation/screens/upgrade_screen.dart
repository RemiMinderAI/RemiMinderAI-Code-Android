import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:purchases_flutter/purchases_flutter.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../../core/config/environment.dart';
import '../../../../core/models/monetization_status.dart';
import '../../../../core/services/revenuecat_service.dart';
import '../../../../core/utils/locale_format.dart';

enum _BillingPlan { monthly, yearly }

/// Premium upgrade: monthly / annual RevenueCat subscription.
class UpgradeScreen extends StatefulWidget {
  const UpgradeScreen({super.key});

  @override
  State<UpgradeScreen> createState() => _UpgradeScreenState();
}

class _UpgradeScreenState extends State<UpgradeScreen> {
  _BillingPlan _selectedPlan = _BillingPlan.monthly;
  bool _loading = false;
  bool _loadingOfferings = true;
  Package? _monthlyPackage;
  Package? _annualPackage;

  /// What the store says is already running, if anything.
  ActiveSubscription? _active;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadOfferings();
  }

  Future<void> _loadOfferings() async {
    try {
      final packages = await RevenueCatService().getOfferings();
      // Read before showing plans: someone who already pays should not be
      // invited to pay again.
      ActiveSubscription? active;
      try {
        active = await RevenueCatService().activeSubscription();
      } catch (_) {
        active = null; // shown as normal; the pre-purchase check still runs
      }
      debugPrint('UPGRADE_DEBUG: initialized=${RevenueCatService().isPremium}, packages=${packages.length}, ids=${packages.map((p) => p.storeProduct.identifier).toList()}');
      Package? byProductId(String productId) {
        for (final package in packages) {
          final id = package.storeProduct.identifier;
          if (id == productId || id.startsWith('$productId:')) return package;
        }
        return null;
      }

      Package? byType(PackageType type) {
        for (final package in packages) {
          if (package.packageType == type) return package;
        }
        return null;
      }

      if (!mounted) return;
      setState(() {
        _active = active;
        _monthlyPackage = byProductId(Environment.revenueCatMonthlyProductId) ??
            byType(PackageType.monthly);
        _annualPackage = byProductId(Environment.revenueCatAnnualProductId) ??
            byType(PackageType.annual);
        _loadingOfferings = false;
        if (_monthlyPackage == null && _annualPackage == null) {
          _error = 'Premium plans are not available in your store yet.';
        } else if (_monthlyPackage == null) {
          _selectedPlan = _BillingPlan.yearly;
        } else if (_annualPackage == null) {
          _selectedPlan = _BillingPlan.monthly;
        }
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = 'Could not load Premium options. Please try again.';
        _loadingOfferings = false;
      });
    }
  }

  Future<void> _purchaseSelectedPlan() async {
    if (_loading) return;
    final package = _selectedPlan == _BillingPlan.monthly
        ? _monthlyPackage
        : _annualPackage;
    if (package == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Premium options are still loading.')),
      );
      return;
    }

    setState(() => _loading = true);
    try {
      // Checked live, not from the cached isPremium flag: an annual subscriber
      // could otherwise be charged again for a monthly plan, and the store
      // would happily take the second payment.
      ActiveSubscription? active;
      try {
        active = await RevenueCatService().activeSubscription();
      } catch (_) {
        // The store could not tell us. Refuse rather than risk a double
        // charge — "unknown" is not "not subscribed".
        if (!mounted) return;
        setState(() => _loading = false);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'We could not check your current subscription. '
              'Please try again in a moment.',
            ),
          ),
        );
        return;
      }
      if (active != null) {
        if (!mounted) return;
        setState(() {
          _active = active;
          _loading = false;
        });
        await _showAlreadySubscribed(active);
        return;
      }

      final premium = await RevenueCatService().purchase(package);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            premium
                ? 'Premium is active. All premium features are unlocked.'
                : 'Purchase completed, but Premium is not active yet. Please try restore purchases.',
          ),
        ),
      );
      if (premium) _closeUpgrade();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Purchase could not be completed: $e')),
      );
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  /// Tell them what they already have, and send them where they can change it.
  ///
  /// Refusing is only half the job — a patient who wanted to switch plans still
  /// needs somewhere to go, and only the store can move them.
  Future<void> _showAlreadySubscribed(ActiveSubscription active) async {
    final plan = active.matches(Environment.revenueCatAnnualProductId)
        ? 'annual'
        : active.matches(Environment.revenueCatMonthlyProductId)
            ? 'monthly'
            : null;
    final renews = active.expiresAt;
    final when = renews == null
        ? ''
        : ' It ${active.willRenew ? 'renews' : 'ends'} on '
            '${LocaleFormat.dateMedium(context, renews)}.';

    await showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('You already have Premium'),
        content: Text(
          plan == null
              ? 'Your Premium subscription is active, so there is nothing to '
                  'buy.$when'
              : 'You are on the $plan plan, so there is nothing to buy.$when',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: const Text('Close'),
          ),
          if ((active.managementUrl ?? '').isNotEmpty)
            TextButton(
              onPressed: () {
                Navigator.of(dialogContext).pop();
                _openManagement(active.managementUrl!);
              },
              child: const Text('Manage subscription'),
            ),
        ],
      ),
    );
  }

  Future<void> _openManagement(String url) async {
    try {
      final launched = await launchUrl(
        Uri.parse(url),
        mode: LaunchMode.externalApplication,
      );
      if (!launched && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Manage your subscription in your device settings.'),
          ),
        );
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Manage your subscription in your device settings.'),
        ),
      );
    }
  }

  Future<void> _restorePurchases() async {
    if (_loading) return;
    setState(() => _loading = true);
    try {
      await RevenueCatService().restorePurchases();
      final premium = RevenueCatService().isPremium;
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            premium
                ? 'Purchases restored. Premium is active.'
                : 'No active Premium subscription was found.',
          ),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Restore failed: $e')),
      );
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Package? _packageFor(_BillingPlan plan) =>
      plan == _BillingPlan.monthly ? _monthlyPackage : _annualPackage;

  String _priceFor(_BillingPlan plan) {
    final package = _packageFor(plan);
    final price = package?.storeProduct.priceString.trim();
    if (price != null && price.isNotEmpty) return price;
    return '—';
  }

  String _periodFor(_BillingPlan plan) {
    final package = _packageFor(plan);
    switch (package?.packageType) {
      case PackageType.monthly:
        return '/ month';
      case PackageType.annual:
        return '/ year';
      default:
        return plan == _BillingPlan.monthly ? '/ month' : '/ year';
    }
  }

  String _titleFor(_BillingPlan plan) {
    final package = _packageFor(plan);
    final title = package?.storeProduct.title.trim();
    if (title != null && title.isNotEmpty) {
      switch (package?.packageType) {
        case PackageType.monthly:
          return 'Monthly';
        case PackageType.annual:
          return 'Annual';
        default:
          break;
      }
    }
    return plan == _BillingPlan.monthly ? 'Monthly' : 'Annual';
  }

  /// Savings vs 12x monthly, computed from live store prices (null if unavailable).
  String? _annualSavingsBadge() {
    final monthly = _monthlyPackage?.storeProduct.price;
    final annual = _annualPackage?.storeProduct.price;
    if (monthly == null || annual == null || monthly <= 0 || annual <= 0) {
      return null;
    }
    final yearlyIfMonthly = monthly * 12;
    if (yearlyIfMonthly <= annual) return null;
    final pct = (((yearlyIfMonthly - annual) / yearlyIfMonthly) * 100).round();
    if (pct <= 0) return null;
    return 'Save $pct%';
  }

  void _closeUpgrade() {
    if (Navigator.of(context).canPop()) {
      Navigator.of(context).pop();
    } else {
      context.go('/patient/home');
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Scaffold(
      backgroundColor: theme.scaffoldBackgroundColor,
      body: SafeArea(
        child: Column(
          children: [
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(vertical: 20, horizontal: 16),
              decoration: const BoxDecoration(
                gradient: LinearGradient(
                  colors: [Color(0xFF1A3A5C), Color(0xFF0F2640)],
                  begin: Alignment.centerLeft,
                  end: Alignment.centerRight,
                ),
              ),
              child: Row(
                children: [
                  IconButton(
                    icon: const Icon(Icons.arrow_back, color: Colors.white),
                    onPressed: _closeUpgrade,
                  ),
                  const Expanded(
                    child: Text(
                      'Upgrade to Pro',
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 22,
                        fontWeight: FontWeight.w700,
                      ),
                      textAlign: TextAlign.center,
                    ),
                  ),
                  const SizedBox(width: 48),
                ],
              ),
            ),
            Expanded(
              child: SingleChildScrollView(
                padding: const EdgeInsets.fromLTRB(16, 24, 16, 120),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    Container(
                      width: 120,
                      height: 120,
                      decoration: BoxDecoration(
                        color: theme.colorScheme.primary,
                        shape: BoxShape.circle,
                        border: Border.all(color: const Color(0xFFC9A84C), width: 4),
                        boxShadow: [
                          BoxShadow(
                            color: const Color(0xFFC9A84C).withOpacity(0.28),
                            blurRadius: 24,
                            offset: const Offset(0, 8),
                          ),
                        ],
                      ),
                      child: const Center(
                        child: FittedBox(
                          fit: BoxFit.scaleDown,
                          child: Text(
                            'Pro',
                          style: TextStyle(
                            color: Colors.white,
                            fontSize: 15,
                            fontWeight: FontWeight.w800,
                          ),
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: 20),
                    Text(
                      'Your health, simplified',
                      style: theme.textTheme.headlineSmall?.copyWith(
                        fontWeight: FontWeight.w800,
                        color: theme.colorScheme.primary,
                      ),
                      textAlign: TextAlign.center,
                    ),
                    const SizedBox(height: 8),
                    Text(
                      'Your AI healthcare companion helps you manage medications, track doctor visits, and stay connected with caregivers.',
                      style: theme.textTheme.bodyLarge?.copyWith(
                        color: const Color(0xFF2D2D2D),
                        height: 1.35,
                      ),
                      textAlign: TextAlign.center,
                    ),
                    const SizedBox(height: 28),
                    Container(
                      padding: const EdgeInsets.all(22),
                      decoration: BoxDecoration(
                        color: const Color(0xFFE6F0FA),
                        borderRadius: BorderRadius.circular(20),
                      ),
                      child: Column(
                        children: [
                          _buildBenefitItem('Unlimited doctor visit summaries', theme),
                          const SizedBox(height: 14),
                          _buildBenefitItem('Priority support', theme),
                          const SizedBox(height: 14),
                          _buildBenefitItem(
                            'Invite up to ${MonetizationLimits.premiumCaregiverLimit} caregivers',
                            theme,
                          ),
                          const SizedBox(height: 14),
                          _buildBenefitItem('Future premium AI features', theme),
                        ],
                      ),
                    ),
                    const SizedBox(height: 28),
                    if (_loadingOfferings)
                      const Padding(
                        padding: EdgeInsets.all(16),
                        child: CircularProgressIndicator(),
                      )
                    else if (_error != null)
                      Text(
                        _error!,
                        style: TextStyle(color: theme.colorScheme.error),
                        textAlign: TextAlign.center,
                      ),
                    if (!_loadingOfferings && _active != null)
                      _currentPlanBanner(theme, _active!),
                    if (!_loadingOfferings &&
                        (_monthlyPackage != null || _annualPackage != null))
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          if (_monthlyPackage != null)
                            Expanded(
                              child: _planCard(
                                theme: theme,
                                title: _titleFor(_BillingPlan.monthly),
                                price: _priceFor(_BillingPlan.monthly),
                                period: _periodFor(_BillingPlan.monthly),
                                selected: _selectedPlan == _BillingPlan.monthly,
                                onTap: () => setState(
                                  () => _selectedPlan = _BillingPlan.monthly,
                                ),
                              ),
                            ),
                          if (_monthlyPackage != null && _annualPackage != null)
                            const SizedBox(width: 14),
                          if (_annualPackage != null)
                            Expanded(
                              child: _planCard(
                                theme: theme,
                                title: _titleFor(_BillingPlan.yearly),
                                price: _priceFor(_BillingPlan.yearly),
                                period: _periodFor(_BillingPlan.yearly),
                                selected: _selectedPlan == _BillingPlan.yearly,
                                highlight: true,
                                badge: _annualSavingsBadge(),
                                onTap: () => setState(
                                  () => _selectedPlan = _BillingPlan.yearly,
                                ),
                              ),
                            ),
                        ],
                      ),
                    const SizedBox(height: 28),
                    SizedBox(
                      width: double.infinity,
                      child: ElevatedButton(
                        // Disabled while a subscription is already running:
                        // the pre-purchase check would refuse anyway, and an
                        // enabled button that always refuses is worse than one
                        // that never invited the tap.
                        onPressed: (_loading ||
                                _active != null ||
                                _packageFor(_selectedPlan) == null)
                            ? null
                            : _purchaseSelectedPlan,
                        style: ElevatedButton.styleFrom(
                          backgroundColor: theme.colorScheme.primary,
                          foregroundColor: Colors.white,
                          padding: const EdgeInsets.symmetric(vertical: 18),
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                        ),
                        child: _loading
                            ? const SizedBox(
                                height: 22,
                                width: 22,
                                child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                              )
                            : Text(
                                _active != null
                                    ? 'You already have Premium'
                                    : 'Continue with Premium',
                                style: const TextStyle(
                                    fontSize: 16, fontWeight: FontWeight.w700),
                              ),
                      ),
                    ),
                    const SizedBox(height: 8),
                    TextButton(
                      onPressed: _loading ? null : _restorePurchases,
                      child: const Text('Restore purchases'),
                    ),
                    TextButton(
                      onPressed: _loading ? null : _closeUpgrade,
                      child: Text(
                        "No thanks, I'll stay on the free plan",
                        style: TextStyle(
                          color: theme.colorScheme.onSurface.withOpacity(0.7),
                          fontSize: 14,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// What they are already paying for, shown above the plans.
  ///
  /// The renewal date here comes from the store's own entitlement record, so
  /// it is the number to trust — Apple's purchase sheet showed a date that
  /// could not be reconciled, and this is the one place we can state it
  /// authoritatively.
  Widget _currentPlanBanner(ThemeData theme, ActiveSubscription active) {
    final plan = active.matches(Environment.revenueCatAnnualProductId)
        ? 'Annual'
        : active.matches(Environment.revenueCatMonthlyProductId)
            ? 'Monthly'
            : null;
    final renews = active.expiresAt;

    return Container(
      margin: const EdgeInsets.only(bottom: 20),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: theme.colorScheme.primary.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: theme.colorScheme.primary.withValues(alpha: 0.3),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.check_circle, color: theme.colorScheme.primary),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  plan == null
                      ? 'Premium is active'
                      : 'Premium is active — $plan plan',
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                if (renews != null) ...[
                  const SizedBox(height: 4),
                  Text(
                    '${active.willRenew ? 'Renews' : 'Ends'} on '
                    '${LocaleFormat.dateMedium(context, renews)}',
                    style: TextStyle(color: theme.colorScheme.secondary),
                  ),
                ],
                if (active.isSandbox && kDebugMode) ...[
                  const SizedBox(height: 4),
                  const Text(
                    'Sandbox purchase — store durations are compressed here.',
                    style: TextStyle(fontSize: 12, fontStyle: FontStyle.italic),
                  ),
                ],
                if ((active.managementUrl ?? '').isNotEmpty) ...[
                  const SizedBox(height: 4),
                  TextButton(
                    onPressed: () => _openManagement(active.managementUrl!),
                    style: TextButton.styleFrom(
                      padding: EdgeInsets.zero,
                      minimumSize: const Size(44, 36),
                      alignment: Alignment.centerLeft,
                    ),
                    child: const Text('Manage subscription'),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _planCard({
    required ThemeData theme,
    required String title,
    required String price,
    required String period,
    required bool selected,
    required VoidCallback onTap,
    bool highlight = false,
    String? badge,
  }) {
    final borderColor = selected
        ? const Color(0xFFC9A84C)
        : theme.colorScheme.primary.withOpacity(highlight ? 0.3 : 0.2);
    final borderWidth = selected ? 2.5 : 2.0;
    final bg = highlight ? const Color(0xFFC9A84C).withOpacity(0.12) : Colors.white;

    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: borderColor, width: borderWidth),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withOpacity(highlight ? 0.08 : 0.05),
              blurRadius: 10,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        child: Column(
          children: [
            if (badge != null)
              Container(
                margin: const EdgeInsets.only(bottom: 8),
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: const Color(0xFFC9A84C),
                  borderRadius: BorderRadius.circular(999),
                ),
                child: Text(
                  badge,
                  style: TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.w800,
                    color: theme.colorScheme.primary,
                  ),
                ),
              ),
            Text(
              title,
              style: theme.textTheme.titleMedium?.copyWith(
                fontWeight: FontWeight.w700,
                color: theme.colorScheme.primary,
              ),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            Text(
              price,
              style: theme.textTheme.headlineSmall?.copyWith(
                fontWeight: FontWeight.w800,
                color: theme.colorScheme.primary,
              ),
            ),
            Text(
              period,
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurface.withOpacity(0.6),
              ),
            ),
            const SizedBox(height: 10),
            Text(
              selected ? 'Selected' : 'Cancel anytime',
              style: theme.textTheme.bodySmall?.copyWith(
                color: selected
                    ? theme.colorScheme.primary
                    : theme.colorScheme.onSurface.withOpacity(0.7),
                fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
              ),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildBenefitItem(String benefit, ThemeData theme) {
    return Row(
      children: [
        Container(
          width: 24,
          height: 24,
          decoration: const BoxDecoration(
            color: Color(0xFFC9A84C),
            shape: BoxShape.circle,
          ),
          child: Icon(Icons.check, color: theme.colorScheme.primary, size: 16),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Text(
            benefit,
            style: theme.textTheme.bodyMedium?.copyWith(
              fontWeight: FontWeight.w600,
              color: const Color(0xFF2D2D2D),
            ),
          ),
        ),
      ],
    );
  }
}

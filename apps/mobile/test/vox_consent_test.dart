import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:RemiMinder/core/services/vox_consent_service.dart';
import 'package:RemiMinder/features/patient/presentation/widgets/vox_consent_dialog.dart';
import 'package:RemiMinder/l10n/app_localizations.dart';

/// Records what the gate did, without touching Firebase or the network.
class _FakeConsent implements VoxConsentService {
  _FakeConsent(this.accepted);
  bool accepted;
  int recorded = 0;

  @override
  Future<bool> hasAccepted() async => accepted;

  @override
  Future<void> recordAcceptance() async {
    recorded++;
    accepted = true;
  }

  @override
  noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Widget _host(Future<bool> Function(BuildContext) run, {Locale? locale}) {
  return MaterialApp(
    locale: locale,
    localizationsDelegates: const [
      AppLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    supportedLocales: AppLocalizations.supportedLocales,
    home: Builder(
      builder: (context) => Scaffold(
        body: ElevatedButton(
          onPressed: () => run(context),
          child: const Text('tap'),
        ),
      ),
    ),
  );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('consent gate', () {
    testWidgets('first use shows the dialog and blocks until answered',
        (tester) async {
      final fake = _FakeConsent(false);
      bool? result;
      await tester.pumpWidget(_host((c) async {
        result = await ensureVoxConsent(c, service: fake);
        return result!;
      }));
      await tester.tap(find.text('tap'));
      await tester.pumpAndSettle();

      expect(find.text('Before you use voice'), findsOneWidget);
      expect(result, isNull, reason: 'must not proceed while unanswered');
    });

    testWidgets('Continue is disabled until the box is ticked', (tester) async {
      final fake = _FakeConsent(false);
      await tester.pumpWidget(_host((c) => ensureVoxConsent(c, service: fake)));
      await tester.tap(find.text('tap'));
      await tester.pumpAndSettle();

      final button = tester.widget<FilledButton>(find.byType(FilledButton));
      expect(button.onPressed, isNull, reason: 'dismissal is not agreement');

      await tester.tap(find.byType(Checkbox));
      await tester.pumpAndSettle();
      final ticked = tester.widget<FilledButton>(find.byType(FilledButton));
      expect(ticked.onPressed, isNotNull);
    });

    testWidgets('declining returns false and records nothing', (tester) async {
      final fake = _FakeConsent(false);
      bool? result;
      await tester.pumpWidget(_host((c) async {
        result = await ensureVoxConsent(c, service: fake);
        return result!;
      }));
      await tester.tap(find.text('tap'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Not now'));
      await tester.pumpAndSettle();

      expect(result, isFalse);
      expect(fake.recorded, 0, reason: 'a decline must persist nothing');
    });

    testWidgets('accepting returns true and is remembered', (tester) async {
      final fake = _FakeConsent(false);
      bool? result;
      await tester.pumpWidget(_host((c) async {
        result = await ensureVoxConsent(c, service: fake);
        return result!;
      }));
      await tester.tap(find.text('tap'));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(Checkbox));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Continue'));
      await tester.pumpAndSettle();

      expect(result, isTrue);
      expect(fake.recorded, 1);

      // Second tap: already agreed, so no dialog and no second record.
      await tester.tap(find.text('tap'));
      await tester.pumpAndSettle();
      expect(find.text('Before you use voice'), findsNothing);
      expect(fake.recorded, 1);
    });

    testWidgets('strings resolve in a non-English locale', (tester) async {
      final fake = _FakeConsent(false);
      await tester.pumpWidget(_host(
        (c) => ensureVoxConsent(c, service: fake),
        locale: const Locale('fr'),
      ));
      await tester.tap(find.text('tap'));
      await tester.pumpAndSettle();

      expect(find.text("Avant d'utiliser la voix"), findsOneWidget);
      expect(find.text('Pas maintenant'), findsOneWidget);
      expect(find.text('Continuer'), findsOneWidget);
    });
  });

  group('stored decision is per user, not per device', () {
    test('two users on one device get separate keys', () {
      final a = VoxConsentService.prefsKeyFor('uid-patient');
      final b = VoxConsentService.prefsKeyFor('uid-caregiver');
      expect(a, isNot(b));
      expect(a.contains('uid-patient'), isTrue);
    });

    test("one user's acceptance does not answer for another", () async {
      SharedPreferences.setMockInitialValues({
        VoxConsentService.prefsKeyFor('uid-patient'): true,
      });
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getBool(VoxConsentService.prefsKeyFor('uid-patient')), isTrue);
      expect(prefs.getBool(VoxConsentService.prefsKeyFor('uid-caregiver')),
          isNull,
          reason: 'a shared family tablet must ask the second person too');
    });
  });

  group('the gate is actually wired into the tap handler', () {
    // Four times in this codebase a function has been written, unit-tested and
    // never called. Everything above proves the gate WORKS; this proves it is
    // REACHED — and fails if anyone later moves it below a recording call.
    late String handleTap;

    setUpAll(() {
      final source = File(
        'lib/features/patient/presentation/widgets/patient_app_shell.dart',
      ).readAsStringSync();
      final start = source.indexOf('Future<void> _handleTap() async {');
      expect(start, greaterThan(-1), reason: '_handleTap not found');
      final end = source.indexOf('\n  Future<', start + 10);
      handleTap = source.substring(start, end);
    });

    test('_handleTap calls ensureVoxConsent', () {
      expect(handleTap.contains('ensureVoxConsent(context)'), isTrue);
    });

    test('a refusal returns instead of falling through', () {
      expect(
        handleTap.contains('if (!await ensureVoxConsent(context)) return;'),
        isTrue,
        reason: 'the result must gate the turn, not merely be computed',
      );
    });

    test('it runs before every path that opens the microphone', () {
      final gate = handleTap.indexOf('ensureVoxConsent');
      for (final starter in [
        '_runCareAskLoop',
        '_startPipecatStream',
        '_recordPromptClip',
      ]) {
        final at = handleTap.indexOf(starter);
        if (at == -1) continue;
        expect(gate, lessThan(at),
            reason: 'consent must precede $starter');
      }
    });

    test('it runs after the availability check, so a blocked user is not asked',
        () {
      expect(handleTap.indexOf('_refreshVoxAvailability'),
          lessThan(handleTap.indexOf('ensureVoxConsent')));
    });

    test('_handleTap is the only way the button starts anything', () {
      final source = File(
        'lib/features/patient/presentation/widgets/patient_app_shell.dart',
      ).readAsStringSync();
      // One gesture, bound to the gated handler. A long-press or double-tap
      // handler added later would be a second door into the microphone.
      expect(RegExp(r'onTap:\s*_handleTap').hasMatch(source), isTrue);
      expect(source.contains('onLongPress:'), isFalse);
      expect(source.contains('onDoubleTap:'), isFalse);
    });

    test('nothing opens the microphone outside the gated handler', () {
      final source = File(
        'lib/features/patient/presentation/widgets/patient_app_shell.dart',
      ).readAsStringSync();
      // Every call site of a recording starter must be inside _handleTap
      // (after the gate) or inside _runCareAskLoop, which _handleTap owns.
      final loopStart = source.indexOf('Future<void> _runCareAskLoop(');
      final tapStart = source.indexOf('Future<void> _handleTap() async {');
      final tapEnd = source.indexOf('\n  Future<', tapStart + 10);
      for (final starter in ['_startPipecatStream(', '_runCareAskLoop(']) {
        for (final m in RegExp(RegExp.escape(starter)).allMatches(source)) {
          final at = m.start;
          if (source.startsWith('Future<', at - 8)) continue; // declaration
          final isDeclaration =
              source.lastIndexOf('\n', at) + 3 == at - starter.length ||
                  source.substring(0, at).endsWith('Future<void> ');
          if (isDeclaration) continue;
          final insideTap = at > tapStart && at < tapEnd;
          final insideLoop = loopStart != -1 && at > loopStart;
          expect(insideTap || insideLoop, isTrue,
              reason: '$starter called outside the consent-gated path');
        }
      }
    });
  });
}

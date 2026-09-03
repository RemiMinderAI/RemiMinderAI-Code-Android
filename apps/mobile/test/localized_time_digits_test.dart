import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'package:RemiMinder/core/config/supported_languages.dart';
import 'package:RemiMinder/core/utils/locale_format.dart';
import 'package:RemiMinder/l10n/app_localizations.dart';

/// Reminders rendered as "8:0 AM". The minute lost its leading zero when the
/// digits were rewritten for the active locale: "00" parses to 0, formats back
/// to a single "0", and nothing put the second digit back.
///
/// Two things had to be right. The zero has to be restored at all, and it has
/// to be the locale's own zero — Bengali numbers as U+09E6, so padding with an
/// ASCII "0" wrote the minute in two scripts.
Future<String> _localize(WidgetTester tester, String code, String text) async {
  late String out;
  await tester.pumpWidget(
    MaterialApp(
      locale: Locale(code),
      supportedLocales: kSupportedLanguageCodes.map(Locale.new),
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: Builder(
        builder: (context) {
          out = LocaleFormat.localizeDigitsInText(context, text);
          return const SizedBox.shrink();
        },
      ),
    ),
  );
  return out;
}

void main() {
  testWidgets('a zero minute keeps both digits in every supported locale',
      (tester) async {
    for (final code in kSupportedLanguageCodes) {
      final out = await _localize(tester, code, '8:00 AM');
      final minutes = out.split(':').last.replaceAll(RegExp(r'[^\p{Nd}]', unicode: true), '');
      expect(minutes.characters.length, 2,
          reason: '$code rendered "$out" — the minute lost a digit');
    }
  });

  testWidgets('the padding digit comes from the same script as the number',
      (tester) async {
    for (final code in kSupportedLanguageCodes) {
      final out = await _localize(tester, code, '8:00 AM');
      final digits = out.replaceAll(RegExp(r'[^\p{Nd}]', unicode: true), '');
      // Every digit in the result must sit in one contiguous Unicode block.
      final blocks = digits.runes.map((r) => r ~/ 16).toSet();
      expect(blocks.length, lessThanOrEqualTo(1),
          reason: '$code mixed scripts in "$out"');
    }
  });

  testWidgets('non-zero minutes and other numbers are untouched',
      (tester) async {
    expect(await _localize(tester, 'en', '8:45 AM'), '8:45 AM');
    expect(await _localize(tester, 'en', '12:05 PM'), '12:05 PM');
    expect(await _localize(tester, 'en', 'in 30 minutes'), 'in 30 minutes');
  });

  testWidgets('a bare zero is still a single digit', (tester) async {
    // Only a run that HAD leading zeros gets padded back.
    expect(await _localize(tester, 'en', '0 reminders'), '0 reminders');
  });
}

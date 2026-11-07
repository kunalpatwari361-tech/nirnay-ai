import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nirnay_app/l10n/app_localizations.dart';
import 'package:nirnay_app/screens/doc_read_screen.dart';
import 'package:nirnay_app/services/models.dart';

// The document check screen: the engine's verdict decides, and no one is asked to confirm what was read.
void main() {
  // A 1x1 PNG, so the photo renders.
  final png = Uint8List.fromList([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01,
    0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4, 0x89, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41,
    0x54, 0x78, 0x9C, 0x63, 0xF8, 0xCF, 0xC0, 0xF0, 0x1F, 0x00, 0x05, 0x00, 0x01, 0xFF, 0x89, 0x99, 0x3D, 0x1D, 0x00, 0x00,
    0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
  ]);

  Future<DocReadNext?> show(WidgetTester tester, DocResult result, String button) async {
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);
    DocReadNext? next;
    await tester.pumpWidget(MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: Builder(
        builder: (context) => Scaffold(
          body: TextButton(
            onPressed: () async => next = await Navigator.push<DocReadNext>(
              context,
              MaterialPageRoute(builder: (_) => DocReadScreen(slot: DocSlot.pan, bytes: png, result: result)),
            ),
            child: const Text('open'),
          ),
        ),
      ),
    ));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('looks-right')), findsNothing);
    expect(find.text('Looks right'), findsNothing);
    await tester.tap(find.byKey(Key(button)));
    await tester.pumpAndSettle();
    return next;
  }

  DocResult pan(String verdict, {int? score, bool accepted = true, Map<String, bool> checks = const {}, String? code}) => DocResult(
        slot: DocSlot.pan,
        detectedType: accepted ? 'pan' : 'aadhaar',
        confidence: 0.97,
        accepted: accepted,
        reasonCode: code,
        fields: accepted ? {'name': 'KAVYA RAO', 'pan': 'AKVPR4821K'} : const {},
        verification: DocVerification(verdict: verdict, score: score, checks: checks),
      );

  testWidgets('a verified document shows its score and just continues', (tester) async {
    final next = await show(tester, pan('verified', score: 96, checks: {'type': true, 'readable': true, 'name': true, 'pan': true}),
        'continue');
    expect(next, DocReadNext.keep);
  });

  testWidgets('a document that failed verification goes to the decision, with no retake', (tester) async {
    final r = pan('failed', score: 10, checks: {'type': true, 'readable': true, 'name': true, 'pan': false});
    tester.view.physicalSize = const Size(1080, 2400);
    await tester.pumpWidget(MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: DocReadScreen(slot: DocSlot.pan, bytes: png, result: r),
    ));
    await tester.pumpAndSettle();
    expect(find.text("This document didn't pass verification"), findsOneWidget);
    expect(find.text('Score 10/100'), findsOneWidget);
    expect(find.textContaining('is rejected'), findsOneWidget);
    expect(find.byKey(const Key('retake')), findsNothing);
    expect(find.byKey(const Key('see-decision')), findsOneWidget);
    expect(await show(tester, r, 'see-decision'), DocReadNext.decide);
  });

  testWidgets("a document that couldn't be checked is retaken", (tester) async {
    final next = await show(tester, pan('retake', accepted: false, code: 'R-DOC-01', checks: {'type': false, 'readable': true}), 'retake');
    expect(next, DocReadNext.retake);
  });
}

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nirnay_app/l10n/app_localizations.dart';
import 'package:nirnay_app/widgets/score_meter.dart';

// The score meters on the decision screen: a credit report that wasn't pulled, and a grade worked
// out without one, never look like a final result.
void main() {
  Future<void> show(WidgetTester tester, Widget meter) async {
    await tester.pumpWidget(MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: Scaffold(body: Center(child: SizedBox(width: 200, child: meter))),
    ));
    await tester.pumpAndSettle();
  }

  testWidgets('a pulled score shows its band', (tester) async {
    await show(tester, const ScoreMeter(title: 'Credit score', score: 765, caption: 'out of 900'));
    expect(find.text('765'), findsOneWidget);
    expect(find.text('Excellent'), findsOneWidget);
  });

  testWidgets('a credit report that was not pulled shows no score', (tester) async {
    await show(tester, const ScoreMeter(title: 'Credit score', score: null, caption: 'Not pulled yet: an officer pulls your credit report'));
    expect(find.text('—'), findsOneWidget);
    expect(find.text('Not pulled yet: an officer pulls your credit report'), findsOneWidget);
  });

  testWidgets('a grade worked out without a credit report is marked provisional, not as a final grade', (tester) async {
    await show(
        tester,
        const ScoreMeter(
            title: 'Application score',
            score: 646,
            caption: 'Worked out without your credit report. An officer confirms it once the report is pulled.',
            badge: 'Provisional',
            provisional: true));
    expect(find.text('646'), findsOneWidget);
    expect(find.text('Provisional'), findsOneWidget);
    expect(find.text('Grade D'), findsNothing);
    expect(find.text('Below our limit'), findsNothing); // no band lit up for a provisional score
  });
}

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../config.dart';
import '../format.dart';
import '../l10n/app_localizations.dart';
import '../policy.dart';
import '../services/models.dart';
import '../state.dart';
import '../theme.dart';
import '../widgets/common.dart';

/// Welcome, in simple flat colours: the loan card with what the customer told us (and the
/// sanctioned amount once the loan is approved), tracking, live checks, the calculators and the
/// demo customers. The language is picked from the button in the header.
class WelcomeScreen extends StatelessWidget {
  const WelcomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context);
    final state = context.watch<AppState>();
    final resume = resumePoint(state);
    final p = state.profile;
    final hour = DateTime.now().hour;
    final greeting = hour < 12 ? t.greetMorning : (hour < 17 ? t.greetAfternoon : t.greetEvening);

    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.dark,
      child: Scaffold(
        backgroundColor: C.page,
        body: SafeArea(
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 560),
              child: ListView(padding: const EdgeInsets.fromLTRB(16, 10, 16, 28), children: [
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  _Header(initials: p == null ? null : _initials(p.name)),
                  const SizedBox(height: 12),
                  if (demoMode) ...[_DemoNote(text: t.sandboxBanner), const SizedBox(height: 16)],
                  Text(greeting, style: const TextStyle(color: C.muted, fontSize: 13)),
                  const SizedBox(height: 2),
                  Text(p == null ? t.welcomeHeadline : t.nearlyThere(p.name.split(' ').first),
                      style: const TextStyle(color: C.ink, fontSize: 24, fontWeight: FontWeight.w800, height: 1.2)),
                  if (p == null) ...[
                    const SizedBox(height: 6),
                    Text(t.welcomeBody, style: const TextStyle(color: C.muted, fontSize: 13, height: 1.4)),
                  ],
                  const SizedBox(height: 20),
                  const _LoanCard(),
                  const SizedBox(height: 16),
                  Row(children: [
                    Expanded(child: _ProductChip(product: Product.homeLoan, icon: Icons.home_outlined, label: t.productHomeLoan)),
                    const SizedBox(width: 8),
                    Expanded(child: _ProductChip(product: Product.lap, icon: Icons.domain_outlined, label: t.productLap)),
                  ]),
                  const SizedBox(height: 16),
                  if (resume == null)
                    FilledButton(key: const Key('start'), onPressed: () => Navigator.pushNamed(context, '/sign-in'), child: Text(t.startApplication))
                  else ...[
                    _Progress(step: resume.step),
                    const SizedBox(height: 16),
                    _label(t.liveChecks),
                    const _LiveChecks(),
                    const SizedBox(height: 14),
                    _ResumeCard(point: resume),
                  ],
                  const SizedBox(height: 26),
                  _label(t.calculators),
                  Row(children: [
                    Expanded(child: _Tile(key: const Key('calc-emi'), title: t.calcEmi, sub: t.calcEmiSub, onTap: () => _emiSheet(context))),
                    const SizedBox(width: 8),
                    Expanded(
                      child: _Tile(key: const Key('calc-eligibility'), title: t.calcEligibility, sub: t.calcEligibilitySub, onTap: () => _eligibilitySheet(context)),
                    ),
                  ]),
                  if (demoMode) ...[
                    const SizedBox(height: 26),
                    _label(t.demoCustomers),
                    Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: Text(t.demoCustomersBody, style: const TextStyle(fontSize: 12.5, color: C.muted)),
                    ),
                    _Persona(phone: '9876500001', label: t.personaRavi, product: Product.homeLoan),
                    _Persona(phone: '9876500002', label: t.personaMeena, product: Product.lap),
                    _Persona(phone: '9876500003', label: t.personaArjun, product: Product.homeLoan),
                  ],
                ]),
              ]),
            ),
          ),
        ),
      ),
    );
  }

  static String _initials(String name) => name.split(' ').where((w) => w.isNotEmpty).take(2).map((w) => w.characters.first).join().toUpperCase();

  /// Where a signed-in customer who came back should continue, which step that is, and how far along they are.
  static ({String route, int step, double done})? resumePoint(AppState s) {
    if (s.session == null || s.finalDecision != null) return null;
    if (!s.allConsented) return (route: '/consent', step: 2, done: 0.15);
    if (s.profile == null) return (route: '/details', step: 3, done: 0.3);
    if (!(s.selfieResult?.passed ?? false)) return (route: '/selfie', step: 4, done: 0.45);
    if (s.inPrinciple != null) return (route: '/decision', step: 6, done: 0.8);
    return (route: '/documents', step: 5, done: 0.6);
  }

  /// Every stage of the application, from sign-in to disbursement: what's done, where it is now, and what's left.
  static List<TimelineStep> trackSteps(AppLocalizations t, AppState s) {
    final first = s.inPrinciple, last = s.finalDecision;
    String? stopped(Decision? d) => switch (d?.outcome) {
          Outcome.refer => t.tlOfficerNext,
          Outcome.reject => t.statusDeclined,
          _ => null,
        };
    final docs = s.requiredDocs;
    final rows = <(String, bool, String?)>[
      (t.stageSignIn, s.session != null, null),
      (t.consentTitle, s.allConsented, null),
      (t.stageDetails, s.profile != null, null),
      (t.stageKyc, s.selfieResult?.passed ?? false, null),
      (t.checkDocs, first != null || (docs.isNotEmpty && s.docsComplete), null),
      (first?.outcome == Outcome.refer ? t.tlOfficer : t.tlInPrinciple, first?.outcome == Outcome.approve || last != null, stopped(first)),
      (t.tlProperty, last != null, t.tlPropertyNext),
      (last?.outcome == Outcome.refer ? t.tlOfficer : t.tlSanction, false, last == null ? null : (stopped(last) ?? t.tlSanctionNext)),
      (t.tlDisbursement, false, null),
    ];
    final now = rows.indexWhere((r) => !r.$2);
    return [
      for (var i = 0; i < rows.length; i++)
        TimelineStep(
          rows[i].$1,
          i < now ? TrackState.done : (i == now ? TrackState.current : TrackState.todo),
          detail: i == now ? rows[i].$3 : null,
        ),
    ];
  }
}

Widget _label(String text) => Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Text(text.toUpperCase(), style: const TextStyle(color: C.muted, fontSize: 11.5, fontWeight: FontWeight.w800, letterSpacing: 1.2)),
    );

BoxDecoration _box({double radius = 16, Color border = C.line}) => BoxDecoration(
      color: C.card,
      borderRadius: BorderRadius.circular(radius),
      border: Border.all(color: border),
    );

class _Header extends StatelessWidget {
  const _Header({this.initials});
  final String? initials;

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context);
    return Row(children: [
      Container(
        width: 30,
        height: 30,
        alignment: Alignment.center,
        decoration: BoxDecoration(color: C.brand, borderRadius: BorderRadius.circular(9)),
        child: const Text('N', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 15)),
      ),
      const SizedBox(width: 10),
      Expanded(child: Text(t.appTitle, style: const TextStyle(color: C.ink, fontSize: 17, fontWeight: FontWeight.w800))),
      const LanguageToggle(),
      if (initials != null)
        Container(
          width: 36,
          height: 36,
          alignment: Alignment.center,
          decoration: const BoxDecoration(shape: BoxShape.circle, color: C.brandTint),
          child: Text(initials!, style: const TextStyle(color: C.brand, fontSize: 12, fontWeight: FontWeight.w800)),
        ),
    ]);
  }
}

class _DemoNote extends StatelessWidget {
  const _DemoNote({required this.text});
  final String text;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(color: C.warnTint, borderRadius: BorderRadius.circular(8), border: Border.all(color: C.warnLine)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.science_outlined, size: 14, color: C.warnInk),
          const SizedBox(width: 6),
          Flexible(child: Text(text, style: const TextStyle(color: C.warnInk, fontSize: 11.5, fontWeight: FontWeight.w600))),
        ]),
      );
}

/// The loan card, in one flat navy. It shows only what the customer has told us: nothing made up
/// before they fill in their details, then what they asked for and the details they gave. Only once
/// the loan is approved does it show the approved (or, after the final decision, the sanctioned)
/// amount with its rate and EMI. "Track" shows where the application is.
class _LoanCard extends StatelessWidget {
  const _LoanCard();

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context);
    final s = context.watch<AppState>();
    final p = s.profile;
    final id = s.session?.applicationId;
    final d = s.finalDecision ?? s.inPrinciple;
    final offer = d != null && d.outcome == Outcome.approve ? d : null; // only an approved loan shows its amount
    final approved = offer != null;
    final product = s.product == Product.homeLoan ? t.productHomeLoan : t.productLap;
    final status = _status(t, s);

    final (String? heading, double? amount, String? note) = offer != null
        ? (s.finalDecision != null ? t.cardSanctionedAmount : t.approvedInPrinciple, offer.amount, null)
        : p != null
            ? (t.cardAsked, s.loanAmount, t.cardSanctionLater)
            : (null, null, id == null ? t.cardNotStarted : t.cardAddDetails); // no default amounts shown as theirs
    final facts = <(String, String)>[
      if (p != null) (t.nameLabel, p.name),
      if (offer != null) ...[
        (t.kfsRate, t.ratePa(offer.ratePct.toStringAsFixed(2))),
        (t.kfsEmi, rupees(offer.emi)),
        (t.tenureLabel, t.yearsLabel('${offer.years}')),
        (t.cardAsked, rupees(s.loanAmount)),
      ] else if (p != null)
        (t.tenureLabel, t.yearsLabel('${s.years}')),
      if (p != null) ...[
        (t.propertyValueLabel, rupees(s.propertyValue)),
        (t.incomeLabel, rupees(p.monthlyIncome)),
        if (p.employer.trim().isNotEmpty) (t.fieldEmployer, p.employer),
        if (p.panMasked.isNotEmpty) (t.fieldPan, p.panMasked),
      ],
    ];

    const soft = TextStyle(color: C.onBrandMuted, fontSize: 12);
    return Container(
      key: const Key('loan-card'),
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 16),
      decoration: BoxDecoration(color: C.brand, borderRadius: BorderRadius.circular(18)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(product.toUpperCase(),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w800, letterSpacing: 1.4)),
              if (id != null) Text(t.applicationNo(id), key: const Key('card-application'), style: soft),
            ]),
          ),
          if (status != null) _StatusPill(key: const Key('card-status'), text: status.$1, color: status.$2),
        ]),
        const SizedBox(height: 16),
        if (heading != null) Text(heading, style: soft.copyWith(fontSize: 12.5)),
        if (amount != null)
          FittedBox(
            fit: BoxFit.scaleDown,
            alignment: AlignmentDirectional.centerStart,
            child: Text(rupees(amount),
                key: Key(approved ? 'card-sanctioned' : 'card-asked'),
                style: const TextStyle(color: Colors.white, fontSize: 30, fontWeight: FontWeight.w800)),
          ),
        if (note != null)
          Text(note,
              key: const Key('card-note'),
              style: amount == null ? const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w600, height: 1.35) : soft.copyWith(height: 1.35)),
        if (facts.isNotEmpty) ...[
        const SizedBox(height: 14),
        Container(height: 1, color: Colors.white.withValues(alpha: 0.18)),
        const SizedBox(height: 12),
        LayoutBuilder(builder: (context, box) {
          final w = (box.maxWidth - 12) / 2;
          return Wrap(spacing: 12, runSpacing: 10, children: [
            for (final (label, value) in facts)
              SizedBox(
                width: w,
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(label, maxLines: 1, overflow: TextOverflow.ellipsis, style: soft),
                  Text(value,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w700)),
                ]),
              ),
          ]);
        }),
        ],
        if (id != null) ...[
          const SizedBox(height: 14),
          FilledButton.icon(
            key: const Key('track'),
            style: FilledButton.styleFrom(
              backgroundColor: Colors.white,
              foregroundColor: C.brand,
              minimumSize: const Size.fromHeight(44),
              textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w800),
            ),
            onPressed: () => _trackSheet(context),
            icon: const Icon(Icons.timeline, size: 18),
            label: Text(t.trackTitle),
          ),
        ],
      ]),
    );
  }

  /// The application's status in a word, and its colour; nothing before the customer signs in.
  static (String, Color)? _status(AppLocalizations t, AppState s) {
    if (s.session == null) return null;
    final d = s.finalDecision ?? s.inPrinciple;
    final last = s.finalDecision != null;
    return switch (d?.outcome) {
      Outcome.approve => (last ? t.statusSanctioned : t.statusApproved, C.goodInk),
      Outcome.refer => (t.statusInReview, C.warnInk),
      Outcome.reject => (t.statusDeclined, C.badInk),
      null => (t.statusInProgress, C.brand),
    };
  }
}

class _StatusPill extends StatelessWidget {
  const _StatusPill({super.key, required this.text, required this.color});
  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(20)),
        child: Text(text, style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w800)),
      );
}

/// Track your application: every stage, from sign-in to disbursement, and a button to the next step.
void _trackSheet(BuildContext context) {
  final t = AppLocalizations.of(context);
  final s = context.read<AppState>();
  final id = s.session?.applicationId;
  final resume = WelcomeScreen.resumePoint(s);
  final route = resume?.route ?? (s.finalDecision != null || s.inPrinciple != null ? '/decision' : null);
  showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    builder: (sheet) => SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(t.trackTitle, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
          if (id != null) Text(t.applicationNo(id), style: const TextStyle(color: C.muted, fontSize: 13)),
          const SizedBox(height: 16),
          Timeline(key: const Key('track-timeline'), steps: WelcomeScreen.trackSteps(t, s)),
          if (route != null) ...[
            const SizedBox(height: 16),
            FilledButton(
              key: const Key('track-open'),
              onPressed: () {
                Navigator.pop(sheet);
                Navigator.pushNamed(context, route);
              },
              child: Text(resume != null && resume.route != '/decision' ? t.continueApplication : t.seeDecision),
            ),
          ],
        ]),
      ),
    ),
  );
}

class _ProductChip extends StatelessWidget {
  const _ProductChip({required this.product, required this.icon, required this.label});
  final Product product;
  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final on = state.product == product;
    return Material(
      color: on ? C.brandTint : C.card,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14), side: BorderSide(color: on ? C.brand : C.line, width: on ? 1.6 : 1)),
      child: InkWell(
        key: Key('product-${product.name}'),
        borderRadius: BorderRadius.circular(14),
        onTap: () => state.setProduct(product),
        child: Container(
          constraints: const BoxConstraints(minHeight: 48),
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          child: Row(children: [
            Icon(icon, size: 18, color: on ? C.brand : C.muted),
            const SizedBox(width: 8),
            Expanded(
              child: Text(label,
                  maxLines: 2, style: TextStyle(color: on ? C.brand : C.ink, fontSize: 13, fontWeight: on ? FontWeight.w800 : FontWeight.w600)),
            ),
          ]),
        ),
      ),
    );
  }
}

/// "Step 4 of 7 · KYC" with a segmented bar.
class _Progress extends StatelessWidget {
  const _Progress({required this.step});
  final int step;

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context);
    final stages = [t.stageSignIn, t.stageDetails, t.stageKyc, t.stageOffer, t.stageProperty];
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
      decoration: _box(),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(
            child: Text('${t.stepOf('$step', '7')} · ${stages[FlowStepper.stageOf(step)]}',
                style: const TextStyle(color: C.ink, fontSize: 13.5, fontWeight: FontWeight.w700)),
          ),
          Text(t.minutesLeft('${(8 - step).clamp(1, 7)}'), style: const TextStyle(color: C.muted, fontSize: 12)),
        ]),
        const SizedBox(height: 10),
        Row(children: [
          for (var i = 1; i <= 7; i++) ...[
            Expanded(
              child: Container(
                height: 5,
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(3),
                  color: i < step ? C.brand : (i == step ? C.brand.withValues(alpha: 0.4) : C.line),
                ),
              ),
            ),
            if (i < 7) const SizedBox(width: 4),
          ],
        ]),
      ]),
    );
  }
}

/// What the engine has checked so far, from the app's own state.
class _LiveChecks extends StatelessWidget {
  const _LiveChecks();

  /// The credit score tile: the bureau's score, or the score on the report the applicant uploaded
  /// when no bureau answered. "Not pulled yet" is never shown as "no credit history".
  static (String, String, Color?) _bureau(AppLocalizations t, AppState s) {
    final p = s.profile;
    if (p == null) return (t.checkBureau, t.checkPending, null);
    if (p.bureauPulled || p.bureauStatus == null) {
      return (t.checkBureau, p.bureauScore == null ? t.checkNoHistory : t.checkScore('${p.bureauScore}'), C.good);
    }
    final report = s.documents[DocSlot.creditReport];
    final score = (report?.verified ?? false) ? report!.fields['score'] : null;
    return score != null ? (t.checkBureau, t.checkScore(score), C.good) : (t.checkBureau, t.checkNotPulled, null);
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context);
    final s = context.watch<AppState>();
    final p = s.profile;
    final done = s.requiredDocs.where((d) => s.documents[d]?.accepted ?? false).length;
    final total = s.requiredDocs.length;
    final tiles = [
      _bureau(t, s),
      // Real mode has no Account Aggregator: the bank statement is read once it's uploaded.
      realMode
          ? ((s.documents[DocSlot.bankStatement]?.verified ?? false)
              ? (t.checkBank, t.verified, C.good)
              : (t.checkBank, t.checkPending, null))
          : (t.checkBank, p == null ? t.checkPending : t.checkBankRead, p == null ? null : C.good),
      (t.checkKyc, (s.selfieResult?.passed ?? false) ? t.verified : t.checkPending, (s.selfieResult?.passed ?? false) ? C.good : null),
      (t.checkDocs, t.countOf('$done', '$total'), done == total ? C.good : (done > 0 ? C.warnInk : null)),
    ];
    return LayoutBuilder(builder: (context, box) {
      final w = (box.maxWidth - 8) / 2;
      return Wrap(spacing: 8, runSpacing: 8, children: [
        for (final (label, value, dot) in tiles)
          Container(
            width: w,
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
            decoration: _box(radius: 14, border: dot == C.warnInk ? C.warnLine : C.line),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Container(
                width: 8,
                height: 8,
                margin: const EdgeInsets.only(top: 5, right: 8),
                decoration: BoxDecoration(shape: BoxShape.circle, color: dot ?? C.idle),
              ),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(label, style: const TextStyle(color: C.muted, fontSize: 11.5)),
                  Text(value, style: const TextStyle(color: C.ink, fontSize: 13.5, fontWeight: FontWeight.w800)),
                ]),
              ),
            ]),
          ),
      ]);
    });
  }
}

class _ResumeCard extends StatelessWidget {
  const _ResumeCard({required this.point});
  final ({String route, int step, double done}) point;

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context);
    final pct = (point.done * 100).round();
    return Material(
      key: const Key('resume-card'),
      color: C.card,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16), side: const BorderSide(color: C.brand)),
      child: InkWell(
        key: const Key('resume'),
        borderRadius: BorderRadius.circular(16),
        onTap: () => Navigator.pushNamed(context, point.route),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 12, 12),
          child: Row(children: [
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(t.continueApplication, style: const TextStyle(color: C.ink, fontSize: 15, fontWeight: FontWeight.w800)),
                Text(t.resumeBody('$pct'), style: const TextStyle(color: C.muted, fontSize: 12)),
              ]),
            ),
            Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(color: C.brand, borderRadius: BorderRadius.circular(12)),
              child: const Icon(Icons.arrow_forward, color: Colors.white),
            ),
          ]),
        ),
      ),
    );
  }
}

class _Tile extends StatelessWidget {
  const _Tile({super.key, required this.title, required this.sub, required this.onTap});
  final String title, sub;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Material(
        color: C.card,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14), side: const BorderSide(color: C.line)),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Row(children: [
              Container(
                width: 34,
                height: 34,
                decoration: BoxDecoration(color: C.brandTint, borderRadius: BorderRadius.circular(10)),
                child: const Icon(Icons.calculate_outlined, size: 19, color: C.brand),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(title, style: const TextStyle(color: C.ink, fontSize: 13.5, fontWeight: FontWeight.w700)),
                  Text(sub, style: const TextStyle(color: C.muted, fontSize: 11.5), maxLines: 2),
                ]),
              ),
            ]),
          ),
        ),
      );
}

class _Persona extends StatelessWidget {
  const _Persona({required this.phone, required this.label, required this.product});
  final String phone, label;
  final Product product;

  @override
  Widget build(BuildContext context) {
    final state = context.read<AppState>();
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Material(
        color: C.card,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14), side: const BorderSide(color: C.line)),
        child: InkWell(
          key: Key('persona-$phone'),
          borderRadius: BorderRadius.circular(14),
          onTap: () {
            state.setProduct(product);
            state.update(() => state.phone = phone);
            Navigator.pushNamed(context, '/sign-in');
          },
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 10, 8, 10),
            child: Row(children: [
              Container(
                width: 38,
                height: 38,
                alignment: Alignment.center,
                decoration: const BoxDecoration(shape: BoxShape.circle, color: C.brandTint),
                child: Text(label.characters.first, style: const TextStyle(color: C.brand, fontWeight: FontWeight.w800)),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(label, style: const TextStyle(color: C.ink, fontSize: 14, fontWeight: FontWeight.w600)),
                  Text('+91 $phone', style: const TextStyle(color: C.muted, fontSize: 12)),
                ]),
              ),
              const Icon(Icons.chevron_right, color: C.muted),
            ]),
          ),
        ),
      ),
    );
  }
}

/// A labelled slider row for the calculators.
Widget _slider(String label, String value, double v, double min, double max, int divisions, ValueChanged<double> onChanged) =>
    Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Expanded(child: Text(label, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700))),
        Text(value, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w800, color: C.brand)),
      ]),
      Slider(value: v.clamp(min, max), min: min, max: max, divisions: divisions, onChanged: onChanged),
    ]);

Widget _result(String label, String amount, List<String> lines) => SectionCard(
      tone: Tone.info,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: const TextStyle(fontSize: 12.5, color: C.brandDark)),
        Text(amount, style: serif(30, color: C.brandDark)),
        for (final l in lines) Text(l, style: const TextStyle(fontSize: 12.5, color: C.muted)),
      ]),
    );

void _emiSheet(BuildContext context) {
  final t = AppLocalizations.of(context);
  final state = context.read<AppState>();
  final lang = state.locale.languageCode;
  final rate = Policy.ratePct(state.product);
  final maxYears = Policy.maxTenureYears(state.product);
  var amount = state.loanAmount;
  var years = state.years.clamp(5, maxYears);
  showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    builder: (_) => StatefulBuilder(builder: (context, set) {
      final e = emi(amount, rate, years * 12);
      return Padding(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 28),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(t.emiCalcTitle, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
          const SizedBox(height: 12),
          _slider(t.loanAmountLabel, rupeesShort(amount, lang: lang), amount, 5e5, 2e7, 390, (v) => set(() => amount = (v / 1e5).round() * 1e5)),
          _slider(t.tenureLabel, t.yearsLabel('$years'), years.toDouble(), 5, maxYears.toDouble(), maxYears - 5, (v) => set(() => years = v.round())),
          const SizedBox(height: 8),
          _result(t.calcEmiSub, rupees(e), [t.totalInterest(rupees(e * years * 12 - amount)), t.rateNote(rate.toStringAsFixed(2))]),
        ]),
      );
    }),
  );
}

void _eligibilitySheet(BuildContext context) {
  final t = AppLocalizations.of(context);
  final state = context.read<AppState>();
  final lang = state.locale.languageCode;
  final rate = Policy.ratePct(state.product);
  final maxYears = Policy.maxTenureYears(state.product);
  var income = 100000.0, existing = 0.0;
  var years = state.years.clamp(5, maxYears);
  showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    builder: (_) => StatefulBuilder(builder: (context, set) {
      final maxEmi = (income * Policy.foirCap(income) - existing).clamp(0, double.infinity).toDouble();
      final loan = (loanForEmi(maxEmi, rate, years * 12) / 1000).floorToDouble() * 1000;
      return Padding(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 28),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(t.eligibilityCalcTitle, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
          const SizedBox(height: 12),
          _slider(t.incomeLabel, rupees(income), income, 15000, 500000, 97, (v) => set(() => income = (v / 5000).round() * 5000)),
          _slider(t.existingEmiLabel, rupees(existing), existing, 0, 150000, 60, (v) => set(() => existing = (v / 2500).round() * 2500)),
          _slider(t.tenureLabel, t.yearsLabel('$years'), years.toDouble(), 5, maxYears.toDouble(), maxYears - 5, (v) => set(() => years = v.round())),
          const SizedBox(height: 8),
          _result(t.eligibleTitle, rupeesShort(loan, lang: lang), [t.emiPerMonth(rupees(emi(loan, rate, years * 12))), t.calcIncomeOnly]),
        ]),
      );
    }),
  );
}

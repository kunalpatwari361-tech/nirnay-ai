import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../config.dart';
import '../format.dart';
import '../l10n/app_localizations.dart';
import '../policy.dart';
import '../services/browser.dart';
import '../services/models.dart';
import '../state.dart';
import '../theme.dart';
import '../widgets/common.dart';

/// Auto-filled details (sample engine) or the customer's own details (real mode), then the
/// quick eligibility check, before the full application. When the engine is connected to
/// DigiLocker, name, date of birth, PAN and Aadhaar come from there; the credit report is always
/// pulled by the engine with the PAN, never asked of the customer.
class DetailsScreen extends StatefulWidget {
  const DetailsScreen({super.key});

  @override
  State<DetailsScreen> createState() => _DetailsScreenState();
}

class _DetailsScreenState extends State<DetailsScreen> with WidgetsBindingObserver {
  final _pan = TextEditingController(text: realMode ? '' : 'ABCPK1234F');
  // Real mode: what the customer tells us. The engine marks it self-declared.
  final _name = TextEditingController(), _email = TextEditingController(), _employer = TextEditingController();
  final _income = TextEditingController(), _emi = TextEditingController(text: '0');
  final _address = TextEditingController();
  DateTime? _dob;
  Employment _work = Employment.salaried;
  bool _busy = false;
  Connections _links = const Connections(); // what the engine can fetch: DigiLocker, the credit bureau
  DigiLockerShare? _dl; // what DigiLocker shared, once the customer went there
  bool _dlBusy = false;

  DigiLockerShare? get _shared => _dl?.done == true ? _dl : null;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _loadLinks();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    for (final c in [_pan, _name, _email, _employer, _income, _emi, _address]) {
      c.dispose();
    }
    super.dispose();
  }

  // Back from the browser: ask the engine what DigiLocker shared.
  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    if (s == AppLifecycleState.resumed && _dl?.status == 'pending') _checkDigiLocker();
  }

  Future<void> _loadLinks() async {
    final state = context.read<AppState>();
    try {
      final links = await state.api.connections();
      if (!mounted) return;
      setState(() => _links = links);
      if (links.digilocker != null && state.session != null && state.profile == null) await _checkDigiLocker();
    } catch (_) {
      // No answer: the customer types and uploads as before.
    }
  }

  Future<void> _checkDigiLocker() async {
    final state = context.read<AppState>();
    try {
      final s = await state.api.digiLockerStatus(state.session!);
      if (!mounted) return;
      setState(() {
        _dl = s;
        if (s.done && s.pan != null) _pan.text = s.pan!;
      });
    } catch (_) {}
  }

  Future<void> _openDigiLocker() async {
    final t = AppLocalizations.of(context);
    final state = context.read<AppState>();
    setState(() => _dlBusy = true);
    try {
      await state.api.recordConsents(state.session!, state.consentRecord); // nothing is fetched before consent
      final url = await state.api.startDigiLocker(state.session!, returnToApp: !kIsWeb);
      if (url != null) {
        setState(() => _dl = DigiLockerShare(status: 'pending'));
        await openSignInPage(url);
      }
      await _checkDigiLocker();
    } catch (_) {
      if (mounted) showError(context, t.errorGeneric);
    } finally {
      if (mounted) setState(() => _dlBusy = false);
    }
  }

  Declaration? _declaration() {
    final dl = _shared;
    final needName = dl?.name == null, needDob = dl?.dateOfBirth == null;
    final income = double.tryParse(_income.text), emi = double.tryParse(_emi.text.isEmpty ? '0' : _emi.text);
    if ((needName && _name.text.trim().length < 2) || (needDob && _dob == null) || _employer.text.trim().length < 2 ||
        income == null || income <= 0 || emi == null) {
      return null;
    }
    return Declaration(
      name: needName ? _name.text.trim() : null,
      dateOfBirth: needDob ? _dob : null,
      employment: _work,
      employer: _employer.text.trim(),
      monthlyIncome: income,
      existingEmi: emi,
      email: _email.text.trim(),
      address: (dl?.address ?? '').isEmpty ? _address.text.trim() : null,
    );
  }

  Future<void> _pickDob() async {
    final now = DateTime.now();
    final d = await showDatePicker(
      context: context,
      initialDate: _dob ?? DateTime(now.year - 30, 1, 1),
      firstDate: DateTime(now.year - 80),
      lastDate: DateTime(now.year - 18, now.month, now.day),
      helpText: AppLocalizations.of(context).pickDob,
    );
    if (d != null) setState(() => _dob = d);
  }

  Future<void> _fetch() async {
    final t = AppLocalizations.of(context);
    final state = context.read<AppState>();
    final pan = _pan.text.trim().toUpperCase();
    if (!RegExp(r'^[A-Z]{5}[0-9]{4}[A-Z]$').hasMatch(pan)) return showError(context, t.invalidPan);
    final declared = realMode ? _declaration() : null;
    if (realMode && declared == null) return showError(context, _shared?.name != null ? t.fillWorkDetails : t.fillDetails);
    setState(() => _busy = true);
    try {
      state.update(() => state.pan = pan);
      await state.api.recordConsents(state.session!, state.consentRecord);
      state.setProfile(await state.api.fetchProfile(state.session!, pan, declared: declared));
    } catch (_) {
      if (mounted) showError(context, t.errorGeneric);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context);
    final state = context.watch<AppState>();
    final lang = state.locale.languageCode;
    final p = state.profile;
    final maxYears = Policy.maxTenureYears(state.product);
    if (state.years > maxYears) state.years = maxYears;

    return StepScaffold(
      title: t.detailsTitle,
      step: 3,
      busy: _busy,
      buttonLabel: p == null ? (realMode ? t.saveDetails : t.fetchDetails) : t.continueLabel,
      onPressed: p == null ? _fetch : () => Navigator.pushNamed(context, '/selfie'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Labelled(
            label: t.panLabel,
            child: TextField(
              key: const Key('pan'),
              controller: _pan,
              enabled: p == null && _shared?.pan == null, // DigiLocker's PAN record can't be retyped
              textCapitalization: TextCapitalization.characters,
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600, letterSpacing: 1),
              inputFormatters: [LengthLimitingTextInputFormatter(10)],
              decoration: InputDecoration(
                hintText: t.panHint,
                suffixIcon: p != null || _shared?.pan != null ? const Icon(Icons.check_circle, color: C.good) : null,
              ),
            ),
          ),
          if (p == null && _links.digilocker != null) ...[const SizedBox(height: 14), _digiLockerCard(t)],
          if (realMode && p == null) ..._declarationForm(t),
          if (p != null) ...[
            const SizedBox(height: 14),
            p.selfDeclared
                ? Notice(tone: Tone.info, icon: Icons.edit_note, body: t.selfDeclaredNote)
                : Notice(tone: Tone.good, icon: Icons.verified_user_outlined, body: t.fetchedFrom),
            const SizedBox(height: 14),
            SectionCard(
              child: Row(
                children: [
                  CircleAvatar(
                    radius: 22,
                    backgroundColor: C.brandTint,
                    child: Text(
                      p.name.characters.first,
                      style: const TextStyle(color: C.brand, fontWeight: FontWeight.w800, fontSize: 17),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(p.name, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w800)),
                        Text('${t.dobLabel}: ${DateFormat('d MMM y').format(p.dateOfBirth)}', style: const TextStyle(fontSize: 12.5, color: C.muted)),
                        Text(
                          p.aadhaarMasked.isEmpty ? 'PAN ${p.panMasked}' : 'PAN ${p.panMasked} · Aadhaar ${p.aadhaarMasked}',
                          style: const TextStyle(fontSize: 12.5, color: C.muted),
                        ),
                        if (p.identitySource == 'digilocker')
                          Padding(padding: const EdgeInsets.only(top: 6), child: Pill(t.dlDone, tone: Tone.good)),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 10),
            _bureauLine(t, p),
            const SizedBox(height: 16),
            Labelled(
              label: t.employmentLabel,
              child: Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 13),
                decoration: BoxDecoration(
                  color: C.brandTint,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: C.brand, width: 2),
                ),
                child: Text(
                  '${p.employment == Employment.salaried ? t.salaried : t.selfEmployed} · ${p.employer}',
                  style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: C.brand),
                ),
              ),
            ),
            const SizedBox(height: 14),
            Labelled(
              label: t.incomeLabel,
              child: _MoneyField(
                key: const Key('income'),
                value: state.income,
                verified: !p.selfDeclared && state.income == p.monthlyIncome ? t.verified : null,
                onChanged: (v) => state.update(() => state.income = v),
              ),
            ),
            const SizedBox(height: 14),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Labelled(
                    label: t.existingEmiLabel,
                    child: _MoneyField(key: const Key('emi'), value: state.existingEmi, onChanged: (v) => state.update(() => state.existingEmi = v)),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Labelled(
                    label: t.propertyValueLabel,
                    child: _MoneyField(
                      key: const Key('property-value'),
                      value: state.propertyValue,
                      onChanged: (v) => state.update(() => state.propertyValue = v),
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 20),
            SectionTitle(t.yourLoan),
            SectionCard(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
              child: Column(
                children: [
                  _sliderHead(t.loanAmountLabel, rupeesShort(state.loanAmount, lang: lang)),
                  Slider(
                    key: const Key('loan-slider'),
                    value: state.loanAmount.clamp(5e5, 2e7),
                    min: 5e5,
                    max: 2e7,
                    divisions: 390,
                    label: rupeesShort(state.loanAmount, lang: lang),
                    onChanged: (v) => state.update(() => state.loanAmount = v),
                  ),
                  _sliderHead(t.tenureLabel, t.yearsLabel('${state.years}')),
                  Slider(
                    value: state.years.toDouble(),
                    min: 5,
                    max: maxYears.toDouble(),
                    divisions: maxYears - 5,
                    label: t.yearsLabel('${state.years}'),
                    onChanged: (v) => state.update(() => state.years = v.round()),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),
            _EligibilityCard(),
          ],
        ],
      ),
    );
  }

  /// DigiLocker: one tap instead of typing the name and date of birth and photographing the cards.
  Widget _digiLockerCard(AppLocalizations t) {
    const muted = TextStyle(fontSize: 12.5, color: C.muted);
    final dl = _dl, shared = _shared;
    if (shared != null) {
      final ids = [
        if ((shared.panMasked ?? '').isNotEmpty) 'PAN ${shared.panMasked}',
        if ((shared.aadhaarMasked ?? '').isNotEmpty) 'Aadhaar ${shared.aadhaarMasked}',
      ];
      return SectionCard(
        key: const Key('digilocker-done'),
        tone: Tone.good,
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const IconBadge(Icons.verified_user_outlined, tone: Tone.good),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(t.dlDone, style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w800, color: C.goodInk)),
                  if (shared.name != null) Text(shared.name!, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w800)),
                  if (shared.dateOfBirth != null) Text('${t.dobLabel}: ${DateFormat('d MMM y').format(shared.dateOfBirth!)}', style: muted),
                  if (ids.isNotEmpty) Text(ids.join(' · '), style: muted),
                ],
              ),
            ),
          ],
        ),
      );
    }
    final pending = dl?.status == 'pending';
    return SectionCard(
      key: const Key('digilocker'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const IconBadge(Icons.badge_outlined),
              const SizedBox(width: 12),
              Expanded(child: Text(t.dlTitle, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w800))),
            ],
          ),
          const SizedBox(height: 8),
          Text(t.dlBody, style: const TextStyle(fontSize: 13, color: C.muted, height: 1.4)),
          if (dl?.status == 'denied') ...[const SizedBox(height: 10), Notice(tone: Tone.warn, icon: Icons.info_outline, body: t.dlDenied)],
          if (dl?.status == 'failed') ...[const SizedBox(height: 10), Notice(tone: Tone.bad, icon: Icons.error_outline, body: t.dlFailed)],
          if (pending) ...[const SizedBox(height: 10), Text(t.dlWaiting, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600))],
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: FilledButton.icon(
                  key: const Key('digilocker-open'),
                  onPressed: _dlBusy ? null : _openDigiLocker,
                  icon: const Icon(Icons.open_in_new, size: 18),
                  label: Text(t.dlOpen),
                ),
              ),
              if (pending) ...[
                const SizedBox(width: 8),
                OutlinedButton(key: const Key('digilocker-check'), onPressed: _dlBusy ? null : _checkDigiLocker, child: Text(t.dlCheck)),
              ],
            ],
          ),
        ],
      ),
    );
  }

  /// Where the credit report comes from: the bureau, pulled with the PAN. The customer never types a score.
  Widget _bureauLine(AppLocalizations t, Profile p) {
    final pulled = p.bureauPulled || p.bureauStatus == null; // older engines always pulled it
    return Row(
      children: [
        Icon(pulled ? Icons.task_alt : Icons.schedule, size: 16, color: pulled ? C.good : C.muted),
        const SizedBox(width: 6),
        Expanded(
          child: Text(
            pulled ? t.bureauFetched : t.bureauLater,
            key: const Key('bureau-line'),
            style: const TextStyle(fontSize: 12.5, color: C.muted),
          ),
        ),
      ],
    );
  }

  List<Widget> _declarationForm(AppLocalizations t) {
    Widget text(
      String label,
      TextEditingController c,
      String key, {
      TextInputType type = TextInputType.text,
      bool digits = false,
      String? prefix,
      String? helper,
    }) => Padding(
      padding: const EdgeInsets.only(top: 14),
      child: Labelled(
        label: label,
        helper: helper,
        child: TextField(
          key: Key(key),
          controller: c,
          keyboardType: type,
          inputFormatters: digits ? [FilteringTextInputFormatter.digitsOnly] : null,
          style: const TextStyle(fontSize: 15),
          decoration: InputDecoration(prefixText: prefix),
        ),
      ),
    );
    Widget work(Employment e, String label) => Expanded(
      child: ChoiceChip(
        key: Key('work-${e.name}'),
        label: SizedBox(
          width: double.infinity,
          child: Text(label, textAlign: TextAlign.center),
        ),
        selected: _work == e,
        onSelected: (_) => setState(() => _work = e),
      ),
    );
    final dob = _dob;
    final dl = _shared;
    return [
      const SizedBox(height: 14),
      Notice(tone: Tone.info, icon: Icons.person_outline, body: t.realDetailsIntro),
      if (dl?.name == null) text(t.fullNameLabel, _name, 'decl-name'),
      if (dl?.dateOfBirth == null)
        Padding(
          padding: const EdgeInsets.only(top: 14),
          child: Labelled(
            label: t.dobLabel,
            child: OutlinedButton.icon(
              key: const Key('decl-dob'),
              onPressed: _pickDob,
              icon: const Icon(Icons.event_outlined, size: 18),
              label: Text(dob == null ? t.pickDob : DateFormat('d MMM y').format(dob)),
            ),
          ),
        ),
      text(t.emailLabel, _email, 'decl-email', type: TextInputType.emailAddress),
      Padding(
        padding: const EdgeInsets.only(top: 14),
        child: Labelled(
          label: t.employmentLabel,
          child: Row(children: [work(Employment.salaried, t.salaried), const SizedBox(width: 8), work(Employment.selfEmployed, t.selfEmployed)]),
        ),
      ),
      text(t.employerLabel, _employer, 'decl-employer'),
      text(t.incomeLabel, _income, 'decl-income', type: TextInputType.number, digits: true, prefix: '₹ '),
      text(t.existingEmiLabel, _emi, 'decl-emi', type: TextInputType.number, digits: true, prefix: '₹ '),
      if ((dl?.address ?? '').isEmpty) text(t.addressLabel, _address, 'decl-address', type: TextInputType.streetAddress),
      const SizedBox(height: 14),
      Notice(key: const Key('bureau-auto'), tone: Tone.good, icon: Icons.insights_outlined, body: t.bureauAuto),
    ];
  }

  Widget _sliderHead(String label, String value) => Row(
    children: [
      Expanded(
        child: Text(label, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700)),
      ),
      Text(
        value,
        style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w800, color: C.brand),
      ),
    ],
  );
}

/// The quick eligibility result, in the blue offer-card style of the home screen.
class _EligibilityCard extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context);
    final state = context.watch<AppState>();
    final e = state.eligibility;
    final lang = state.locale.languageCode;
    final why = switch (e.limitedBy) {
      'income' => t.limitedByIncome(percent(e.foirCap)),
      'property' => t.limitedByProperty(percent(Policy.ltvCap(state.product, e.loanByProperty))),
      _ => t.limitedByAsked,
    };
    const soft = TextStyle(color: C.onBrandMuted, fontSize: 12.5, height: 1.35);
    return Container(
      key: const Key('eligibility'),
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: C.brand,
        borderRadius: BorderRadius.circular(20),
        boxShadow: const [BoxShadow(color: Color(0x330A2550), blurRadius: 20, offset: Offset(0, 8))],
      ),
      child: Stack(
        children: [
          Positioned(
            right: -30,
            top: -30,
            child: Container(
              width: 130,
              height: 130,
              decoration: BoxDecoration(shape: BoxShape.circle, color: Colors.white.withValues(alpha: 0.08)),
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(18),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(t.eligibleTitle, style: const TextStyle(color: Colors.white, fontSize: 14)),
                Text(
                  rupees(e.eligible),
                  key: const Key('eligible-amount'),
                  style: serif(32, color: Colors.white),
                ),
                Text(
                  t.emiPerMonth(rupees(e.emi)),
                  style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w700),
                ),
                Text(t.rateNote(e.ratePct.toStringAsFixed(2)), style: soft),
                const SizedBox(height: 12),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(color: Colors.white.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(12)),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(why, style: const TextStyle(color: Colors.white, fontSize: 13, height: 1.35)),
                      const SizedBox(height: 4),
                      Text(
                        '${rupeesShort(e.loanByIncome, lang: lang)} · ${rupeesShort(e.loanByProperty, lang: lang)}',
                        style: soft,
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 10),
                Text(t.eligibilityNote, style: soft),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _MoneyField extends StatelessWidget {
  const _MoneyField({super.key, required this.value, required this.onChanged, this.verified});
  final double value;
  final ValueChanged<double> onChanged;
  final String? verified;

  @override
  Widget build(BuildContext context) => TextFormField(
    initialValue: value.round().toString(),
    keyboardType: TextInputType.number,
    style: const TextStyle(fontSize: 15),
    inputFormatters: [FilteringTextInputFormatter.digitsOnly],
    decoration: InputDecoration(
      prefixText: '₹ ',
      suffixIcon: verified == null
          ? null
          : Padding(
              padding: const EdgeInsets.only(right: 10),
              child: Pill(verified!, tone: Tone.good),
            ),
      suffixIconConstraints: const BoxConstraints(minWidth: 0, minHeight: 0),
    ),
    onChanged: (s) => onChanged(double.tryParse(s) ?? 0),
  );
}

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../config.dart';
import '../l10n/app_localizations.dart';
import '../reasons.dart';
import 'doc_read_screen.dart';
import '../services/browser.dart';
import '../services/capture.dart';
import '../services/image_checks.dart' as checks;
import '../services/models.dart';
import '../state.dart';
import '../theme.dart';
import '../widgets/common.dart';

/// Income and KYC papers. Each photo is checked on the phone for blur, glare and darkness
/// before it is sent; the engine then reads it (OCR) and checks it's the right document (model 6).
/// When the engine is connected to DigiLocker, the PAN and e-Aadhaar can come from there instead
/// of photos of the cards.
class DocumentsScreen extends StatefulWidget {
  const DocumentsScreen({super.key});

  @override
  State<DocumentsScreen> createState() => _DocumentsScreenState();
}

class _DocumentsScreenState extends State<DocumentsScreen> with WidgetsBindingObserver {
  final _busy = <DocSlot>{};
  final _problems = <DocSlot, String>{};
  bool _deciding = false;
  Connections _links = const Connections(); // is the engine connected to DigiLocker?
  bool _dlBusy = false, _dlWaiting = false;
  String? _dlProblem;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) => _loadLinks());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  // Back from DigiLocker in the browser: ask the engine what was shared.
  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    if (s == AppLifecycleState.resumed && _dlWaiting) _checkDigiLocker();
  }

  Future<void> _loadLinks() async {
    try {
      final links = await context.read<AppState>().api.connections();
      if (mounted) setState(() => _links = links);
    } catch (_) {
      // No answer: the cards are photographed as before.
    }
  }

  Future<void> _openDigiLocker() async {
    final state = context.read<AppState>();
    setState(() {
      _dlBusy = true;
      _dlProblem = null;
    });
    try {
      final url = await state.api.startDigiLocker(state.session!, returnToApp: !kIsWeb);
      if (url != null) {
        setState(() => _dlWaiting = true);
        await openSignInPage(url);
      }
      await _checkDigiLocker();
    } catch (_) {
      if (mounted) setState(() => _dlProblem = AppLocalizations.of(context).docsDlRetry);
    } finally {
      if (mounted) setState(() => _dlBusy = false);
    }
  }

  Future<void> _checkDigiLocker() async {
    final t = AppLocalizations.of(context);
    final state = context.read<AppState>();
    try {
      final s = await state.api.digiLockerStatus(state.session!);
      if (!mounted) return;
      if (s.done) {
        state.update(() => state.digiLocker = s);
        setState(() => _dlWaiting = false);
      } else if (s.status != 'pending') {
        setState(() {
          _dlWaiting = false;
          _dlProblem = s.error == 'pan_mismatch' ? t.dlPanMismatch : t.docsDlRetry;
        });
      }
    } catch (_) {}
  }

  Future<void> _add(DocSlot slot, Future<Uint8List?> Function() source) async {
    final t = AppLocalizations.of(context);
    final state = context.read<AppState>();
    final bytes = await source();
    if (bytes == null) return;
    setState(() {
      _busy.add(slot);
      _problems.remove(slot);
    });
    DocResult? read;
    try {
      // Blur, glare and darkness are photo problems; a PDF goes straight to the engine.
      final pdf = isPdf(bytes);
      final problem = pdf ? checks.PhotoProblem.none : await compute(checks.check, bytes);
      if (problem != checks.PhotoProblem.none) {
        _problems[slot] = switch (problem) {
          checks.PhotoProblem.blurry => t.tooBlurry,
          checks.PhotoProblem.glare => t.tooMuchGlare,
          checks.PhotoProblem.dark => t.tooDark,
          _ => t.docUnreadable,
        };
        return;
      }
      final result = await state.api.uploadDocument(state.session!, slot, bytes, '${slot.name}.${pdf ? 'pdf' : 'jpg'}');
      if (!result.accepted) {
        _problems[slot] = result.reasonCode == 'R-DOC-02' ? t.docUnreadable : t.docWrong(docName(t, slot));
      }
      state.update(() => state.documents[slot] = result);
      read = result;
    } catch (_) {
      _problems[slot] = t.errorGeneric;
    } finally {
      if (mounted) setState(() => _busy.remove(slot));
    }
    // Show the engine's check. No one confirms what was read: a document that couldn't be checked
    // is retaken, and one that failed verification goes straight to the decision.
    if (read == null || !mounted) return;
    final next = await Navigator.push<DocReadNext>(
      context,
      MaterialPageRoute(builder: (_) => DocReadScreen(slot: slot, bytes: bytes, result: read!)),
    );
    if (!mounted) return;
    if (next == DocReadNext.retake) await _add(slot, source);
    if (next == DocReadNext.decide) await _decide();
  }

  Future<void> _decide() async {
    final state = context.read<AppState>();
    setState(() => _deciding = true);
    try {
      final d = await state.api.inPrinciple(state.session!, state.request);
      state.update(() => state.inPrinciple = d);
      if (mounted) Navigator.pushNamed(context, '/decision');
    } catch (_) {
      if (mounted) showError(context, AppLocalizations.of(context).errorGeneric);
    } finally {
      if (mounted) setState(() => _deciding = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context);
    final state = context.watch<AppState>();
    final missing = state.requiredDocs.where((s) => !(state.documents[s]?.accepted ?? false)).length;
    final done = state.requiredDocs.length - missing;
    final total = state.requiredDocs.length;
    return StepScaffold(
      title: t.docsTitle,
      step: 5,
      busy: _deciding,
      buttonLabel: state.docsComplete ? t.getOffer : t.docsMissing('$missing'),
      onPressed: state.docsComplete ? _decide : null,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(t.docsProgress('$done', '$total'), style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w800)),
                    ),
                    Pill(missing == 0 ? t.verified : t.docsMissing('$missing'), tone: missing == 0 ? Tone.good : Tone.warn),
                  ],
                ),
                const SizedBox(height: 10),
                ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: LinearProgressIndicator(value: total == 0 ? 0 : done / total, minHeight: 8),
                ),
                const SizedBox(height: 10),
                Text(t.docsBody, style: const TextStyle(fontSize: 12.5, color: C.muted)),
              ],
            ),
          ),
          const SizedBox(height: 14),
          if (state.digiLocker != null)
            _digiLockerDone(t, state.digiLocker!)
          else if (_links.digilocker != null && state.requiredDocs.any((s) => s == DocSlot.pan || s == DocSlot.aadhaar))
            _digiLockerCard(t),
          for (final slot in state.requiredDocs) _docCard(context, slot),
          SectionCard(
            padding: const EdgeInsets.all(14),
            child: Row(
              children: [
                const IconBadge(Icons.account_balance_outlined, tone: Tone.good),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(t.docBankStatement, style: const TextStyle(fontSize: 14.5, fontWeight: FontWeight.w700)),
                      Text(t.bankViaAa, style: const TextStyle(fontSize: 12.5, color: C.muted)),
                    ],
                  ),
                ),
                Pill(t.fetched, tone: Tone.good),
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// DigiLocker instead of photographing the PAN and Aadhaar cards.
  Widget _digiLockerCard(AppLocalizations t) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: SectionCard(
        key: const Key('docs-digilocker'),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const IconBadge(Icons.badge_outlined),
                const SizedBox(width: 12),
                Expanded(child: Text(t.docsDlTitle, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w800))),
              ],
            ),
            const SizedBox(height: 8),
            Text(t.docsDlBody, style: const TextStyle(fontSize: 13, color: C.muted, height: 1.4)),
            if (_dlProblem != null) ...[
              const SizedBox(height: 10),
              Notice(key: const Key('docs-digilocker-problem'), tone: Tone.warn, icon: Icons.info_outline, body: _dlProblem!),
            ],
            if (_dlWaiting) ...[
              const SizedBox(height: 10),
              Text(t.dlWaiting, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
            ],
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: FilledButton.icon(
                    key: const Key('docs-digilocker-open'),
                    onPressed: _dlBusy ? null : _openDigiLocker,
                    icon: const Icon(Icons.open_in_new, size: 18),
                    label: Text(t.dlOpen),
                  ),
                ),
                if (_dlWaiting) ...[
                  const SizedBox(width: 8),
                  OutlinedButton(key: const Key('docs-digilocker-check'), onPressed: _dlBusy ? null : _checkDigiLocker, child: Text(t.dlCheck)),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _digiLockerDone(AppLocalizations t, DigiLockerShare s) {
    final ids = [
      if ((s.panMasked ?? '').isNotEmpty) 'PAN ${s.panMasked}',
      if ((s.aadhaarMasked ?? '').isNotEmpty) 'Aadhaar ${s.aadhaarMasked}',
    ];
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: SectionCard(
        key: const Key('docs-digilocker-done'),
        tone: Tone.good,
        padding: const EdgeInsets.all(14),
        child: Row(
          children: [
            const IconBadge(Icons.verified_user_outlined, tone: Tone.good),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(t.docsDlDone, style: const TextStyle(fontSize: 14.5, fontWeight: FontWeight.w800, color: C.goodInk)),
                  if (s.name != null) Text(s.name!, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700)),
                  if (ids.isNotEmpty) Text(ids.join(' · '), style: const TextStyle(fontSize: 12.5, color: C.muted)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _docCard(BuildContext context, DocSlot slot) {
    final t = AppLocalizations.of(context);
    final result = context.watch<AppState>().documents[slot];
    final problem = _problems[slot];
    final busy = _busy.contains(slot);
    final ok = result?.accepted ?? false;
    final failed = result?.failed ?? false;
    final score = result?.score;
    final status = busy
        ? t.checking
        : failed
        ? t.docFailed
        : problem ?? (ok && score != null ? t.verifyScore('$score') : null);
    final (pill, tone) = busy
        ? (t.checking, Tone.info)
        : failed
        ? (t.verifyScore('${score ?? 0}'), Tone.bad)
        : problem != null
        ? (t.docTryAgain, Tone.bad)
        : ok
        ? (t.verified, Tone.good)
        : (t.docNeeded, Tone.warn);
    return Padding(
      key: Key('doc-${slot.name}'),
      padding: const EdgeInsets.only(bottom: 10),
      child: SectionCard(
        padding: const EdgeInsets.fromLTRB(14, 14, 14, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                IconBadge(
                  failed ? Icons.gpp_bad_outlined : ok ? Icons.task_alt : Icons.description_outlined,
                  tone: failed || problem != null ? Tone.bad : ok ? Tone.good : Tone.info,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(docName(t, slot), style: const TextStyle(fontSize: 14.5, fontWeight: FontWeight.w700)),
                ),
                if (busy) const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)) else Pill(pill, tone: tone),
              ],
            ),
            if (status != null)
              Padding(
                padding: const EdgeInsets.only(left: 52, top: 4),
                child: Text(status, style: TextStyle(fontSize: 12.5, color: problem != null || failed ? C.badInk : C.muted)),
              ),
            if (!ok) ...[
              const SizedBox(height: 10),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: busy ? null : () => _add(slot, () => takePhoto()),
                      icon: const Icon(Icons.photo_camera_outlined, size: 18),
                      label: Text(t.camera, overflow: TextOverflow.ellipsis),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: busy ? null : () => _add(slot, () => takePhoto(gallery: true)),
                      icon: const Icon(Icons.photo_library_outlined, size: 18),
                      label: Text(t.gallery, overflow: TextOverflow.ellipsis),
                    ),
                  ),
                ],
              ),
            ],
            Wrap(
              alignment: WrapAlignment.end,
              children: [
                // ID cards are photographed: the engine needs the face on them, and e-Aadhaar PDFs are locked.
                if (!ok && slot != DocSlot.pan && slot != DocSlot.aadhaar)
                  TextButton.icon(
                    key: Key('pdf-${slot.name}'),
                    onPressed: busy ? null : () => _add(slot, pickPdf),
                    icon: const Icon(Icons.picture_as_pdf_outlined, size: 18),
                    label: Text(t.uploadPdf),
                  ),
                if (demoMode)
                  TextButton.icon(
                    key: Key('sample-${slot.name}'),
                    onPressed: busy
                        ? null
                        : () => _add(slot, () async {
                              final s = context.read<AppState>();
                              return await s.api.sample(s.session!, slot.name) ?? sampleDocument(seed: slot.index);
                            }),
                    icon: const Icon(Icons.science_outlined, size: 18),
                    label: Text(t.useSample),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../l10n/app_localizations.dart';
import '../reasons.dart';
import '../services/capture.dart';
import '../services/models.dart';
import '../theme.dart';
import '../widgets/common.dart';

/// What the customer does next, once the engine has checked a document.
enum DocReadNext { keep, retake, decide }

/// The engine's check of a document photo: a scan passes over the photo, then the verdict with
/// its verification score, the checks, and the fields it read. No one confirms what was read:
/// a verified document is kept, one that couldn't be checked is retaken, and one that failed
/// verification goes to the decision, which rejects the application. Pops a [DocReadNext].
class DocReadScreen extends StatefulWidget {
  const DocReadScreen({super.key, required this.slot, required this.bytes, required this.result});
  final DocSlot slot;
  final Uint8List bytes;
  final DocResult result;

  @override
  State<DocReadScreen> createState() => _DocReadScreenState();
}

class _DocReadScreenState extends State<DocReadScreen> with SingleTickerProviderStateMixin {
  late final _scan = AnimationController(vsync: this, duration: const Duration(milliseconds: 1500))..forward();

  @override
  void dispose() {
    _scan.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context);
    final r = widget.result;
    final asked = docName(t, widget.slot);
    final seen = DocSlot.values.where((s) => s.name == r.detectedType).firstOrNull;
    final fields = r.fields.entries.where((e) => _label(t, e.key) != null).toList();
    final checks = r.verification?.checks ?? const <String, bool>{};
    final score = r.score;
    final title = r.failed
        ? t.docFailed
        : r.accepted
            ? t.docVerified(seen == null ? asked : docName(t, seen))
            : r.reasonCode == 'R-DOC-02'
                ? t.docUnreadable
                : t.docWrong(asked);

    return Scaffold(
      appBar: AppBar(
        leading: IconButton(
          icon: const Icon(Icons.close),
          onPressed: () => Navigator.pop(context, r.failed ? DocReadNext.decide : DocReadNext.keep),
        ),
        title: Text(t.readingDoc(asked), maxLines: 1, overflow: TextOverflow.ellipsis),
        shape: const Border(bottom: BorderSide(color: C.line)),
        actions: [Padding(padding: const EdgeInsets.only(right: 14), child: Center(child: Pill(t.documentAi)))],
      ),
      body: SafeArea(
        child: Column(children: [
          const SandboxBanner(),
          Expanded(
            child: Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 560),
                child: ListView(padding: const EdgeInsets.fromLTRB(16, 16, 16, 24), children: [
                  _photo(),
                  const SizedBox(height: 16),
                  AnimatedBuilder(
                    animation: _scan,
                    builder: (context, child) => AnimatedOpacity(
                      opacity: _scan.isCompleted ? 1 : 0.25,
                      duration: const Duration(milliseconds: 300),
                      child: child,
                    ),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      SectionCard(
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Row(children: [
                            Expanded(
                              child: Text(
                                title,
                                key: Key('verdict-${r.failed ? 'failed' : r.accepted ? 'verified' : 'retake'}'),
                                style: TextStyle(fontSize: 15, fontWeight: FontWeight.w800, color: r.verified ? C.ink : C.badInk),
                              ),
                            ),
                            if (score != null) ...[
                              const SizedBox(width: 8),
                              Pill(t.verifyScore('$score'), tone: r.failed ? Tone.bad : score >= 80 ? Tone.good : Tone.warn),
                            ],
                          ]),
                          const SizedBox(height: 8),
                          CheckRow(ok: checks['type'] ?? r.accepted, label: t.readMatches),
                          CheckRow(ok: checks['readable'] ?? true, label: t.readClear),
                          if (checks['name'] != null) CheckRow(ok: checks['name']!, label: t.checkName),
                          if (checks['pan'] != null) CheckRow(ok: checks['pan']!, label: t.checkPan),
                        ]),
                      ),
                      if (r.failed) ...[
                        const SizedBox(height: 12),
                        SectionCard(
                          tone: Tone.bad,
                          padding: const EdgeInsets.all(14),
                          child: Text(t.docFailedBody, style: const TextStyle(fontSize: 13.5, height: 1.4)),
                        ),
                      ],
                      if (r.accepted && fields.isNotEmpty) ...[
                        const SizedBox(height: 18),
                        SectionTitle(t.whatWeRead),
                        SectionCard(
                          padding: const EdgeInsets.fromLTRB(16, 6, 16, 6),
                          child: Column(children: [
                            for (var i = 0; i < fields.length; i++) ...[
                              Padding(
                                padding: const EdgeInsets.symmetric(vertical: 10),
                                child: Row(children: [
                                  Expanded(child: Text(_label(t, fields[i].key)!, style: const TextStyle(fontSize: 13, color: C.muted))),
                                  Flexible(
                                    child: Text(fields[i].value,
                                        textAlign: TextAlign.end, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700)),
                                  ),
                                ]),
                              ),
                              if (i < fields.length - 1) const Divider(height: 1),
                            ],
                          ]),
                        ),
                      ],
                    ]),
                  ),
                ]),
              ),
            ),
          ),
          Container(
            decoration: const BoxDecoration(
              color: C.card,
              border: Border(top: BorderSide(color: C.line)),
              boxShadow: [BoxShadow(color: Color(0x140F1B2D), blurRadius: 16, offset: Offset(0, -4))],
            ),
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
            child: SizedBox(
              width: double.infinity,
              height: 52,
              child: r.failed
                  ? FilledButton(
                      key: const Key('see-decision'),
                      onPressed: () => Navigator.pop(context, DocReadNext.decide),
                      child: Text(t.seeDecision),
                    )
                  : r.accepted
                      ? FilledButton(
                          key: const Key('continue'),
                          onPressed: () => Navigator.pop(context, DocReadNext.keep),
                          child: Text(t.continueLabel),
                        )
                      : FilledButton.icon(
                          key: const Key('retake'),
                          onPressed: () => Navigator.pop(context, DocReadNext.retake),
                          icon: const Icon(Icons.refresh),
                          label: Text(t.retake),
                        ),
            ),
          ),
        ]),
      ),
    );
  }

  /// The photo with a teal scan line passing over it once, then a teal frame.
  Widget _photo() => ClipRRect(
        borderRadius: BorderRadius.circular(16),
        child: AnimatedBuilder(
          animation: _scan,
          builder: (context, _) => Stack(children: [
            if (isPdf(widget.bytes))
              Container(
                height: 180,
                width: double.infinity,
                color: C.brandTint,
                child: const Center(child: Icon(Icons.picture_as_pdf_outlined, size: 64, color: C.brand)),
              )
            else
              Image.memory(widget.bytes, width: double.infinity, fit: BoxFit.fitWidth, gaplessPlayback: true),
            Positioned.fill(
              child: LayoutBuilder(builder: (context, box) {
                final y = box.maxHeight * Curves.easeInOut.transform(_scan.value);
                return Stack(children: [
                  if (!_scan.isCompleted) ...[
                    Positioned(
                      left: 0,
                      right: 0,
                      top: 0,
                      height: y,
                      child: Container(color: C.brand.withValues(alpha: 0.10)),
                    ),
                    Positioned(
                      left: 0,
                      right: 0,
                      top: y - 2,
                      height: 4,
                      child: Container(
                        decoration: const BoxDecoration(color: C.brand),
                      ),
                    ),
                  ] else
                    Positioned.fill(
                      child: DecoratedBox(
                        decoration: BoxDecoration(
                          borderRadius: BorderRadius.circular(16),
                          border: Border.all(color: widget.result.verified ? C.good : C.badInk, width: 3),
                        ),
                      ),
                    ),
                ]);
              }),
            ),
          ]),
        ),
      );

  static String? _label(AppLocalizations t, String key) => switch (key) {
        'name' => t.fieldName,
        'pan' => t.fieldPan,
        'aadhaar' => t.fieldAadhaar,
        'employer' => t.fieldEmployer,
        'netPay' => t.fieldNetPay,
        'period' => t.fieldPeriod,
        'year' => t.fieldYear,
        'income' => t.fieldIncome,
        'business' => t.fieldBusiness,
        _ => null,
      };
}

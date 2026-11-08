import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../l10n/app_localizations.dart';
import '../theme.dart';
import 'common.dart';

/// Where a 300–900 score sits. The cut-offs are the engine's: below 650 is under the policy
/// limit, 650–699 goes to an officer, 700 and up passes, and 760 and up is grade A.
enum ScoreBand { low, check, good, excellent }

ScoreBand bandOf(int score) => score < 650
    ? ScoreBand.low
    : score < 700
        ? ScoreBand.check
        : score < 760
            ? ScoreBand.good
            : ScoreBand.excellent;

extension ScoreBandLook on ScoreBand {
  Color get colour => switch (this) {
        ScoreBand.low => const Color(0xFFD64535),
        ScoreBand.check => const Color(0xFFE9A23B),
        ScoreBand.good => const Color(0xFF55B77E),
        ScoreBand.excellent => C.goodInk,
      };

  Tone get tone => switch (this) {
        ScoreBand.low => Tone.bad,
        ScoreBand.check => Tone.warn,
        ScoreBand.good || ScoreBand.excellent => Tone.good,
      };

  String name(AppLocalizations t) => switch (this) {
        ScoreBand.low => t.bandLow,
        ScoreBand.check => t.bandCheck,
        ScoreBand.good => t.bandGood,
        ScoreBand.excellent => t.bandExcellent,
      };

  String range() => switch (this) {
        ScoreBand.low => '300–649',
        ScoreBand.check => '650–699',
        ScoreBand.good => '700–759',
        ScoreBand.excellent => '760–900',
      };
}

/// A half-circle meter for one 300–900 score: the bands, a needle at the score, the number,
/// and a pill under it (the band's name, or the grade when [badge] is given).
class ScoreMeter extends StatelessWidget {
  const ScoreMeter({super.key, required this.title, required this.score, required this.caption, this.badge, this.provisional = false});
  final String title;
  final int? score;
  final String caption; // "out of 900", or why there's no score
  final String? badge; // shown instead of the band's name, for example "Grade B"
  final bool provisional; // not final yet (no credit report): neutral colours, no band lit up

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context);
    final s = score;
    final band = s == null ? null : bandOf(s);
    final tone = provisional ? Tone.info : band?.tone;
    return SectionCard(
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 12),
      child: Column(
        children: [
          Text(title, textAlign: TextAlign.center, style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: C.muted)),
          const SizedBox(height: 8),
          AspectRatio(
            aspectRatio: 2,
            child: CustomPaint(painter: _MeterPainter(score: s, provisional: provisional)),
          ),
          const SizedBox(height: 2),
          const Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('300', style: TextStyle(fontSize: 10.5, color: C.muted)),
              Text('900', style: TextStyle(fontSize: 10.5, color: C.muted)),
            ],
          ),
          FittedBox(
            fit: BoxFit.scaleDown,
            child: Text(s == null ? '—' : '$s', style: serif(28).copyWith(color: provisional ? C.ink : (band?.tone.ink ?? C.muted), height: 1.1)),
          ),
          const SizedBox(height: 4),
          Text(caption, textAlign: TextAlign.center, style: const TextStyle(fontSize: 11.5, color: C.muted, height: 1.3)),
          if (band != null) ...[
            const SizedBox(height: 8),
            Pill(badge ?? band.name(t), tone: tone!),
          ],
        ],
      ),
    );
  }
}

/// The four bands with their score ranges, shown once under the meters.
class ScoreBandKey extends StatelessWidget {
  const ScoreBandKey({super.key});

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context);
    return Wrap(
      spacing: 14,
      runSpacing: 6,
      children: [
        for (final b in ScoreBand.values)
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(width: 10, height: 10, decoration: BoxDecoration(color: b.colour, shape: BoxShape.circle)),
              const SizedBox(width: 6),
              Text('${b.name(t)} ${b.range()}', style: const TextStyle(fontSize: 11.5, color: C.muted)),
            ],
          ),
      ],
    );
  }
}

class _MeterPainter extends CustomPainter {
  _MeterPainter({required this.score, this.provisional = false});
  final int? score;
  final bool provisional;

  static const _min = 300.0, _max = 900.0;
  static const _edges = [300.0, 650.0, 700.0, 760.0, 900.0];

  double _angle(double v) => math.pi + (v.clamp(_min, _max) - _min) / (_max - _min) * math.pi;

  @override
  void paint(Canvas canvas, Size size) {
    final stroke = size.width * 0.085;
    final radius = math.min(size.width / 2, size.height) - stroke / 2;
    final centre = Offset(size.width / 2, size.height);
    final rect = Rect.fromCircle(center: centre, radius: radius);
    const gap = 0.012; // a thin gap between bands
    for (var i = 0; i < ScoreBand.values.length; i++) {
      final from = _angle(_edges.elementAt(i)), to = _angle(_edges.elementAt(i + 1));
      final band = ScoreBand.values.elementAt(i);
      final active = !provisional && score != null && bandOf(score!) == band;
      final paint = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = stroke
        ..strokeCap = StrokeCap.butt
        ..color = score == null ? C.line : band.colour.withValues(alpha: active ? 1 : 0.35);
      canvas.drawArc(rect, from + (i == 0 ? 0 : gap / 2), to - from - (i == 0 || i == 3 ? gap / 2 : gap), false, paint);
    }
    if (score == null) return;
    final a = _angle(score!.toDouble());
    final tip = centre + Offset(math.cos(a), math.sin(a)) * (radius - stroke * 0.2);
    canvas.drawLine(
      centre,
      tip,
      Paint()
        ..color = C.ink
        ..strokeWidth = 2.5
        ..strokeCap = StrokeCap.round,
    );
    canvas.drawCircle(centre, stroke * 0.45, Paint()..color = C.ink);
  }

  @override
  bool shouldRepaint(_MeterPainter old) => old.score != score || old.provisional != provisional;
}

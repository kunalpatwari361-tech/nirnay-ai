import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../config.dart';
import '../l10n/app_localizations.dart';
import '../services/capture.dart';
import '../services/live_camera.dart';
import '../services/models.dart';
import '../state.dart';
import '../theme.dart';
import '../widgets/common.dart';

/// Live selfie for eKYC. The front camera runs inside the app; the engine picks the order of a short
/// head-turn challenge (look straight, then turn left and right), and the app sends one frame per
/// step. The engine checks liveness on the frames, that the head turned and stayed the same person,
/// and matches the face 1:1 with the Aadhaar or PAN photo.
class SelfieScreen extends StatefulWidget {
  const SelfieScreen({super.key});

  /// How long each step waits before its frame is taken: time to look, or to turn the head.
  static Duration settleTime = const Duration(milliseconds: 1500);
  static Duration turnTime = const Duration(milliseconds: 2400);

  @override
  State<SelfieScreen> createState() => _SelfieScreenState();
}

enum _Phase { idle, starting, live, sending }

class _SelfieScreenState extends State<SelfieScreen> {
  _Phase _phase = _Phase.idle;
  LiveCamera? _cam;
  SelfieChallenge? _challenge;
  int _step = 0;

  bool get _busy => _phase != _Phase.idle;

  @override
  void dispose() {
    _cam?.dispose();
    super.dispose();
  }

  /// A single photo: only the demo's sample selfie, for running without a camera.
  Future<void> _checkPhoto(Uint8List? bytes) async {
    if (bytes == null) return;
    final state = context.read<AppState>();
    setState(() => _phase = _Phase.sending);
    try {
      final result = await state.api.checkSelfie(state.session!, bytes);
      state.update(() {
        state.selfie = bytes;
        state.selfieResult = result;
      });
    } catch (_) {
      if (mounted) showError(context, AppLocalizations.of(context).errorGeneric);
    } finally {
      if (mounted) setState(() => _phase = _Phase.idle);
    }
  }

  Future<void> _startLive() async {
    final t = AppLocalizations.of(context);
    final state = context.read<AppState>();
    setState(() => _phase = _Phase.starting);
    try {
      final challenge = await state.api.selfieChallenge(state.session!);
      final cam = openLiveCamera();
      _cam = cam;
      await cam.start();
      if (!mounted) return;
      setState(() {
        _challenge = challenge;
        _step = 0;
        _phase = _Phase.live;
      });
      final frames = <Uint8List>[];
      for (var i = 0; i < challenge.steps.length; i++) {
        if (!mounted) return;
        setState(() => _step = i);
        await Future<void>.delayed(challenge.steps[i] == 'center' ? SelfieScreen.settleTime : SelfieScreen.turnTime);
        if (!mounted) return;
        frames.add(await cam.capture());
      }
      setState(() => _phase = _Phase.sending);
      final result = await state.api.checkLiveSelfie(state.session!, challenge.id, frames);
      state.update(() {
        state.selfie = frames[challenge.steps.indexOf('center')];
        state.selfieResult = result;
      });
    } on CameraUnavailable {
      if (mounted) showError(context, t.cameraUnavailable);
    } catch (_) {
      if (mounted) showError(context, t.errorGeneric);
    } finally {
      final cam = _cam;
      _cam = null;
      await cam?.dispose();
      if (mounted) setState(() => _phase = _Phase.idle);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context);
    final state = context.watch<AppState>();
    final r = state.selfieResult;
    final passed = r?.passed ?? false;
    final live = _phase == _Phase.live && _cam != null;
    return StepScaffold(
      title: t.selfieTitle,
      step: 4,
      busy: _phase == _Phase.sending || _phase == _Phase.starting,
      buttonLabel: passed ? t.continueLabel : t.liveSelfieStart,
      onPressed: _busy ? null : (passed ? () => Navigator.pushNamed(context, '/documents') : _startLive),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (!live) Notice(tone: Tone.info, icon: Icons.face_retouching_natural, body: t.liveSelfieBody),
          if (live) _prompt(t),
          const SizedBox(height: 16),
          Center(child: _oval(state, r, passed, live)),
          const SizedBox(height: 12),
          if (live) _steps() else Text(t.selfieTips, style: const TextStyle(fontSize: 12.5, color: C.muted), textAlign: TextAlign.center),
          if (_phase == _Phase.sending) ...[
            const SizedBox(height: 10),
            Text(t.liveChecking, key: const Key('live-checking'), textAlign: TextAlign.center, style: const TextStyle(fontWeight: FontWeight.w600)),
          ],
          if (!_busy) ...[
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              alignment: WrapAlignment.center,
              children: [
                if (r != null)
                  OutlinedButton.icon(
                    key: const Key('retake-selfie'),
                    onPressed: _startLive,
                    icon: const Icon(Icons.refresh, size: 18),
                    label: Text(t.retake),
                  ),
                if (demoMode)
                  OutlinedButton.icon(
                    key: const Key('sample-selfie'),
                    onPressed: () => _checkPhoto(sampleSelfie()),
                    icon: const Icon(Icons.science_outlined, size: 18),
                    label: Text(t.useSample),
                  ),
              ],
            ),
          ],
          if (r != null && !_busy) ...[
            const SizedBox(height: 16),
            SectionCard(
              padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
              child: Column(
                children: [
                  CheckRow(ok: r.faceFound, label: t.checkFace),
                  CheckRow(ok: r.live, label: '${t.checkLive} (${(r.livenessScore * 100).round()}%)'),
                  if (r.liveSelfie) CheckRow(ok: r.headTurn ?? false, label: t.checkHeadTurn),
                  r.matchPending
                      ? CheckRow(ok: true, label: t.checkMatchPending)
                      : CheckRow(ok: r.faceMatch, label: '${t.checkMatch} (${(r.matchScore * 100).round()}%)'),
                ],
              ),
            ),
            if (!passed) ...[
              const SizedBox(height: 12),
              Notice(tone: Tone.bad, icon: Icons.error_outline, body: t.selfieFailed),
              const SizedBox(height: 4),
              Align(
                child: TextButton(onPressed: () => showError(context, t.videoKycDemo), child: Text(t.videoKyc)),
              ),
            ],
          ],
        ],
      ),
    );
  }

  /// What to do now, in big type: look straight, or turn which way.
  Widget _prompt(AppLocalizations t) {
    final step = _challenge!.steps[_step];
    final (text, icon) = switch (step) {
      'left' => (t.liveTurnLeft, Icons.arrow_back),
      'right' => (t.liveTurnRight, Icons.arrow_forward),
      _ => (t.liveLookStraight, Icons.center_focus_strong_outlined),
    };
    return Container(
      key: Key('live-step-$step'),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(color: C.brandTint, borderRadius: BorderRadius.circular(14)),
      child: Row(
        children: [
          Icon(icon, color: C.brand, size: 28),
          const SizedBox(width: 12),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w800, color: C.brand))),
        ],
      ),
    );
  }

  Widget _oval(AppState state, SelfieResult? r, bool passed, bool live) {
    final colour = live ? C.brand : (r == null ? C.brand : (passed ? C.good : C.badInk));
    final Widget inside;
    if (live) {
      inside = KeyedSubtree(key: const Key('live-preview'), child: _cam!.preview());
    } else if (state.selfie != null) {
      inside = Image.memory(state.selfie!, fit: BoxFit.cover);
    } else {
      inside = const ColoredBox(color: C.brandTint, child: Center(child: Icon(Icons.person_outline, size: 84, color: C.brand)));
    }
    return Container(
      width: 220,
      height: 280,
      padding: const EdgeInsets.all(4),
      decoration: ShapeDecoration(shape: OvalBorder(side: BorderSide(color: colour, width: 3))),
      child: ClipOval(child: SizedBox.expand(child: inside)),
    );
  }

  /// One dot per step, and a bar that fills while the current step waits for its frame.
  Widget _steps() {
    final steps = _challenge!.steps;
    final wait = steps[_step] == 'center' ? SelfieScreen.settleTime : SelfieScreen.turnTime;
    return Column(
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            for (var i = 0; i < steps.length; i++)
              Container(
                width: 10,
                height: 10,
                margin: const EdgeInsets.symmetric(horizontal: 4),
                decoration: BoxDecoration(shape: BoxShape.circle, color: i <= _step ? C.brand : C.idle),
              ),
          ],
        ),
        const SizedBox(height: 10),
        TweenAnimationBuilder<double>(
          key: ValueKey(_step),
          tween: Tween(begin: 0, end: 1),
          duration: wait,
          builder: (_, v, _) => LinearProgressIndicator(value: v, minHeight: 4, borderRadius: BorderRadius.circular(2)),
        ),
      ],
    );
  }
}

// Real mode only. Run with: flutter test test/real_mode_test.dart --dart-define=NIRNAY_REAL=true
// In a normal `flutter test` run these tests skip themselves (the build has no real-mode flag).
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:nirnay_app/config.dart';
import 'package:nirnay_app/main.dart';
import 'package:nirnay_app/policy.dart';
import 'package:nirnay_app/screens/selfie_screen.dart';
import 'package:nirnay_app/services/api.dart';
import 'package:nirnay_app/services/browser.dart';
import 'package:nirnay_app/services/live_camera.dart';
import 'package:nirnay_app/services/models.dart';
import 'package:nirnay_app/state.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Answers like the engine in real mode and keeps what the app sent.
class FakeRealEngine implements NirnayApi {
  Declaration? declared;
  String? pan;
  int codesSent = 0;

  @override
  Future<String?> sendOtp(String phone) async {
    codesSent++;
    return 'console'; // a test server that prints the code instead of texting it
  }

  @override
  Future<Session> verifyOtp(String phone, String otp, String deviceId) async =>
      Session(token: 't', deviceId: deviceId, applicationId: 'HL-2611-10001');

  @override
  Future<void> recordConsents(Session session, ConsentRecord consent) async {}

  /// What the engine says it can do, and what DigiLocker shares when the customer goes there.
  Connections links = const Connections();
  DigiLockerShare? share;
  String? openedUrl;

  @override
  Future<Connections> connections() async => links;

  @override
  Future<String?> startDigiLocker(Session session, {bool returnToApp = true}) async {
    share = DigiLockerShare(status: 'pending');
    return 'https://digilocker.example/authorize?state=s';
  }

  @override
  Future<DigiLockerShare> digiLockerStatus(Session session) async => share ?? DigiLockerShare(status: 'none');

  @override
  Future<Profile> fetchProfile(Session session, String pan, {Declaration? declared}) async {
    this.pan = pan;
    this.declared = declared;
    final dl = share?.done == true ? share : null;
    return Profile(
      name: dl?.name ?? declared!.name!,
      dateOfBirth: dl?.dateOfBirth ?? declared!.dateOfBirth!,
      panMasked: '${pan.substring(0, 2)}XXX${pan.substring(5, 9)}X',
      aadhaarMasked: dl?.aadhaarMasked ?? '',
      address: dl?.address ?? declared!.address ?? '',
      employment: declared!.employment,
      employer: declared.employer,
      monthlyIncome: declared.monthlyIncome,
      existingEmi: declared.existingEmi,
      bureauScore: links.bureau == null ? null : 771,
      requiredDocuments: [if (dl == null) ...[DocSlot.pan, DocSlot.aadhaar], DocSlot.salarySlip, DocSlot.form16, DocSlot.bankStatement],
      selfDeclared: true,
      identitySource: dl == null ? null : 'digilocker',
      bureauStatus: links.bureau == null ? 'not_pulled' : 'scored',
      bureauSource: links.bureau ?? 'not-connected',
    );
  }

  @override
  Future<SelfieResult> checkSelfie(Session session, Uint8List jpeg) => throw UnimplementedError();
  @override
  Future<SelfieChallenge> selfieChallenge(Session session) async => SelfieChallenge(id: 'c1', steps: ['center', 'right', 'left']);
  List<Uint8List>? liveFrames;
  @override
  Future<SelfieResult> checkLiveSelfie(Session session, String challengeId, List<Uint8List> frames) async {
    liveFrames = frames;
    return SelfieResult(faceFound: true, live: true, livenessScore: 0.97, faceMatch: true, matchScore: 0.88, liveSelfie: true, headTurn: true);
  }
  @override
  Future<DocResult> uploadDocument(Session session, DocSlot slot, Uint8List bytes, String fileName) => throw UnimplementedError();
  @override
  Future<Decision> inPrinciple(Session session, LoanRequest request) => throw UnimplementedError();
  @override
  Future<Decision> finalSanction(Session session, LoanRequest request, PropertyInput property) => throw UnimplementedError();
  @override
  Future<Uint8List?> sample(Session session, String kind) async => null;
  @override
  Future<DecisionLetter> decisionLetter(Session session, String lang) => throw UnimplementedError();
  @override
  Future<AssistantAnswer> ask(Session session, String question, String lang) => throw UnimplementedError();
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('no demo people, demo code or demo banner', (tester) async {
    await tester.pumpWidget(NirnayApp(api: FakeRealEngine()));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('persona-9876500001')), findsNothing);
    expect(find.textContaining('Demo mode'), findsNothing);
    expect(find.textContaining('Ravi'), findsNothing);
  }, skip: !realMode);

  testWidgets('a test server prints the code: the app says where it is, and a new code can be sent', (tester) async {
    final engine = FakeRealEngine();
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(NirnayApp(api: engine));
    await tester.pumpAndSettle();
    tester.state<NavigatorState>(find.byType(Navigator).first).pushNamed('/sign-in');
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('phone')), '9302100000');
    await tester.tap(find.byKey(const Key('primary')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('otp-on-server')), findsOneWidget);
    expect(find.textContaining('server window'), findsOneWidget);
    expect(find.textContaining('We sent a 6-digit code'), findsNothing);
    await tester.tap(find.byKey(const Key('resend-otp')));
    await tester.pumpAndSettle();
    expect(engine.codesSent, 2);
    expect(find.text('New code sent.'), findsOneWidget);
  }, skip: !realMode);

  testWidgets('the customer types their own details, and they go to the engine', (tester) async {
    final engine = FakeRealEngine();
    tester.view.physicalSize = const Size(1080, 2400); // a phone, so the date picker fits
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);
    final state = AppState(engine)..session = Session(token: 't', deviceId: 'd', applicationId: 'HL-2611-10001');
    await tester.pumpWidget(NirnayApp(api: engine, state: state));
    await tester.pumpAndSettle();
    final nav = tester.state<NavigatorState>(find.byType(Navigator).first);
    nav.pushNamed('/details');
    await tester.pumpAndSettle();
    expect(find.text('ABCPK1234F'), findsNothing); // no demo PAN
    Future<void> type(String key, String text) async {
      await tester.ensureVisible(find.byKey(Key(key)));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(Key(key)), text);
      await tester.pumpAndSettle();
    }

    await type('pan', 'AKVPR4821K');
    await type('decl-name', 'Kavya Rao');
    await tester.tap(find.byKey(const Key('primary')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Please fill in'), findsOneWidget); // no date of birth yet
    await tester.ensureVisible(find.byKey(const Key('decl-dob')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('decl-dob')));
    await tester.pumpAndSettle();
    // Flutter's own date picker: press its OK (keeps the initial date, 30 years ago).
    tester.widget<TextButton>(find.widgetWithText(TextButton, 'OK')).onPressed!();
    await tester.pumpAndSettle();
    await type('decl-employer', 'Lotus Analytics Pvt Ltd');
    await type('decl-income', '95000');
    await type('decl-emi', '8000');
    expect(find.byKey(const Key('decl-score')), findsNothing); // the credit report is pulled, never asked
    expect(find.byKey(const Key('bureau-auto')), findsOneWidget);
    expect(find.byKey(const Key('digilocker')), findsNothing); // this engine isn't connected to DigiLocker
    await tester.pump(const Duration(seconds: 5)); // let the earlier "Please fill in" message go away
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('primary')));
    await tester.pumpAndSettle();
    final d = engine.declared!;
    expect(engine.pan, 'AKVPR4821K');
    expect((d.name, d.employer, d.monthlyIncome, d.existingEmi, d.creditScore), ('Kavya Rao', 'Lotus Analytics Pvt Ltd', 95000.0, 8000.0, null));
    expect(d.toJson()['employment'], 'salaried');
    expect(d.toJson().containsKey('credit_score'), isFalse);
    expect(find.textContaining('get your credit report from the credit bureau before'), findsOneWidget); // not pulled yet
    expect(find.textContaining('You entered these details'), findsOneWidget);
    expect(find.text('Verified'), findsNothing); // self-declared income isn't shown as verified
    expect(state.requiredDocs, contains(DocSlot.bankStatement));
  }, skip: !realMode);

  testWidgets('DigiLocker fills name, date of birth, PAN and Aadhaar: the customer types only work and income', (tester) async {
    final engine = FakeRealEngine()..links = const Connections(digilocker: 'digilocker', bureau: 'decentro-live-eq');
    final opened = <String>[];
    final original = openSignInPage;
    addTearDown(() => openSignInPage = original);
    openSignInPage = (url) async {
      opened.add(url); // the customer allows sharing on DigiLocker's page
      engine.share = DigiLockerShare(
        status: 'done',
        name: 'Kavya Rao',
        dateOfBirth: DateTime(1992, 4, 18),
        address: '12 MG Road, Pune, Maharashtra 411001',
        pan: 'AKVPR4821K',
        panMasked: 'AKXXX4821X',
        aadhaarMasked: 'XXXX XXXX 4821',
        hasPan: true,
        hasAadhaar: true,
      );
    };
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);
    final state = AppState(engine)..session = Session(token: 't', deviceId: 'd', applicationId: 'HL-2611-10002');
    await tester.pumpWidget(NirnayApp(api: engine, state: state));
    await tester.pumpAndSettle();
    tester.state<NavigatorState>(find.byType(Navigator).first).pushNamed('/details');
    await tester.pumpAndSettle();
    Future<void> tap(String key) async {
      await tester.ensureVisible(find.byKey(Key(key)));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(Key(key)));
      await tester.pumpAndSettle();
    }

    Future<void> type(String key, String text) async {
      await tester.ensureVisible(find.byKey(Key(key)));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(Key(key)), text);
      await tester.pumpAndSettle();
    }

    expect(find.byKey(const Key('digilocker')), findsOneWidget);
    await tap('digilocker-open');
    expect(opened.single, startsWith('https://digilocker.example/'));
    expect(find.byKey(const Key('digilocker-done')), findsOneWidget);
    expect(find.text('Kavya Rao'), findsOneWidget);
    for (final k in ['decl-name', 'decl-dob', 'decl-address', 'decl-score']) {
      expect(find.byKey(Key(k)), findsNothing, reason: k);
    }
    final pan = tester.widget<TextField>(find.byKey(const Key('pan')));
    expect((pan.controller!.text, pan.enabled), ('AKVPR4821K', false)); // DigiLocker's PAN, not retyped
    await type('decl-employer', 'Lotus Analytics Pvt Ltd');
    await type('decl-income', '95000');
    await tap('primary');
    final d = engine.declared!;
    expect(engine.pan, 'AKVPR4821K');
    expect((d.name, d.dateOfBirth, d.address, d.creditScore), (null, null, null, null));
    expect(d.toJson().keys.toSet().intersection({'name', 'dob', 'address', 'credit_score'}), isEmpty);
    expect(state.requiredDocs, isNot(anyOf(contains(DocSlot.pan), contains(DocSlot.aadhaar)))); // no cards to photograph
    expect(find.text('Received from DigiLocker'), findsOneWidget);
    expect(find.text('Credit report fetched from the credit bureau'), findsOneWidget);
  }, skip: !realMode);

  testWidgets('live selfie: the camera runs in the app, the customer follows the prompts, one frame per step', (tester) async {
    final engine = FakeRealEngine();
    final cam = FakeCamera();
    final (camera, settle, turn) = (openLiveCamera, SelfieScreen.settleTime, SelfieScreen.turnTime);
    addTearDown(() {
      openLiveCamera = camera;
      SelfieScreen.settleTime = settle;
      SelfieScreen.turnTime = turn;
    });
    openLiveCamera = () => cam;
    SelfieScreen.settleTime = const Duration(milliseconds: 300);
    SelfieScreen.turnTime = const Duration(milliseconds: 500);
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);
    final state = AppState(engine)..session = Session(token: 't', deviceId: 'd', applicationId: 'HL-2611-10003');
    await tester.pumpWidget(NirnayApp(api: engine, state: state));
    await tester.pumpAndSettle();
    tester.state<NavigatorState>(find.byType(Navigator).first).pushNamed('/selfie');
    await tester.pumpAndSettle();
    expect(find.text('Start live selfie'), findsOneWidget);
    await tester.tap(find.byKey(const Key('primary')));
    await tester.pump(); // the engine's challenge
    await tester.pump();
    expect(cam.started, isTrue);
    expect(find.byKey(const Key('live-preview')), findsOneWidget);
    expect(find.byKey(const Key('live-step-center')), findsOneWidget);
    expect(find.text('Look straight at the camera'), findsOneWidget);
    await tester.pump(const Duration(milliseconds: 310));
    await tester.pump();
    expect(find.byKey(const Key('live-step-right')), findsOneWidget); // the engine's order, not a fixed one
    expect(find.text('Slowly turn your head to your right'), findsOneWidget);
    await tester.pump(const Duration(milliseconds: 510));
    await tester.pump();
    expect(find.byKey(const Key('live-step-left')), findsOneWidget);
    await tester.pump(const Duration(milliseconds: 510));
    await tester.pumpAndSettle();
    expect(engine.liveFrames, cam.frames); // centre, right, left: all three, in order
    expect(cam.disposed, isTrue); // the camera is off once the frames are taken
    expect(find.byKey(const Key('live-preview')), findsNothing);
    expect(state.selfie, same(cam.frames.first)); // the straight-on frame is the one kept for the face match
    expect(find.text('Head turn followed'), findsOneWidget);
    expect(find.text('Continue'), findsOneWidget);
  });

  Future<(FakeRealEngine, AppState)> onDocuments(WidgetTester tester, {required DigiLockerShare Function() shares}) async {
    final engine = FakeRealEngine()..links = const Connections(digilocker: 'cashfree');
    final original = openSignInPage;
    addTearDown(() => openSignInPage = original);
    openSignInPage = (url) async => engine.share = shares(); // the customer finishes in DigiLocker
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);
    final session = Session(token: 't', deviceId: 'd', applicationId: 'HL-2611-10005');
    final state = AppState(engine)..session = session;
    state.profile = await engine.fetchProfile(session, 'AKVPR4821K',
        declared: Declaration(name: 'Kavya Rao', dateOfBirth: DateTime(1992, 4, 18), employment: Employment.salaried,
            employer: 'Lotus Analytics', monthlyIncome: 95000, existingEmi: 0));
    await tester.pumpWidget(NirnayApp(api: engine, state: state));
    await tester.pumpAndSettle();
    tester.state<NavigatorState>(find.byType(Navigator).first).pushNamed('/documents');
    await tester.pumpAndSettle();
    return (engine, state);
  }

  testWidgets('documents: the PAN and e-Aadhaar come from DigiLocker instead of photos of the cards', (tester) async {
    final (_, state) = await onDocuments(tester, shares: () => DigiLockerShare(
          status: 'done',
          name: 'Kavya Rao',
          panMasked: 'AKXXX4821X',
          aadhaarMasked: 'XXXX XXXX 4821',
          hasPan: true,
          hasAadhaar: true,
          requiredDocuments: [DocSlot.salarySlip, DocSlot.form16, DocSlot.bankStatement],
        ));
    expect(find.byKey(const Key('doc-pan')), findsOneWidget);
    expect(find.text('Get your PAN and e-Aadhaar from DigiLocker'), findsOneWidget);
    await tester.tap(find.byKey(const Key('docs-digilocker-open')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('docs-digilocker-done')), findsOneWidget);
    expect(find.text('PAN and e-Aadhaar received from DigiLocker'), findsOneWidget);
    expect(find.text('PAN AKXXX4821X · Aadhaar XXXX XXXX 4821'), findsOneWidget);
    for (final k in ['doc-pan', 'doc-aadhaar', 'docs-digilocker']) {
      expect(find.byKey(Key(k)), findsNothing, reason: k); // no cards to photograph
    }
    expect(state.requiredDocs, [DocSlot.salarySlip, DocSlot.form16, DocSlot.bankStatement]);
    expect(find.byKey(const Key('doc-salarySlip')), findsOneWidget);
  }, skip: !realMode);

  testWidgets('documents: a different PAN in DigiLocker is not used, and the cards stay', (tester) async {
    final (_, state) = await onDocuments(tester, shares: () => DigiLockerShare(status: 'failed', error: 'pan_mismatch'));
    await tester.tap(find.byKey(const Key('docs-digilocker-open')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('docs-digilocker-problem')), findsOneWidget);
    expect(find.textContaining("isn't the one you entered"), findsOneWidget);
    expect(state.digiLocker, isNull);
    expect(state.requiredDocs, containsAll([DocSlot.pan, DocSlot.aadhaar]));
    expect(find.byKey(const Key('doc-pan')), findsOneWidget);
  }, skip: !realMode);

  testWidgets('live selfie: no camera access says how to allow it', (tester) async {
    final original = openLiveCamera;
    addTearDown(() => openLiveCamera = original);
    openLiveCamera = () => FakeCamera(denied: true);
    final engine = FakeRealEngine();
    final state = AppState(engine)..session = Session(token: 't', deviceId: 'd', applicationId: 'HL-2611-10004');
    await tester.pumpWidget(NirnayApp(api: engine, state: state));
    await tester.pumpAndSettle();
    tester.state<NavigatorState>(find.byType(Navigator).first).pushNamed('/selfie');
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('primary')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Allow camera access for Nirnay'), findsOneWidget);
    expect(engine.liveFrames, isNull);
    expect(find.text('Start live selfie'), findsOneWidget); // they can try again
  });
}

/// A front camera that hands out small, different JPEG frames and keeps them.
class FakeCamera implements LiveCamera {
  FakeCamera({this.denied = false});
  final bool denied;
  bool started = false, disposed = false;
  final frames = <Uint8List>[];

  @override
  Future<void> start() async {
    if (denied) throw CameraUnavailable('denied');
    started = true;
  }

  @override
  Widget preview() => const ColoredBox(color: Colors.black);

  @override
  Future<Uint8List> capture() async {
    final f = img.Image(width: 48 + frames.length, height: 64);
    img.fill(f, color: img.ColorRgb8(200, 160, 120));
    frames.add(Uint8List.fromList(img.encodeJpg(f)));
    return frames.last;
  }

  @override
  Future<void> dispose() async => disposed = true;
}

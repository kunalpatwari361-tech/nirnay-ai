import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:nirnay_app/policy.dart';
import 'package:nirnay_app/services/api.dart';
import 'package:nirnay_app/services/http_api.dart';
import 'package:nirnay_app/services/models.dart';

// The JSON the FastAPI engine sends (backend/app.py), checked against the app's models.
void main() {
  final session = Session(token: 'tok', deviceId: 'dev1', applicationId: 'HL-2611-12345');
  final seen = <http.BaseRequest>[];
  final bodies = <String>[];

  HttpNirnayApi api(Map<String, (int, Object?)> routes) => HttpNirnayApi('http://engine.test',
          client: MockClient((req) async {
        seen.add(req);
        bodies.add(req.body);
        final hit = routes['${req.method} ${req.url.path}'];
        if (hit == null) return http.Response(jsonEncode({'error': 'not_found'}), 404);
        return http.Response(hit.$2 == null ? '' : jsonEncode(hit.$2), hit.$1, headers: {'content-type': 'application/json'});
      }));

  setUp(() {
    seen.clear();
    bodies.clear();
  });

  test('sign-in, consent and profile', () async {
    final a = api({
      'POST /v1/otp/send': (204, null),
      'POST /v1/otp/verify': (200, {'token': 'tok', 'deviceId': 'dev1', 'applicationId': 'HL-2611-12345'}),
      'PUT /v1/applications/HL-2611-12345/consents': (200, {'given': {}}),
      'POST /v1/applications/HL-2611-12345/profile': (200, {
        'name': 'Ravi Kumar', 'dateOfBirth': '1994-03-14', 'panMasked': 'ABXXX1234X', 'aadhaarMasked': 'XXXX XXXX 2336',
        'address': 'Pune', 'employment': 'salaried', 'employer': 'Nimbus', 'monthlyIncome': 100000.0,
        'existingEmi': 10000, 'bureauScore': 765,
      }),
    });
    await a.sendOtp('9876500001');
    final s = await a.verifyOtp('9876500001', '123456', 'dev1');
    expect(s.applicationId, 'HL-2611-12345');
    await a.recordConsents(s, ConsentRecord(given: {'bureau': true}, options: {'aaMonths': 12}, optional: {}));
    final p = await a.fetchProfile(s, 'ABCPK1234K');
    expect(p.name, 'Ravi Kumar');
    expect(p.dateOfBirth, DateTime(1994, 3, 14));
    expect(p.employment, Employment.salaried);
    expect(p.bureauScore, 765);
    final profileCall = seen.last;
    expect(profileCall.headers['Authorization'], 'Bearer tok');
    expect(profileCall.headers['X-Device-Id'], 'dev1');
    expect(jsonDecode(bodies[2])['options']['aaMonths'], 12);
  });

  test('DigiLocker and the credit bureau, as the engine reports them', () async {
    final a = api({
      'GET /v1/health': (200, {'ok': true, 'digilocker': 'sandbox', 'bureau': 'sandbox-bureau'}),
      'POST /v1/applications/HL-2611-12345/digilocker': (200, {'url': 'http://engine.test/v1/sandbox/digilocker/oauth2/1/authorize?state=s'}),
      'GET /v1/applications/HL-2611-12345/digilocker': (200, {
        'status': 'done', 'error': null, 'name': 'Priya Nair', 'dateOfBirth': '1990-05-09', 'gender': 'F',
        'address': 'Kochi 682001', 'pan': 'DNRPN7731F', 'panMasked': 'DNXXX7731X', 'aadhaarMasked': 'XXXX XXXX 4821',
        'hasPan': true, 'hasAadhaar': true, 'requiredDocuments': ['salarySlip', 'form16', 'bankStatement'],
      }),
      'POST /v1/applications/HL-2611-12345/profile': (200, {
        'name': 'Priya Nair', 'dateOfBirth': '1990-05-09', 'panMasked': 'DNXXX7731X', 'aadhaarMasked': 'XXXX XXXX 4821',
        'address': 'Kochi 682001', 'employment': 'salaried', 'employer': 'Orchid', 'monthlyIncome': 85000,
        'existingEmi': 5000, 'bureauScore': null, 'requiredDocuments': ['salarySlip', 'form16'], 'selfDeclared': true,
        'identitySource': 'digilocker', 'bureauStatus': 'not_pulled', 'bureauSource': 'not-connected',
      }),
    });
    final links = await a.connections();
    expect((links.digilocker, links.bureau), ('sandbox', 'sandbox-bureau'));
    expect(await a.startDigiLocker(session), contains('/digilocker/oauth2/1/authorize'));
    expect(jsonDecode(bodies.last), {'returnTo': 'app'}); // the engine sends the browser back to nirnay://
    final share = await a.digiLockerStatus(session);
    expect((share.done, share.name, share.dateOfBirth, share.pan, share.hasAadhaar), (true, 'Priya Nair', DateTime(1990, 5, 9), 'DNRPN7731F', true));
    expect(share.requiredDocuments, [DocSlot.salarySlip, DocSlot.form16, DocSlot.bankStatement]); // shared from the documents step
    final p = await a.fetchProfile(session, 'DNRPN7731F',
        declared: Declaration(employment: Employment.salaried, employer: 'Orchid', monthlyIncome: 85000, existingEmi: 5000));
    expect(jsonDecode(bodies.last)['declared'].keys, isNot(contains('name'))); // DigiLocker gave the name and date of birth
    expect((p.identitySource, p.bureauStatus, p.bureauPulled), ('digilocker', 'not_pulled', false));
    expect(p.requiredDocuments, [DocSlot.salarySlip, DocSlot.form16]);
  });

  test('engine errors become ApiException codes', () async {
    final a = api({'POST /v1/otp/verify': (401, {'error': 'wrong_otp'})});
    expect(() => a.verifyOtp('9876500001', '000000', 'd'), throwsA(isA<ApiException>().having((e) => e.code, 'code', 'wrong_otp')));
    final b = api({});
    expect(() => b.fetchProfile(session, 'ABCPK1234K'), throwsA(isA<ApiException>().having((e) => e.code, 'code', 'not_found')));
  });

  test('selfie and documents are sent as uploads', () async {
    final a = api({
      'POST /v1/applications/HL-2611-12345/selfie': (200, {
        'faceFound': true, 'live': true, 'livenessScore': 0.9999, 'faceMatch': true, 'matchScore': 0.0, 'matchPending': true,
      }),
      'POST /v1/applications/HL-2611-12345/documents': (200, {
        'slot': 'salarySlip', 'detectedType': 'salarySlip', 'confidence': 0.97, 'accepted': true, 'reasonCode': null,
        'fields': {'netPay': '1,00,000.00', 'name': 'Ravi Kumar'},
        'verification': {'verdict': 'verified', 'score': 94, 'checks': {'type': true, 'readable': true, 'name': true}},
      }),
    });
    final s = await a.checkSelfie(session, Uint8List.fromList([1, 2, 3]));
    expect(s.passed, isTrue);
    expect(s.matchPending, isTrue);
    final d = await a.uploadDocument(session, DocSlot.salarySlip, Uint8List.fromList([4, 5]), 'slip.jpg');
    expect(d.accepted, isTrue);
    expect(d.fields['netPay'], '1,00,000.00');
    expect((d.verified, d.failed, d.score, d.verification!.checks['name']), (true, false, 94, true));
    // MockClient hands over the encoded multipart body, so check what goes over the wire.
    expect(seen.last.headers['content-type'], startsWith('multipart/form-data'));
    expect(bodies.last, contains('name="slot"\r\n\r\nsalarySlip'));
    expect(bodies.last, contains('name="file"; filename="slip.jpg"'));
    expect(bodies[0], contains('name="file"; filename="selfie.jpg"'));
  });

  test('live selfie: the engine picks the steps, and the frames go back with its challenge id', () async {
    final a = api({
      'POST /v1/applications/HL-2611-12345/selfie/challenge': (200, {'challengeId': 'ch-9', 'steps': ['center', 'left', 'right'], 'expiresIn': 120}),
      'POST /v1/applications/HL-2611-12345/selfie/live': (200, {
        'faceFound': true, 'live': false, 'livenessScore': 0.98, 'faceMatch': false, 'matchScore': 0.0, 'matchPending': true,
        'liveSelfie': true, 'headTurn': false, 'problems': ["the head didn't turn as asked"],
      }),
    });
    final c = await a.selfieChallenge(session);
    expect((c.id, c.expiresIn), ('ch-9', 120));
    expect(c.steps, ['center', 'left', 'right']);
    final r = await a.checkLiveSelfie(session, c.id, [for (var i = 0; i < 3; i++) Uint8List.fromList([i, i])]);
    expect((r.liveSelfie, r.headTurn, r.live, r.passed), (true, false, false, false));
    expect(seen.last.headers['X-Device-Id'], 'dev1');
    expect(bodies.last, contains('name="challengeId"\r\n\r\nch-9'));
    expect(RegExp('name="frames"; filename="frame[0-2].jpg"').allMatches(bodies.last), hasLength(3));
  });

  test('sandbox samples: bytes from the engine, or null so the app uses its own', () async {
    final a = api({'GET /v1/applications/HL-2611-12345/samples/pan': (200, null)});
    expect(await a.sample(session, 'pan'), isNotNull);
    expect(await a.sample(session, 'photo-front'), isNull);
  });

  test('decisions parse, including valuation and reasons', () async {
    final decision = {
      'stage': 'final', 'outcome': 'refer', 'amount': 6000000.0, 'years': 15, 'ratePct': 10.5, 'emi': 66323.94,
      'reasons': [
        {'code': 'R-PRP-01', 'positive': false, 'values': {}},
      ],
      'valuation': {'estimate': 16501000.0, 'low': 12456000.0, 'high': 20413000.0, 'valueForLtv': 12000000.0},
      'kfs': {'loan_amount': 6000000.0, 'processing_fee': 30000, 'gst_on_fee': 5400, 'total_interest': 5938309.2,
        'total_payable': 11973709.2, 'apr_pct': 10.6, 'cooling_off_days': 3},
      'score': {'score': 712, 'grade': 'B', 'base': 650, 'factors': [
        {'input': 'bureau_score', 'bin': '738 or more', 'points': 38},
        {'input': 'loan_amount', 'bin': 'missing', 'points': 0},
      ]},
      'record': {'seq': 4, 'hash': 'abcdef0123456789'},
      'credit': {'score': null, 'status': 'not_pulled'},
      'verification': {'score': 61, 'documents': {'pan': 97, 'bankStatement': 61, 'notASlot': 50}},
    };
    final a = api({
      'POST /v1/applications/HL-2611-12345/in-principle': (200, {...decision, 'stage': 'in_principle', 'outcome': 'approve', 'valuation': null}),
      'POST /v1/applications/HL-2611-12345/final': (200, decision),
    });
    final req = LoanRequest(product: Product.lap, amount: 6000000, years: 15, propertyValue: 12000000);
    final ip = await a.inPrinciple(session, req);
    expect(ip.outcome, Outcome.approve);
    expect(jsonDecode(bodies.last)['product'], 'lap');
    final fin = await a.finalSanction(
        session,
        req,
        PropertyInput(city: 'Pune', locality: 'Baner', areaSqft: 1800, bhk: 3, declaredPrice: 12000000, isNew: false, photos: [
          GeoPhoto(label: 'front', bytes: Uint8List.fromList([9]), takenAt: DateTime.utc(2026, 11, 5), latitude: 18.5, longitude: 73.9),
        ]));
    expect(fin.outcome, Outcome.refer);
    expect(fin.reasons.single.code, 'R-PRP-01');
    expect((fin.credit!.score, fin.credit!.status), (null, 'not_pulled'));
    expect(fin.verification!.score, 61);
    expect(fin.verification!.documents, {DocSlot.pan: 97, DocSlot.bankStatement: 61}); // unknown slots are dropped
    expect(fin.valuation!.valueForLtv, 12000000);
    expect(fin.keyFacts!.loanAmount, 6000000);
    expect(fin.keyFacts!.fee + fin.keyFacts!.gst, 35400);
    expect(fin.keyFacts!.aprPct, 10.6);
    expect(fin.score!.score, 712);
    expect(fin.score!.factors.first.points, 38);
    expect(fin.reference, '#4 · abcdef0123');
    final body = bodies.last;
    expect(body, contains('"locality":"Baner"'));
    expect(body, contains('"latitude":18.5'));
    expect(body, contains('name="photos"; filename="front.jpg"'));
    expect(body, contains('"product":"lap"'));
  });

  test('the decision letter and the assistant', () async {
    final a = api({
      'GET /v1/applications/HL-2611-12345/letter': (200, {'lines': ['Dear Ravi,', 'Your loan is approved.'], 'engines': {'untranslated': 1}}),
      'POST /v1/applications/HL-2611-12345/assistant': (200, {'answer': 'We will call you.', 'handoff_to_human': true}),
    });
    final letter = await a.decisionLetter(session, 'hi');
    expect(letter.lines, ['Dear Ravi,', 'Your loan is approved.']);
    expect(letter.untranslated, 1);
    expect(seen.last.url.queryParameters['lang'], 'hi');
    final answer = await a.ask(session, 'When will I hear back?', 'hi');
    expect(answer.handoff, isTrue);
    expect(jsonDecode(bodies.last), {'question': 'When will I hear back?', 'lang': 'hi'});
  });

  test('the engine says how the sign-in code went out', () async {
    final a = HttpNirnayApi('http://engine.test',
        client: MockClient((req) async => http.Response('', 204, headers: {'x-otp-delivery': 'console'})));
    expect(await a.sendOtp('9876500001'), 'console');
  });
}

// The calls the app makes to the Nirnay engine. HttpNirnayApi calls the FastAPI engine
// (backend/); MockNirnayApi answers with sample data when no engine URL is set
// (flutter run --dart-define=NIRNAY_API_URL=http://localhost:8000 picks the real one).
import 'dart:typed_data';

import 'models.dart';

abstract class NirnayApi {
  /// Sends a one-time code by SMS. Returns how it went out, when the engine says: "console" means
  /// a test server printed it in its own window instead of texting it.
  Future<String?> sendOtp(String phone);

  /// Checks the code and binds this device to the account.
  Future<Session> verifyOtp(String phone, String otp, String deviceId);

  /// Saves the consent choices. The engine fetches nothing until the needed consents are recorded.
  Future<void> recordConsents(Session session, ConsentRecord consent);

  /// What this engine can do: identity from DigiLocker, and the credit report from the bureau.
  Future<Connections> connections();

  /// DigiLocker's sign-in and consent page for this application, to open in the browser. With
  /// [returnToApp] the engine sends the browser back to nirnay://digilocker when it's done.
  /// Null: nothing to open (the sample engine shares straight away).
  Future<String?> startDigiLocker(Session session, {bool returnToApp = true});

  /// What DigiLocker shared, once the customer has been there.
  Future<DigiLockerShare> digiLockerStatus(Session session);

  /// PAN + Aadhaar eKYC / DigiLocker + bureau + Account Aggregator, after consent.
  /// In real mode [declared] carries what the customer typed in; the sample engine ignores it.
  Future<Profile> fetchProfile(Session session, String pan, {Declaration? declared});

  /// Face found, passive liveness and 1:1 match with the eKYC photo (a single photo: the demo's sample).
  Future<SelfieResult> checkSelfie(Session session, Uint8List jpeg);

  /// The live selfie's head-turn steps, in an order the engine picks.
  Future<SelfieChallenge> selfieChallenge(Session session);

  /// One frame from the app's camera per challenge step, in order: liveness, head turn, face match.
  Future<SelfieResult> checkLiveSelfie(Session session, String challengeId, List<Uint8List> frames);

  /// OCR + document classifier (model 6) + tamper checks.
  Future<DocResult> uploadDocument(Session session, DocSlot slot, Uint8List bytes, String fileName);

  /// Stage A: identity, AML, fraud and documents, policy, risk grade.
  Future<Decision> inPrinciple(Session session, LoanRequest request);

  /// Stage B: property, valuation (model 7), title and the final sanction.
  Future<Decision> finalSanction(Session session, LoanRequest request, PropertyInput property);

  /// The decision letter, in the customer's language (app language code, e.g. "hi").
  Future<DecisionLetter> decisionLetter(Session session, String lang);

  /// The help assistant: answers about this application only, in the customer's language.
  Future<AssistantAnswer> ask(Session session, String question, String lang);

  /// Demo only: a sample file for a capture step without a camera. `kind` is a document slot
  /// name or `photo-<label>`. Null means the app uses its own stand-in image.
  Future<Uint8List?> sample(Session session, String kind);
}

class ApiException implements Exception {
  ApiException(this.code);
  final String code; // for example "wrong_otp"
  @override
  String toString() => 'ApiException($code)';
}

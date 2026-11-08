// What the app sends to and gets from the Nirnay engine.
import 'dart:math' as math;
import 'dart:typed_data';

import '../policy.dart';

class Session {
  Session({required this.token, required this.deviceId, required this.applicationId});
  final String token;
  final String deviceId;
  final String applicationId;
}

/// Filled from PAN, Aadhaar eKYC / DigiLocker and the Account Aggregator, so the customer types little.
class Profile {
  Profile({
    required this.name,
    required this.dateOfBirth,
    required this.panMasked,
    required this.aadhaarMasked,
    required this.address,
    required this.employment,
    required this.employer,
    required this.monthlyIncome,
    required this.existingEmi,
    required this.bureauScore,
    this.requiredDocuments,
    this.selfDeclared = false,
    this.identitySource,
    this.bureauStatus,
    this.bureauSource,
  });
  final String name, panMasked, aadhaarMasked, address, employer;
  final DateTime dateOfBirth;
  final Employment employment;
  final double monthlyIncome, existingEmi;
  final int? bureauScore; // null: no credit history
  final List<DocSlot>? requiredDocuments; // the engine's list; null: the app's own
  final bool selfDeclared; // real mode: the customer typed these in, and documents will prove them
  final String? identitySource; // "digilocker": name, date of birth, PAN and Aadhaar came from DigiLocker
  // How the credit report was got: "scored" / "no_history" from the bureau, "not_pulled" (no bureau
  // connected: an officer pulls it), "unavailable" (the bureau failed), "self_declared" (older apps).
  final String? bureauStatus, bureauSource;
  bool get bureauPulled => bureauStatus == 'scored' || bureauStatus == 'no_history';
  /// No bureau answered, so the applicant can upload their own credit report instead.
  bool get creditReportWanted => bureauStatus != null && !bureauPulled;

  int ageOn(DateTime day) {
    var age = day.year - dateOfBirth.year;
    if (day.month < dateOfBirth.month || (day.month == dateOfBirth.month && day.day < dateOfBirth.day)) age--;
    return age;
  }
}

/// Real mode: what the customer tells us about themselves. The engine marks every fact
/// self-declared and checks it against the documents. Name and date of birth are left out when
/// DigiLocker shared them. The credit score is never asked: the engine pulls the credit report.
class Declaration {
  Declaration({
    this.name,
    this.dateOfBirth,
    required this.employment,
    required this.employer,
    required this.monthlyIncome,
    required this.existingEmi,
    this.email,
    this.address,
    this.creditScore,
  });
  final String? name;
  final String employer;
  final DateTime? dateOfBirth;
  final Employment employment;
  final double monthlyIncome, existingEmi;
  final String? email, address;
  final int? creditScore;

  Map<String, Object?> toJson() => {
    if (name != null) 'name': name,
    if (dateOfBirth != null) 'dob': isoDay(dateOfBirth!),
    'employment': employment == Employment.selfEmployed ? 'selfEmployed' : 'salaried',
    'employer': employer,
    'monthly_income': monthlyIncome,
    'existing_emi': existingEmi,
    if (email != null && email!.isNotEmpty) 'email': email,
    if (address != null && address!.isNotEmpty) 'address': address,
    if (creditScore != null) 'credit_score': creditScore,
  };
}

String isoDay(DateTime d) =>
    '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

/// What the engine can do for the customer. Null means it can't.
class Connections {
  const Connections({this.digilocker, this.bureau});
  final String? digilocker; // "digilocker" (the real one) or "sandbox" (a test stand-in)
  final String? bureau; // where the credit report comes from, e.g. "decentro-live-eq"
}

/// What DigiLocker shared: Aadhaar (name, date of birth, address, last 4 digits) and the PAN record.
class DigiLockerShare {
  DigiLockerShare({
    required this.status,
    this.error,
    this.name,
    this.dateOfBirth,
    this.address,
    this.pan,
    this.panMasked,
    this.aadhaarMasked,
    this.hasPan = false,
    this.hasAadhaar = false,
    this.requiredDocuments,
  });
  final String status; // none, pending, done, denied, failed
  final String? error, name, address, pan, panMasked, aadhaarMasked;
  final DateTime? dateOfBirth;
  final bool hasPan, hasAadhaar;

  /// Shared from the documents step (details already saved): the documents still to upload.
  final List<DocSlot>? requiredDocuments;
  bool get done => status == 'done';
}

/// The live selfie's steps, in the order the engine picked: "center", then "left" and "right".
class SelfieChallenge {
  SelfieChallenge({required this.id, required this.steps, this.expiresIn = 120});
  final String id;
  final List<String> steps;
  final int expiresIn; // seconds the engine waits for the frames
}

class SelfieResult {
  SelfieResult({
    required this.faceFound,
    required this.live,
    required this.livenessScore,
    required this.faceMatch,
    required this.matchScore,
    this.matchPending = false,
    this.liveSelfie = false,
    this.headTurn,
  });
  final bool faceFound, live, faceMatch;
  final double livenessScore, matchScore;
  // No ID photo to compare with yet: the engine matches the selfie when the Aadhaar or PAN card arrives.
  final bool matchPending;
  final bool liveSelfie; // taken live in the app with the head-turn check (not a single photo)
  final bool? headTurn; // the head turned as asked (live selfie only)
  bool get passed => faceFound && live && faceMatch;
}

/// The customer's consent choices, sent to the engine before any data is fetched (DPDP Act 2023).
class ConsentRecord {
  ConsentRecord({required this.given, required this.options, required this.optional});
  final Map<String, bool> given; // bureau, aa, kyc, privacy
  final Map<String, Object> options; // aaMonths, aaAccounts, kycMethod, useCkyc, recheckBeforePayout
  final Map<String, bool> optional; // offers by channel, WhatsApp updates, insurance quote

  Map<String, Object> toJson() => {'given': given, 'options': options, 'optional': optional};
}

enum DocSlot {
  pan, aadhaar, salarySlip, form16, itr, gst, bankStatement, saleDeed, allotmentLetter, encumbrance, propertyTax,
  creditReport, // optional: the applicant's own report, when no bureau answered
}

class DocResult {
  DocResult({
    required this.slot,
    required this.detectedType,
    required this.confidence,
    required this.accepted,
    this.reasonCode,
    this.fields = const {},
    this.verification,
  });
  final DocSlot slot;
  final String detectedType; // what model 6 says it is
  final double confidence;
  final bool accepted;
  final String? reasonCode; // R-DOC-01 wrong document, R-DOC-02 unreadable
  final Map<String, String> fields; // what OCR read: name, pan, employer, netPay, period, year, income, business;
  // a credit report: score, bureau, reportDate
  final DocVerification? verification; // null from an engine older than verdicts

  bool get failed => verification?.verdict == 'failed';
  bool get verified => accepted && !failed;
  /// 0-100. Older engines sent only the classifier's confidence.
  int? get score => verification != null ? verification!.score : (accepted ? (confidence * 100).round() : null);
}

/// The engine's verdict on one document. No one confirms what was read: the checks decide.
/// verified: every check passed. retake: it couldn't be checked (not the document asked for, or
/// unreadable). failed: it's the right document but didn't pass verification, which rejects the application.
class DocVerification {
  DocVerification({required this.verdict, this.score, this.checks = const {}});
  final String verdict;
  final int? score; // the weakest check, 0-100; null for a retake
  final Map<String, bool> checks; // type, readable, name, pan (the fraud checks are never sent)
}

class PropertyInput {
  PropertyInput({
    required this.city,
    required this.locality,
    required this.areaSqft,
    required this.bhk,
    required this.declaredPrice,
    required this.isNew,
    this.society,
    this.photos = const [],
  });
  final String city, locality;
  final String? society;
  final double areaSqft, declaredPrice;
  final int bhk;
  final bool isNew;
  final List<GeoPhoto> photos;
}

class GeoPhoto {
  GeoPhoto({required this.label, required this.bytes, required this.takenAt, this.latitude, this.longitude, this.accuracyM});
  final String label;
  final Uint8List bytes;
  final DateTime takenAt;
  final double? latitude, longitude, accuracyM;
  bool get geoTagged => latitude != null && longitude != null;
}

enum Outcome { approve, refer, reject }

class Reason {
  Reason({required this.code, required this.positive, this.values = const {}});
  final String code; // a reason code (R-...) or an offer fact (OK-...)
  final bool positive;
  final Map<String, String> values;
}

class Decision {
  Decision({
    required this.stage,
    required this.outcome,
    required this.amount,
    required this.years,
    required this.ratePct,
    required this.emi,
    required this.reasons,
    this.valuation,
    this.keyFacts,
    this.score,
    this.reference,
    this.credit,
    this.verification,
  });
  final String stage; // "in_principle" or "final"
  final Outcome outcome;
  final double amount, ratePct, emi;
  final int years;
  final List<Reason> reasons;
  final Valuation? valuation;
  final KeyFacts? keyFacts; // the engine's Key Facts Statement for this offer
  final ScoreBreakdown? score; // how the credit scorecard (model 1) reached its score
  final String? reference; // the decision record's number and the start of its hash, for audits and complaints
  final CreditScore? credit; // the credit bureau's score
  final VerificationScore? verification; // the document checks' score
}

/// The credit score. status: scored, no_history, not_pulled (no bureau connected: an officer pulls
/// the report), unavailable (the bureau failed), applicant_report (read from the credit report the
/// applicant uploaded: bureau and reportDate say which), self_declared (older applications).
class CreditScore {
  CreditScore({this.score, this.status, this.bureau, this.reportDate});
  final int? score;
  final String? status, bureau, reportDate;
  bool get fromApplicantReport => status == 'applicant_report';
}

/// The score after verification: the weakest uploaded document's, since one bad document decides.
class VerificationScore {
  VerificationScore({this.score, this.documents = const {}});
  final int? score;
  final Map<DocSlot, int> documents;
}

/// Key Facts Statement numbers: processing fee 0.5% plus 18% GST (illustrative), and the APR,
/// the yearly rate at which the EMIs repay what the customer actually receives. The engine
/// sends the same figures; the app works them out itself only for an offer the customer reshaped.
class KeyFacts {
  KeyFacts({
    required this.loanAmount,
    required this.fee,
    required this.gst,
    required this.totalInterest,
    required this.totalPayable,
    required this.aprPct,
    this.coolingOffDays = 3,
  });
  final double loanAmount, fee, gst, totalInterest, totalPayable, aprPct;
  final int coolingOffDays;

  factory KeyFacts.compute(double amount, double emi, int years) {
    final n = years * 12;
    final fee = (amount * 0.005).roundToDouble();
    final gst = (fee * 0.18).roundToDouble();
    final received = amount - fee - gst;
    // Solve received = EMI x [1 - (1 + i)^-n] / i for the monthly rate i (bisection).
    var lo = 1e-6, hi = 0.05;
    for (var k = 0; k < 100; k++) {
      final mid = (lo + hi) / 2;
      final pv = emi * (1 - math.pow(1 + mid, -n)) / mid;
      if (pv > received) {
        lo = mid;
      } else {
        hi = mid;
      }
    }
    return KeyFacts(
      loanAmount: amount,
      fee: fee,
      gst: gst,
      totalInterest: emi * n - amount,
      totalPayable: emi * n + fee + gst,
      aprPct: (lo + hi) / 2 * 12 * 100,
    );
  }
}

/// One line of the scorecard: an input, the range it fell in, and the points it added or took away.
class ScoreFactor {
  ScoreFactor({required this.input, required this.bin, required this.points});
  final String input, bin; // input: bureau_score, credit_history_years, ltv, loan_amount
  final int points;
}

class ScoreBreakdown {
  ScoreBreakdown({required this.score, required this.grade, required this.base, required this.factors, this.provisional = false});
  final int score, base;
  final String grade; // A to E
  final List<ScoreFactor> factors;
  final bool provisional; // worked out without a credit report: an officer confirms it once the report is pulled
}

class DecisionLetter {
  DecisionLetter({required this.lines, required this.untranslated});
  final List<String> lines;
  final int untranslated; // lines the translation engines couldn't do, left in English
}

class AssistantAnswer {
  AssistantAnswer({required this.answer, required this.handoff});
  final String answer;
  final bool handoff; // the assistant couldn't answer, and a person will follow up
}

class Valuation {
  Valuation({required this.estimate, required this.low, required this.high, required this.valueForLtv});
  final double estimate, low, high, valueForLtv;
}

class LoanRequest {
  LoanRequest({required this.product, required this.amount, required this.years, required this.propertyValue});
  final Product product;
  final double amount, propertyValue;
  final int years;
}

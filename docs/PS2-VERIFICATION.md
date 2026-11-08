# PS2 review: what was checked, what was built, and the evidence

This answers the PS2 review's eight problems against the Nirnay decision engine (`backend/`) on 8 November 2026. For each problem: what the review asked, what was already in the code, what was added, the tests that prove it, and what is still not done.

## How "without any change" was read

**Nothing that already worked was changed. Every missing piece was added alongside it.**

- **Earlier tests:** all the engine tests that passed before still pass, unchanged. The three demo cases decide exactly as before: Ravi approved, Meena referred at the property, Arjun rejected.
- **Policy versions:** the new rules sit in a new policy version, `policy-v2`. `policy-v1` is kept as it was, and an old record replays under the version it names, so no past decision changes.
- **The teammate's code:** `api/`, `frontend/` and their models (`eligibility`, `fraud_detection`, `underwriting`, `explainability`, `audit`) were not edited. `api/document.py` imported a package that didn't exist. That package is now added (`models/document_ai`), so the file works without being edited.
- **The app:** the Flutter app is unchanged and needs no new APK.
- **READMEs:** only additions, plus one count updated in our own section: the compliance rules went from 8 to 10.
- **One behaviour change, made on purpose:** the offline deed reader (used when no Claude API key is set) now reads the buyer's name. It reads it from the "IN FAVOUR OF … PURCHASER" clause. Without this, the new LAP ownership check could never pass offline. The real-mode tests and Meena's sale deed decide as before.

## Summary

| # | Problem | Before | Now | Evidence |
|---|---|---|---|---|
| 1 | Data sources | Sources were used but not listed per record | Every record lists each source: provider, mode, time, status and fallback | `provenance.py`; `test_decision_table.py`, `test_fraud_signals.py` |
| 2 | HL vs LAP policy | Separate rates, tenures and LTV; no property type, no LAP cash-flow, ownership or encumbrance checks | Property type per product, a commercial LAP LTV, and LAP cash flow, ownership and encumbrance; loan facts on every application | `policy.py`, `gates.py`; `test_product_policy.py` |
| 3 | Fraud and KYC/AML | Identity, PAN/name match, AML, fraud models, ring, reuse and tamper checks all present | Added: duplicate application on the same PAN, and cash-deposit share. A model score never rejects. | `gates.py`; `test_fraud_signals.py`, `test_decision_table.py` |
| 4 | Risk scoring | Score and grade in the risk gate | A risk summary on every record: score, category, 8 factors, model confidence, reasons, and the models' validation limits | `risk.py`; `test_risk_summary.py` |
| 5 | Documents and property | Upload → OCR → type → applicant match → authenticity → retake/refer in the engine; but `models.document_ai` was missing | `models/document_ai` added; the prototype's document route now loads | `models/document_ai/`; `test_document_ai.py` |
| 6 | Compliance checklist | 8 coded rules with sources | A 7-area checklist (area → rule → source → code → tests → evidence → limits), 2 new rules (audit, data privacy), and a test that each rule fails when broken | `compliance.py`; `test_compliance.py` |
| 7 | One decision engine | One engine (`backend/`), but the table wasn't tested in one place and the two backends were confusing | Outcome-table tests, an all-gates end-to-end test, and a note on which backend decides | `test_decision_table.py`; `README.md` |
| 8 | Explainability and governance | Records with versions, hash chain and replay | One explain view per record, with the hash check, chain check and a fresh replay | `explain.py`; `test_decision_table.py` |

## Problem 1: where each piece of data came from

**What already existed:**
- Sandbox stand-ins for each source (`sandbox.py`).
- Real-mode DigiLocker (`digilocker.py`) and the bureau (`bureau.py`).
- Uploaded documents and statements (`intake.py`).
- A refer, rather than a failure, when a source is missing. But no single list said, per decision, which source each fact came from.

**What was added (`backend/provenance.py`):**
- Each record now keeps `evidence.data_sources`, one row per source.
- The sources covered: identity (eKYC or DigiLocker), PAN, credit bureau, bank statement, income tax, payroll, sanctions lists, selfie and face match, each uploaded document, the same-PAN lookup, and, at the final stage, the valuation, land records and sale deed.
- Each row gives:
  - the provider;
  - the mode (`sandbox`, `live`, `provider_test_server`, `uploaded`, `self_declared`, `not_connected`);
  - when it was fetched or read;
  - its verification status;
  - what it was used for;
  - `if_unavailable`: what the engine does without it.

**When an external source fails, the engine refers. It never stops and it never approves:**

| Source down or missing | What happens | Code |
|---|---|---|
| Credit bureau (not connected, failed pull, test server) | Refer; an officer pulls the report. An uploaded report counts until sanction. | R-BUR-06, R-BUR-07 |
| Sanctions list not loaded | Refer, and the compliance gate holds the decision | R-AML-02, R-CMP-01 |
| Account Aggregator / bank statement | Income from the slip or ITR; nothing proven → refer. A LAP can't be assessed without it. | R-AFF-04, R-AFF-05 |
| DigiLocker / eKYC | The PAN and Aadhaar cards are uploaded and read; a name that differs refers | R-KYC-02 |
| Land records not connected | Title from the sale deed; no deed → refer. For a LAP, encumbrance unchecked → refer. | R-PRP-01, R-PRP-06 |
| Valuation model unsure | An empanelled valuer values the property | R-PRP-02 |

**Evidence:**
- `test_decision_table.py::test_provider_unavailable_refers` covers the bureau, the sanctions list, the Account Aggregator and land records.
- `::test_explain_shows_everything_for_one_record` checks every source row has a status and a fallback.
- `::test_explain_rebuilds_the_sources_for_a_record_made_before_they_were_kept` covers older records.
- `test_fraud_signals.py::test_real_mode_finds_the_same_pan_with_another_date_of_birth` checks that a real-mode record shows the bureau as `not_connected`.

## Problem 2: Home Loan and LAP each have their own policy

**What already existed:**
- Rates (HL 8.75%, LAP 10.50%) and maximum tenures (30 and 15 years).
- RBI LTV tiers for HL (90/80/75%), a LAP LTV of 60%, and FOIR caps by income (45/50/60%).
- The rule that eligibility is the lowest of the loan by income, the loan by property and the amount asked.
- The title check.

**What was added (policy-v2, `policy.py` and `gates.py`):**

| | Home Loan | LAP |
|---|---|---|
| Property type | Residential only; commercial → **reject** (R-POL-03) | Residential or commercial |
| LTV | RBI tiers 90 / 80 / 75% | 60% residential, **50% commercial** |
| Income | Proven income, FOIR cap | Proven income, FOIR cap, **and cash flow**: no bank statement, or monthly credits below 2× all EMIs → refer (R-AFF-05) |
| Ownership | Deed buyer's name vs the applicant (R-PRP-01) | **The applicant must own the property**: no deed in their name, or another owner → refer (R-PRP-05) |
| Existing charges | – | **Encumbrance certificate and CERSAI**: a charge, or no check possible → refer (R-PRP-06) |
| Commercial property | – | Valued by an empanelled valuer (the valuation model learnt from flats) |

**Every application now shows its loan facts:**
- The staff list (`GET /v1/staff/applications`) has a `loan` block for each application. So does the explain view.
- It holds: product, property type, amount asked and offered, income used and its source, existing EMI, FOIR and its cap, property value, value used for LTV, LTV and its cap, and which limit set the amount.
- The loan request takes `propertyType` (`residential` by default).

**Evidence:** `test_product_policy.py` (11 tests). It covers:
- the policy table;
- residential only for a home loan;
- the commercial LTV;
- the LAP cash-flow check, and that a home loan has no cash-flow rule;
- LAP ownership and encumbrance, and that a home loan has no LAP property rules;
- commercial property going to a valuer;
- Meena's real LAP meeting the new rules (credits 2.66× her EMIs, the deed in her name, no charge);
- loan facts for every application;
- `propertyType` through the API.

## Problem 3: fraud, KYC and AML refer, and a model never rejects

**What already existed (gates `identity`, `aml` and `fraud_documents`):**
- Liveness and face match (R-KYC-01).
- PAN and name matching across PAN, eKYC and documents (R-KYC-02).
- UN-list screening with namesakes cleared by date of birth, and no tipping off (R-AML-01/02).
- The fraud classifier (R-FRD-01) and the anomaly detector (R-FRD-04).
- The device, phone, email and bank-account ring (R-FRD-05) and the reused photo (R-FRD-06).
- Slip vs bank credits (R-FRD-02) and payroll (R-FRD-03).
- Editing signs (R-DOC-03) and failed document verification (R-DOC-05).

**What was added:**
- **Duplicate application (R-FRD-07):**
  - It looks at other applications on the same PAN (`store.same_pan`).
  - It refers when the name or date of birth differs, or, within 90 days, the income (by more than 25%) or the employer differs.
  - The same person applying again with the same details is noted, not flagged.
  - It runs in real mode only, because the sandbox's demo people all share one sample PAN.
- **Bank anomaly (R-FRD-08):** half or more of the money coming in is cash deposits. Entries the transaction tagger wasn't sure of are noted for the officer.

**Never on a model score alone:**
- Every fraud signal only refers: the fraud and anomaly models, the ring, the duplicate check and the cash share.
- `test_decision_table.py::test_a_suspicious_signal_refers_and_never_rejects` checks the fraud gate has no rejecting reason for any of them.
- `::test_even_a_certain_fraud_score_only_refers` holds a fraud probability of 1.0 to a refer.
- Rejections come only from policy rules and from verified document facts: a file that names an editing tool, someone else's PAN, or a slip the payroll record contradicts.

**Evidence:** `test_fraud_signals.py` (9 tests), `test_decision_table.py`, and `test_gates.py` (existing).

## Problem 4: the risk score and what is behind it

**What already existed:**
- Model 1, the live credit scorecard, sets the grade. A grade without a credit report is provisional.
- Model 2, the challenger, runs in shadow and never sets the outcome.

**What was added (`backend/risk.py`):** every record has a `risk_summary`. It holds:
- **Score, grade and category:** A/B low, C medium, D/E high.
- **Eight factors**, each with its value, detail and a rating: credit history, income, existing EMIs, FOIR, LTV, bank behaviour (bounces, cash share), employment stability (salary months, or ITR for the self-employed) and property risk (valuation confidence, title, type).
- **The scorecard's points**, highest and lowest.
- **Model confidence:**
  - input completeness;
  - the gap between the champion and the challenger, and whether their grades agree;
  - whether the grade is provisional;
  - the probability of default;
  - the models' validation, read from their own metrics files.
- **Fraud signals,** with the rule that they only refer.
- **The decision and its reasons.**

**No claim that it is production-ready:**
- The summary states `production_ready: false`.
- It quotes the test AUCs: scorecard **0.5945**, challenger **0.6206**. Both were trained and tested on L&T vehicle loans, not mortgages.
- The summary explains a decision; it never makes one. `test_the_summary_never_changes_the_decision` checks that.

**Evidence:** `test_risk_summary.py` (5 tests); the explain view.

## Problem 5: documents and property feed one decision

**What already existed (`intake.py`, for every upload):**
1. OCR in English and Hindi (model 8).
2. The type, identified by the classifier (model 6).
3. Field rules (`fields.py`).
4. The applicant match: the name score and the PAN on the card.
5. Authenticity: editing tools, PDF revisions, the same file on another application, someone else's PAN or name.
6. A verdict: verified, retake (wrong or unreadable) or failed (reject, R-DOC-05).

The property step adds:
- the valuation model with its range and confidence (model 7);
- circle rates;
- geotagged photos that must be recent and not reused;
- land records, or the deed reader with every fact quoted from the deed.

The documents gate and the property gate feed the same decision as every other gate.

**What was missing, and is now added (`models/document_ai/`):** `api/document.py` imports three functions that didn't exist:
- `ocr.extract_text(path)`;
- `extract_fields.extract_fields(text)`;
- `verification.verify_applicant(declared, extracted)`.

The new package wraps the engine's own OCR, classifier and field rules. Its result has the shape the teammate's underwriting and explainability code read (`verification_status`, `mismatches`). A mismatch is a finding for an officer, never a rejection, and a full Aadhaar number is never returned. The prototype's route `POST /api/v1/documents/ocr` now loads and answers.

**Evidence:**
- `models/document_ai/tests/test_document_ai.py` (7 tests), which reads a real rendered PAN card end to end through the prototype's own route.
- The existing `test_verification.py`, `test_api.py` and `test_real_mode.py` for the engine's document flow.

## Problem 6: the compliance checklist

**What already existed:** the compliance gate, with 8 coded rules (CMP-01 to CMP-08), each naming its regulation.

**What was added (`backend/compliance.py`, `GET /v1/staff/compliance/checklist`):**
- **Seven areas:** KYC, AML / sanctions, Fair Practices Code, Consent, Loan disclosure, Audit, and Data privacy.
- **For each area:** the regulation, the engine's rules, the code that runs them, the tests that prove them, the evidence the record keeps, and what is still missing.
- **Two new coded rules** (policy-v2):
  - **CMP-09: the decision can be reproduced.** The policy version, model inputs and model versions are in the record.
  - **CMP-10: no Aadhaar number in the record.** Only the masked number is allowed.

**A checklist is not compliance.** The checklist says so itself:
- It shows that the engine enforces these rules and that tests prove it.
- It doesn't make the platform compliant. That needs:
  - the lender's own policies;
  - legal review of the wording;
  - KYC through a regulated provider;
  - contracted data providers;
  - security controls;
  - an independent audit.

**Evidence (`test_compliance.py`, 12 tests):**
- The checklist covers all 7 areas.
- Every coded rule, CMP-01 to CMP-10, is listed, and every test and file it names exists.
- Each of CMP-02 to CMP-10 holds a decision when its condition is broken. CMP-01 is covered by `test_gates.py::test_withdrawn_consent_holds_the_decision`.

## Problem 7: one engine, and the outcome table

**Which backend decides:**
- `backend/` (Nirnay) is the only decision engine. The app calls it, and all the gates, records and replays live there.
- `api/` (LoanGuard) is the earlier dashboard prototype, with its own simpler rules. It doesn't decide the app's applications.

This note is now in `README.md`.

**The outcome table, tested in one place (`test_decision_table.py`).** Each row starts from Ravi's real recorded evidence and changes one fact:

| Situation | Outcome | Cases tested |
|---|---|---|
| Every check passes | **Approve** | both stages |
| Information missing | **Refer** | no Form 16 (R-DOC-04), income not proven (R-AFF-04), no selfie (R-KYC-01), no credit history (R-BUR-04) |
| A provider unavailable | **Refer** | bureau failed (R-BUR-06), no sanctions list (R-AML-02 + R-CMP-01), no bank data (R-AFF-04), no land records and no deed (R-PRP-01) |
| A hard policy breach | **Reject** | under 21 (R-POL-01), score below 650 (R-BUR-01), EMIs at the limit (R-AFF-01), home loan on a commercial property (R-POL-03), a document that failed verification (R-DOC-05) |
| A suspicious fraud signal | **Refer** | fraud model at 0.99 (R-FRD-01), anomaly (R-FRD-04), ring (R-FRD-05), cash deposits (R-FRD-08), same PAN with another date of birth (R-FRD-07) |
| A refer and a reject together | **Reject** | the most severe gate wins |

**All the gates run end to end:** `test_every_gate_runs_end_to_end` checks Ravi's real records.
- In-principle: identity, aml, fraud_documents, policy, affordability, risk_grade, authority, compliance.
- Final: the same, plus property, which makes the eight gates plus compliance.
- The compliance gate's ten rules all pass.

## Problem 8: explaining each decision, and replaying it

**What already existed:**
- Every record holds its sequence number, time, engine, policy and model versions, the full evidence, every gate's result and reasons, the outcome, and the customer reasons.
- Each record's SHA-256 covers its body and the previous hash.
- `GET /v1/staff/ledger/verify` checks the chain.
- `POST /v1/staff/records/{seq}/replay` re-runs the decision and re-scores the models.

**What was added (`backend/explain.py`, `GET /v1/staff/records/{seq}/explain`):** one view per record with:
- the application ID and the time of the decision;
- the data sources and a data-quality line;
- verification: identity, documents and AML;
- model outputs;
- the risk summary;
- the loan facts;
- the policy rules applied and the compliance rules;
- every gate's reasons, with what an officer should check;
- the customer reasons;
- the engine, policy and model versions;
- audit: the record's own hash recomputed, the chain check and a fresh replay. `reproducible` is true only when both the decision and the model scores come out the same.

**Replay under the record's own policy:**
- `decide()` now uses the policy version named in the record.
- `test_an_old_policy_v1_record_replays_under_v1` checks it: a home loan on a commercial property is rejected under v2 but approved, as it was, under v1. It also checks v2 changed no v1 number.

**Evidence:** `test_decision_table.py::test_explain_shows_everything_for_one_record` and `::test_explain_works_on_a_record_made_before_this_review` (a record already in a database), and the existing `test_records_replay_and_the_chain_verifies`, `test_ledger_detects_an_edited_record` and `test_record_holds_versions_and_sources`.

## Test results

Run on 8 November 2026 (Windows, CPU only).

| Suite | Result |
|---|---|
| Engine and models: `python -m pytest backend/tests models` | **343 passed, 0 failed**, 1 skipped (a scorecard test that needs the raw L&T data, which isn't in the repo), in 7 min 41 s |
| Engine (`backend/tests`) | 161 passed: the 101 from before, unchanged, plus 60 new |
| Models (`models`) | 182 passed: including 7 new for `models/document_ai` and 1 new for the deed reader |
| App (`app/`) | Not re-run: the app wasn't changed |

```bash
.venv/Scripts/python.exe -m pytest backend/tests models -q
```

## What is still not done

**Data and models:**
- **Data sources are sandbox stand-ins.** UIDAI eKYC, the bureaus, the Account Aggregator, payroll, ITD and land records (with the encumbrance certificate and CERSAI) are fictional. Real mode has DigiLocker and the bureau coded to their specs but not run live. In real mode, land records, encumbrance and CERSAI aren't connected, so every LAP is referred for the encumbrance check (R-PRP-06).
- **The models are not validated for Indian mortgages:**
  - The credit scorecard (AUC 0.5945) and the challenger (0.6206) learnt from vehicle loans.
  - The fraud classifier learnt from BAF, a synthetic, non-Indian account-opening dataset.
  - The valuation model learnt from flat listings (typical error 12.9%), not commercial property.
  - None of this is production-ready. It needs mortgage outcome data, back-testing and a model-risk review.

**Product rules:**
- **The thresholds are illustrative:** LAP cash flow 2× EMIs, cash share 50%, the duplicate window of 90 days with a 25% income difference, and the 50% commercial LAP LTV. A lender's credit policy would set them. Only the HL LTV tiers come from RBI.
- **The LAP cash-flow check uses average monthly credits,** not the average bank balance or a full banking-surplus analysis.

**App:**
- **The app doesn't ask for the property type yet.** It sends residential by default. When a selector is added, the customer text for R-POL-03 needs adding in the 23 languages.

**Fraud checks:**
- **The duplicate check runs in real mode only.** It sees only this engine's applications, not other lenders' (that needs a shared fraud registry or the bureau's).

**Compliance:**
- **Compliance is shown, not certified.** There's no retention and erasure schedule, consent manager, breach process or WORM storage yet (see the checklist's "limits").

**`api/` (LoanGuard):**
- It still has its own audit log, which can't replay. That's by design: it's the prototype, and `backend/` is the decision engine.

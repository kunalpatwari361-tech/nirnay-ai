"""The compliance checklist: each area, the rule in the engine, where it comes from, how it is
tested, and what evidence the decision record keeps. GET /v1/staff/compliance/checklist.

This maps regulation to code so a reviewer can check it. It is not a certificate: the engine
being consistent with these rules doesn't make the lender compliant. That needs the lender's
own policies, legal review of the wording, KYC done by a regulated entity, real data providers
under contract, information-security controls, and an audit by the compliance function.
backend/tests/test_compliance.py checks that every coded rule is in this list and every test
named here exists.
"""
from __future__ import annotations

NOT_A_CERTIFICATE = ("A checklist of rules the engine enforces, with their tests. It does not make the platform "
                     "compliant: that needs the lender's policies, legal review, regulated KYC and data providers, "
                     "security controls and an independent audit.")

CHECKLIST = [
    {"area": "KYC",
     "source": "RBI Master Direction - KYC, 2016 (as amended): customer identification, V-CIP, Aadhaar offline/e-KYC",
     "rules": ["CMP-02 KYC completed", "R-KYC-01 live selfie and face match", "R-KYC-02 names agree across PAN, eKYC and documents"],
     "code": ["backend/gates.py identity", "backend/gates.py compliance (CMP-02)", "backend/live_selfie.py", "backend/intake.py verify"],
     "tests": ["backend/tests/test_gates.py::test_spoofed_selfie_refers_to_video_kyc",
               "backend/tests/test_gates.py::test_face_mismatch_refers",
               "backend/tests/test_real_mode.py::test_without_a_face_on_the_id_card_the_face_match_refers",
               "backend/tests/test_compliance.py::test_cmp02_no_selfie_holds_the_decision"],
     "evidence": "evidence.identity (liveness, face similarity, name scores), the identity gate's reasons, data_sources.identity",
     "limits": "Sandbox eKYC is a stand-in. Real KYC needs a regulated provider (DigiLocker/UIDAI via a KUA) and V-CIP where required."},
    {"area": "AML / sanctions",
     "source": "PMLA 2002 and the PML Rules; RBI KYC Directions ch. on UNSC lists (UAPA s.51A)",
     "rules": ["CMP-03 sanctions screening ran", "R-AML-01 possible list match refers", "R-AML-02 list unavailable refers",
               "no tipping off: the customer sees a general review"],
     "code": ["backend/checks/aml.py screen", "backend/gates.py aml", "backend/reasons.py (customer code R-RSK-01)"],
     "tests": ["backend/tests/test_gates.py::test_aml_hit_refers_without_tipping_off",
               "backend/tests/test_compliance.py::test_cmp03_no_sanctions_list_holds_the_decision",
               "backend/tests/test_decision_table.py::test_provider_unavailable_refers"],
     "evidence": "evidence.aml (lists and their dates, hits, cleared namesakes), data_sources.sanctions_lists",
     "limits": "The UN list is real; the PEP list is a sandbox one. Production needs a licensed PEP and adverse-media source."},
    {"area": "Fair Practices Code",
     "source": "RBI Fair Practices Code for HFCs (Master Direction - HFC, 2021, ch. on FPC): reasons for rejection in writing",
     "rules": ["CMP-06 every adverse outcome has a reason", "customer reasons from a reviewed set, in the customer's language",
               "CMP-07 AI is not the final judge (LLM findings only refer)"],
     "code": ["backend/gates.py compliance (CMP-06, CMP-07)", "backend/pipeline.py _customer_reasons", "backend/letters.py"],
     "tests": ["backend/tests/test_gates.py::test_low_bureau_score_rejects",
               "backend/tests/test_compliance.py::test_cmp06_an_adverse_outcome_without_a_reason_is_held",
               "backend/tests/test_compliance.py::test_cmp07_an_llm_rejection_is_held"],
     "evidence": "gates[].reasons with officer text and checks; customer_reasons; the decision letter",
     "limits": "Letter wording needs legal review; translations need a native-speaker review."},
    {"area": "Consent",
     "source": "DPDP Act 2023 s.6 (consent); RBI Digital Lending Directions 2025 (explicit consent before data pull)",
     "rules": ["CMP-01 consent before data pull", "no data pull without consent (API refuses)"],
     "code": ["backend/intake.py record_consents, consents_ok", "backend/gates.py compliance (CMP-01)"],
     "tests": ["backend/tests/test_gates.py::test_withdrawn_consent_holds_the_decision",
               "backend/tests/test_api.py::test_no_data_pull_without_consent"],
     "evidence": "evidence.consents (what was given, when), the consent log in the database",
     "limits": "Consent text needs legal review; a consent manager (DPDP) isn't integrated."},
    {"area": "Loan disclosure",
     "source": "RBI Key Facts Statement circular (Apr 2024); Digital Lending Directions 2025 (KFS, APR, cooling-off); "
               "RBI HFC Directions (LTV tiers for housing loans)",
     "rules": ["CMP-05 Key Facts Statement with APR", "CMP-08 cooling-off period stated", "CMP-04 LTV within RBI tiers"],
     "code": ["backend/pipeline.py kfs", "backend/gates.py compliance (CMP-04, CMP-05, CMP-08)", "backend/policy.py ltv_cap"],
     "tests": ["backend/tests/test_compliance.py::test_cmp05_an_offer_without_an_apr_is_held",
               "backend/tests/test_compliance.py::test_cmp08_an_offer_without_a_cooling_off_period_is_held",
               "backend/tests/test_compliance.py::test_cmp04_ltv_above_the_rbi_tier_is_held"],
     "evidence": "offer.kfs (rate, EMI, fees, GST, APR, total payable, cooling-off days), the compliance gate's rules",
     "limits": "Fee and rate values are illustrative. The KFS format must match the lender's approved template."},
    {"area": "Audit",
     "source": "RBI FREE-AI framework (2025): audit trail and explainability; RBI IT governance directions (record keeping)",
     "rules": ["CMP-09 decision can be reproduced", "hash-chained ledger", "replay under the record's own policy version"],
     "code": ["backend/ledger.py", "backend/pipeline.py run, replay", "backend/explain.py", "backend/policy.py POLICIES"],
     "tests": ["backend/tests/test_api.py::test_records_replay_and_the_chain_verifies",
               "backend/tests/test_api.py::test_ledger_detects_an_edited_record",
               "backend/tests/test_api.py::test_record_holds_versions_and_sources",
               "backend/tests/test_decision_table.py::test_explain_shows_everything_for_one_record",
               "backend/tests/test_decision_table.py::test_an_old_policy_v1_record_replays_under_v1",
               "backend/tests/test_compliance.py::test_cmp09_a_record_without_model_versions_is_held"],
     "evidence": "every record: seq, hash, prev_hash, at, engine, policy and model versions, evidence, gates, outcome, risk_summary",
     "limits": "Records sit in a file or database table; production adds WORM storage and access logging."},
    {"area": "Data privacy",
     "source": "DPDP Act 2023 (purpose limitation, security safeguards); Aadhaar Act 2016 and UIDAI rules (masked number only)",
     "rules": ["CMP-10 no Aadhaar number in the record", "Aadhaar card images never stored", "live selfie frames not stored",
               "staff routes need a key", "phone numbers masked in staff lists"],
     "code": ["backend/fields.py (Aadhaar last four only)", "backend/gates.py compliance (CMP-10)", "backend/app.py staff",
              "backend/intake.py upload_document (an Aadhaar file is never kept)"],
     "tests": ["backend/tests/test_compliance.py::test_cmp10_an_aadhaar_number_in_the_record_is_held",
               "backend/tests/test_real_mode.py::test_everything_is_in_the_database_and_survives_a_restart",
               "backend/tests/test_api.py::test_staff_routes_need_the_key"],
     "evidence": "the compliance gate's CMP-10 result; documents table (Aadhaar file empty); masked fields",
     "limits": "No retention and erasure schedule, breach process or data-protection officer workflow in code yet."},
]


def coded_rules() -> list[str]:
    """Every CMP rule ID named in the checklist."""
    return sorted({r.split(" ")[0] for row in CHECKLIST for r in row["rules"] if r.startswith("CMP-")})


def checklist() -> dict:
    return {"note": NOT_A_CERTIFICATE, "areas": CHECKLIST, "coded_rules": coded_rules()}

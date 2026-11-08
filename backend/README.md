# Nirnay decision engine (backend)

The FastAPI engine behind the customer app. It runs **all 12 models**, the rule-based checks, the **eight gates** from the report plus the **compliance gate**, and writes every decision to a **hash-chained record** that can be replayed.

**AI is never the final judge.** Rules and validated models set every outcome. The LLM only reads and drafts, and its findings can only send a case to an officer.

## Run it

From the repo root (`K:\IIt b`), with the venv from the main README:

```bash
.venv/Scripts/python -m pip install -r backend/requirements.txt
```

```bash
.venv/Scripts/python -m backend.fetch
```

```bash
.venv/Scripts/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

- `backend.fetch` downloads the face-match and Hindi OCR weights (checking their SHA-256) and refreshes the UN sanctions list. The liveness weights are already in the repo.
- Open `http://127.0.0.1:8000/docs` for the interactive API, and `/v1/models` to see each model's status.
- Point the app at it: `flutter run -d chrome --dart-define=NIRNAY_API_URL=http://127.0.0.1:8000` (see [app/README.md](../app/README.md)).

**Settings** (environment variables; nothing secret has a default):

| Variable | What it does |
|---|---|
| `NIRNAY_SANDBOX` | `1` (default): fictional people and stand-in data sources, OTP `123456`. `0`: **real mode** (below) |
| `DATABASE_URL` | Supabase PostgreSQL (session pooler string). Unset: SQLite in `backend/var/nirnay.db` |
| `NIRNAY_SMS_PROVIDER` | `twilio` or `msg91` for real SMS codes (with `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_VERIFY_SERVICE_SID`, or `MSG91_AUTH_KEY`, `MSG91_TEMPLATE_ID`). `sandbox` (the fixed demo code) is refused in real mode. |
| `NIRNAY_BUREAU`, `DECENTRO_CLIENT_ID`, `DECENTRO_CLIENT_SECRET` | `decentro`: pull the credit report with the applicant's PAN through Decentro (`bureau.py`). `DECENTRO_BASE_URL` defaults to the staging server; `DECENTRO_BUREAU` is `EQ` (default), `EX` or `CR`. Sandbox mode uses its own stand-in bureau. |
| `DIGILOCKER_CLIENT_ID`, `DIGILOCKER_CLIENT_SECRET`, `DIGILOCKER_REDIRECT_URI` | Turn on DigiLocker sign-in (`digilocker.py`, Meri Pehchaan API v2.4) for name, date of birth, PAN and e-Aadhaar. Needs a DigiLocker requester registration. `NIRNAY_DIGILOCKER=off` hides it; sandbox mode has a stand-in. |
| `DIGILOCKER_PURPOSE`, `DIGILOCKER_SERVICE_NAME`, `DIGILOCKER_DOCTYPES` | What DigiLocker's consent screen shows. Defaults: `kyc`, `Nirnay home loan`, and `ADHAR,PANCR` (only the Aadhaar and PAN records; empty asks for none in particular). Letters, digits, spaces and underscores only. |
| `CASHFREE_CLIENT_ID`, `CASHFREE_CLIENT_SECRET`, `CASHFREE_PUBLIC_KEY_FILE` | Or DigiLocker through Cashfree Secure ID, a DigiLocker partner (no requester registration of your own). Its sandbox signs in to the real DigiLocker. The public key signs each call for Cashfree's 2FA (leave it unset if the server's IP is whitelisted). `CASHFREE_VERIFICATION_URL` defaults to the sandbox. |
| `NIRNAY_STORE_FILES` | `1` (default): keep uploaded documents in the database for officers. Aadhaar images are never kept. |
| `NIRNAY_STAFF_KEY` | Turns on the staff routes; send it as `X-Staff-Key` |
| `ANTHROPIC_API_KEY` | Turns on Claude for model 11 (or `NIRNAY_LLM=on` with an `ant auth login` profile). Without it, offline rules stand in. |
| `BHASHINI_USER_ID`, `BHASHINI_ULCA_API_KEY` | Turn on Bhashini for model 12 |
| `NIRNAY_CORS_ORIGINS` | Browser origins allowed (default: the web build on port 8765) |
| `NIRNAY_VAR_DIR` | Where the local SQLite database goes (default `backend/var/`, git-ignored) |

## Real mode and the database

With `NIRNAY_SANDBOX=0` the engine serves real applicants. **[DEPLOY.md](DEPLOY.md)** is the step-by-step guide: Supabase, Twilio, Render and the shareable APK.

- **Sign-in:** OTPs go by SMS through Twilio Verify or MSG91, which also check the code. The engine caps sends at 3 codes per number every 15 minutes, and tries at 5 per code.
- **Details:** the applicant types their work, employer, income and EMIs. Each typed fact is marked *self-declared* in the decision record.
  - **DigiLocker** (when connected): name, date of birth, address, PAN and e-Aadhaar come from there, so the PAN and Aadhaar card uploads are dropped. The files are checked against DigiLocker's HMAC and parsed with `defusedxml`; only the last 4 Aadhaar digits are kept, and the e-Aadhaar photo is used in memory for the face match, then dropped. Without DigiLocker the applicant types their name and date of birth and uploads the cards.
    - **The link (spec v2.4):** it carries the purpose and service name shown on DigiLocker's consent screen, asks only for the Aadhaar and PAN records, and opens DigiLocker's mobile sign-in with the applicant's number filled in. After reading the files the engine revokes the token. If DigiLocker also shares the account's mobile number, one that differs from the sign-in number is noted for the officer; the number itself isn't kept.
    - **Through Cashfree:** the engine makes the link, and asks Cashfree for the result each time the app checks (Cashfree doesn't call back). Its Aadhaar and PAN answers are kept the same way (last 4 digits only, photo in memory only).
    - **From the documents step:** DigiLocker can also be opened after the details were typed, instead of photographing the PAN and Aadhaar cards. The PAN must be the one entered (otherwise nothing is used). DigiLocker's verified name, date of birth and address then replace the typed ones. A typed name far from DigiLocker's refers the case (R-KYC-02), and a different date of birth is noted for the officer.
  - **Credit report:** never asked of the applicant. With a bureau connected it's pulled with the PAN, and its EMIs count if they're higher than the typed ones. A report from a staging server, a failed pull, or no bureau connected (`not_pulled`) all refer the case (R-BUR-06) so an officer pulls it.
  - **The applicant's own credit report:** when no bureau answered, the applicant can upload the free report they download from CIBIL, Experian, Equifax or CRIF (`creditReport` slot, `credit_report.py`, rules not a model). The engine reads the bureau, score, report date, name and PAN. A report more than 90 days old, or with no readable date, is turned away (`R-DOC-06`, download a new one). The name and PAN are checked like any document's, so someone else's report fails verification (R-DOC-05). A verified report fills the score (`applicant_report`): it counts for the in-principle offer, a score below the minimum refers instead of rejecting (a misread must never reject), and before sanction an officer confirms it with the bureau (R-BUR-07). Each new upload starts again from the bureau's own answer, so a failed one takes the earlier score away.
- **Documents prove them:** they're required, and a bank statement is added because there's no Account Aggregator.
  - **Identity:** the selfie must match the face on the PAN or Aadhaar card. Real mode never skips the face match.
  - **Live selfie:** the selfie must be taken live in the app. The engine sets a head-turn challenge (look straight, then turn left and right, in a random order; single use, 2 minutes) and the app's front camera sends one frame per step. The engine checks there's exactly one face in each frame, the same person throughout (model 9), the head turning both ways from the straight frame, and passive liveness on the straight frame (model 10). A phone that mirrors its photos still passes. A selfie sent without the challenge refers the case (R-KYC-01) for a video KYC call. The frames aren't kept: only their scores are.
  - **Names:** the names on the documents must match the declared name.
  - **Income:** comes from the bank statement (`statement.py` reads its rows; then model 5), the salary slip or the ITR, not from what was typed.
  - **Title:** with no land records, it's read from the sale deed. No deed means Refer.
- **Lists:** the fictional sandbox PEP list is not used, and the sandbox sample documents are switched off.
- **Database (`db.py`):** every step is saved as it happens and survives restarts.
  - **Tables:** applications, sessions, OTP attempts, an append-only consent history, documents with their checks and files, and the hash-chained decision records.
  - **On Supabase:** the tables live in a private `nirnay` schema with RLS on and no access for the API roles, and the connection uses TLS.
- **Staff:** `GET /v1/staff/applications` lists every application.

## How the 12 models are wired in

| # | Model | Where the engine uses it |
|---|---|---|
| 1 | Credit scorecard | **Risk grade** gate: grade C refers (`R-RSK-01`), D and E reject (`R-RSK-02`). With no credit report pulled the grade is provisional, so C to E refer to an officer instead (`R-RSK-01`); a grade from the applicant's own report is not provisional, but D and E still refer until an officer confirms the score |
| 2 | Credit challenger | Scored and recorded next to model 1, in **shadow**. It never changes the outcome (a test checks this). |
| 3 | Fraud classifier | **Fraud & documents** gate: review or high alert refers (`R-FRD-01`) |
| 4 | Anomaly detector | **Fraud & documents** gate: unusual or broken integrity rule refers (`R-FRD-04`) |
| 5 | Transaction tagger | Tags the Account Aggregator statement when the profile is fetched. Its salary, EMI and bounce totals feed **Affordability** and the slip check. |
| 6 | Document classifier | Every upload: wrong type `R-DOC-01`, unreadable `R-DOC-02` |
| 7 | Property AVM | **Property** gate (final stage): price above range `R-PRP-03`, below circle value `R-PRP-04`, unsure `R-PRP-02`. LTV uses the lower of the price and the estimate. |
| 8 | OCR | Every upload, English and Hindi. Its text feeds model 6, the field rules and the deed reader. |
| 9 | Face match | Selfie against the Aadhaar/PAN photo, whichever arrives second; **Identity** gate (`R-KYC-01`) |
| 10 | Liveness | Every selfie; **Identity** gate (`R-KYC-01`) |
| 11 | LLM | Deed reader on sale-deed uploads (**Property** gate, refer only); officer file notes; letters; help assistant |
| 12 | Translation | Letters and assistant answers in the customer's language |

**Rule-based checks (no training):** AML name matching against the UN list and a sandbox PEP list (rapidfuzz + jellyfish); fraud rings across applications (networkx); reused property photos (imagehash) and duplicate document files (SHA-256); PDF and photo tamper checks (pypdf, Pillow); field reading from OCR text; the policy table.

### Bridging the training data

Models 1, 2 and 4 learned from L&T **vehicle** loans and model 3 from BAF. `adapters.py` writes down how a home-loan application becomes their inputs. Nothing is tuned to the demo cases:
- **Credit:** bureau inputs go in unchanged. Loan amount goes in as missing (0 points), because ₹45 lakh is off the vehicle scale. FOIR judges affordability instead.
- **Anomaly:** loan and property value are rescaled to a typical L&T loan (×0.02), which keeps every ratio the forest learned.
- **Fraud:** what we can measure in India is measured: name vs email, OTP-verified phone, address and account age, emails per device, minutes spent applying. The rest use the median of genuine BAF applications (`data/fraud_neutral.json`), so they push the score neither way.

## The decision

Each stage **gathers** evidence (data sources and models, as plain JSON), then **decides** with pure gate functions, then **records** the result.

| Gate | Refers when | Rejects when |
|---|---|---|
| 1 Identity | No live face, face mismatch, names differ across PAN, eKYC and documents | – |
| 2 AML | Close name match with a matching or unknown date of birth. Namesakes with a different date of birth are cleared automatically. | – |
| 3 Fraud & documents | Fraud or anomaly alert, ring, missing/unreadable/wrong document, a doubtful file (edited after it was made, or revisions appended), slip above bank salary credits | A document failed verification (`R-DOC-05`: it names an editing tool, is the same file as on another applicant's application, its PAN isn't the one entered, or its name is another person's); the slip doesn't match the employer's payroll record (`R-FRD-03`) |
| 4 Policy | No credit history, score 650–699, loan past the maximum age | Under 21, score below 650 |
| 5 Property (final) | Title gap (land records or the deed reader), valuation unsure or price out of range, below circle value, photos not geo-tagged, old or reused | – |
| 6 Affordability | 2+ bounces, no salary credits seen, business credits below 80% of ITR income | EMIs already at the FOIR limit |
| 7 Risk grade | Grade C | Grade D or E |
| 8 Authority | Above ₹1 crore | – |
| Compliance | Any of 8 coded rules fails: consent before data pull, KYC done, screening ran, RBI LTV tiers, KFS with APR, a reason for every adverse outcome, AI not the final judge, cooling-off stated | – |

**Document verification.** Every upload gets a verdict and a 0–100 score (`intake.verify`), and no one is asked to confirm what was read:
- **verified:** every check passed. The score is the weakest check: the classifier's confidence, the name match, and 60 at most for a doubtful file.
- **retake:** the classifier couldn't confirm it's the document asked for, or OCR couldn't read it, so nothing could be checked.
- **failed:** the reasons in `R-DOC-05` above. The score drops to 10 or less and the application is rejected. Uploading the document again doesn't clear it: the failure stays on the file.

The rejection is made by rules, not by a model on its own (CMP-07). The app sees the verdict, the score and the non-fraud checks (right document, readable, name, PAN); which fraud check failed stays in the record for officers. The applicant's own earlier applications (same PAN) don't count as reuse. Names score below 50 only when they belong to someone else; 50–80 still refers (`R-KYC-02`). A rejection shows the customer only its rejecting reasons. The decision also carries the bureau's credit score and the verification score (the lowest document's).

The most severe result wins. Customers see reason codes from the app's reviewed set, in their language. **Fraud and AML reasons show only as a general review** (`R-RSK-01`), because PMLA forbids tipping off. Officers see the exact internal code, what it means and what to check (`reasons.py`).

**Records:** `ledger.py` appends each decision with its evidence, rule and model versions, gate results and outcome to a JSON-lines file. Each record's SHA-256 covers its body and the previous hash, so any edit breaks the chain. `GET /v1/staff/ledger/verify` finds where. `POST /v1/staff/records/{seq}/replay` re-runs the gates on the stored evidence and re-scores models 1–4 from their stored inputs; both must match.

## API

Customer routes need `Authorization: Bearer <token>` from OTP sign-in and the same `X-Device-Id`. A session reaches only its own application.

| Method and path | What it does |
|---|---|
| `POST /v1/otp/send`, `POST /v1/otp/verify` | Sign in (sandbox OTP `123456`; 5 tries, then locked) |
| `PUT /v1/applications/{id}/consents` | Records the needed and optional consents and their options (months, accounts, KYC method) |
| `POST /v1/applications/{id}/profile` | eKYC, PAN, bureau, Account Aggregator (model 5). Refused until the needed consents are recorded. |
| `POST /v1/applications/{id}/digilocker` | DigiLocker's sign-in link (PKCE, or Cashfree's link). Body `{"returnTo": "app"}` sends the browser back to `nirnay://digilocker` at the end. Before or after the details are saved. |
| `GET /v1/applications/{id}/digilocker` | What DigiLocker shared: `pending`, `done` (name, date of birth, masked PAN and Aadhaar), `denied` or `failed` (e.g. `pan_mismatch`). After the details are saved, also `requiredDocuments`. |
| `GET /v1/digilocker/return` | Where Cashfree sends the browser at the end (an https engine only): back to the app |
| `GET /v1/digilocker/callback` | Where DigiLocker returns the applicant's browser |
| `POST /v1/applications/{id}/selfie/challenge` | A live-selfie challenge: `challengeId`, `steps` (e.g. `["center", "right", "left"]`), `expiresIn` |
| `POST /v1/applications/{id}/selfie/live` | One frame per step (`frames`, in order) with the `challengeId`: head turn, same person, models 9 and 10 |
| `POST /v1/applications/{id}/selfie` | A single photo, models 9 and 10 (sandbox demos; real mode refers it) |
| `POST /v1/applications/{id}/documents` | Models 8 and 6, field reading, tamper and duplicate checks, ID-photo face, deed reader for sale deeds; the verdict and verification score |
| `POST /v1/applications/{id}/in-principle` | Stage A decision and Key Facts Statement |
| `POST /v1/applications/{id}/final` | Stage B with property details and geo-tagged photos (model 7, title) |
| `GET /v1/applications/{id}` | Status |
| `GET /v1/applications/{id}/letter?lang=hi` | Letter: fixed template, LLM paragraphs, translated |
| `POST /v1/applications/{id}/assistant` | Help assistant, answered in the customer's language |
| `GET /v1/models` | Status of all 12 models and the checks |
| `GET /v1/sandbox/personas/{ravi,meena,arjun,priya}/documents/{slot}` | Sample documents for demos (PAN, Aadhaar, salary slip, Form 16, ITR, GST, bank statement, sale deed) |
| `GET /v1/staff/applications/{id}/file-note` | LLM file note for officers |
| `GET /v1/staff/applications/{id}/records`, `GET /v1/staff/records/{seq}` | Decision records |
| `POST /v1/staff/records/{seq}/replay`, `GET /v1/staff/ledger/verify` | Replay and chain check |

## Demo cases (sandbox)

The data sources are fake. The models and rules that judge the data are the real ones.

| Person | Phone | Result |
|---|---|---|
| Ravi Kumar, salaried | 98765 00001 | HL ₹45 lakh on ₹60 lakh: **approved** in both stages, EMI ₹39,767 at 8.75% |
| Meena Sharma, shop owner | 98765 00002 | LAP ₹60 lakh on ₹1.2 crore: **approved in principle, referred at the property**. Land records and the deed reader both find the deceased co-owner. |
| Arjun Mehta, salaried | 98765 00003 | HL ₹30 lakh: **rejected**. OCR reads ₹93,000 net pay on his slip; bank credits and payroll say ₹62,000. |
| Anyone else | any number | Priya Nair, a clean salaried applicant |

Choices in the app change the result. Share only the savings account through the Account Aggregator, for example, and no salary credits are seen (`R-AFF-04`).

## Tests

```bash
.venv/Scripts/python -m pytest backend
```

91 tests; OCR is the slow part (a few minutes on a quiet PC):
- **`test_api.py`:** the three demo cases end to end through HTTP, sample documents recognised, replay and chain check, letters and assistant offline, file notes, OTP lockout, session/device binding, no data without consent, the savings-only account choice, wrong documents, staff key, and an edited record breaking the chain.
- **`test_cashfree_digilocker.py`:** DigiLocker through a stand-in of Cashfree: the link, waiting, the shared Aadhaar (last 4 digits only) and PAN, saying no, an expired link, the 2FA signature, and DigiLocker from the documents step replacing the typed details (a different PAN is refused).
- **`test_digilocker_bureau.py`:** DigiLocker's files (e-Aadhaar and PAN record) read with only the last 4 digits kept, unsafe or tampered files refused, the v2.4 link (purpose, service name, Aadhaar and PAN only, the mobile filled in), the sandbox sign-in, saying no, the token revoked after reading, DigiLocker's mobile compared and not kept, and real mode with DigiLocker and the credit report.
- **`test_live_selfie.py`:** the head turn in either order or mirrored, measured from the straight frame; a still photo, both turns the same way, a second face, a missing face, a different face and a spoof all failing; challenges single-use, per application and expiring; and the route with the real face models.
- **`test_gates.py`:** each gate rule on Ravi's real recorded evidence with one fact changed: spoofed selfie, face mismatch, AML hit (shown to the customer only as a review), fraud alert, low score, no history, age, bounces, EMI limit, ₹1 crore limit, missing and edited documents, a document that failed verification, records from before verdicts deciding as they did, withdrawn consent, and the challenger never changing the outcome.
- **`test_verification.py`:** the score as the weakest check; edited, reused and someone else's documents failing; unreadable or wrong documents as retakes; a card with another PAN rejecting the application, the failure staying after a retake; and the applicant's own files on an earlier application not counting as reuse.

The selfie test uses Minivision's sample photo when it is on the machine (`NIRNAY_FACE_SAMPLES`, default `K:/tools/downloads/silent-face`); the repo holds no face photos.

## Not done yet

- **Live LLM and translation runs.** No Anthropic, Bhashini or Hugging Face credentials were available, so Claude, Bhashini and IndicTrans2 are tested only through mocks. The phrasebook and the offline rules are what ran.
- **Real data sources.** UIDAI, the bureaus, the Account Aggregator, payroll/EPFO, ITD and land records are sandbox functions in `sandbox.py`. Each is one function to replace with a real adapter.
- **Storage hardening.** Applications are in the database, but personal data in records isn't encrypted at the application level (Supabase encrypts its disks), and the ledger isn't copied to WORM storage. Run one engine instance, because cross-application checks also use an in-memory copy.
- **Real data sources.** Real mode replaces the bureau, Account Aggregator, eKYC, payroll and land records with self-declared facts and documents. Licensed connections would replace those one at a time.
- **DigiLocker and the bureau, live.** Both are written to the published specs (DigiLocker's Meri Pehchaan API v2.4; Decentro's credit report API) and tested against mocks and the sandbox stand-ins. Neither has run against the real service yet: DigiLocker needs the lender registered as a requester (or a Cashfree account), and Decentro needs an account.
- **Officer actions.** Officers can read file notes and records, but there is no route yet to record their decision on a referred case. That belongs to the staff portal.
- **Speed.** A document takes 4–9 seconds (two OCR passes on a CPU). The report's under-2-second target is for the decision itself, which takes well under a second once the evidence is in.

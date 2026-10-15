# RemiMinderAI Product Requirements Document

| Field | Value |
|---|---|
| Product | RemiMinderAI |
| Document status | Draft for stakeholder review |
| Version | 1.0 |
| Last updated | October 15, 2026 |
| Platforms | iOS and Android (shared Flutter application) |
| Primary users | Patients and caregivers |

## 1. Executive summary

RemiMinderAI is a mobile healthcare companion that helps adults capture and understand healthcare visits, follow medication and care instructions, and share selected information with trusted caregivers.

The product serves two connected experiences:

- **Patients** record visits, scan health documents, receive plain-language summaries, manage reminders, and control caregiver access.
- **Caregivers** monitor connected patients, review shared visit information, follow reminder activity, and respond to alerts.

RemiMinderAI is one cross-platform product delivered through a shared Flutter codebase. This PRD therefore applies to both iOS and Android. Platform-specific requirements are identified where permissions, authentication, notifications, deep links, billing, or store policies differ.

This document distinguishes:

- **Current** — behavior evidenced in the repository.
- **MVP requirement** — behavior required for a dependable public release.
- **Later** — valuable work intentionally outside the MVP.

## 2. Problem statement

Healthcare instructions are often delivered verbally or across disconnected documents. Patients may forget details, misunderstand terminology, or miss medication and follow-up tasks. Caregivers frequently lack timely, permissioned visibility into the information needed to help.

Existing tools tend to solve only one part of this problem: recording, note taking, reminders, or caregiver monitoring. RemiMinderAI should provide a connected workflow from capture to understanding to action while keeping the patient in control of sensitive health information.

## 3. Product vision

Give every patient a clear, actionable record of their care and an easy way to involve trusted caregivers without replacing professional medical advice.

## 4. Goals and success measures

### 4.1 Product goals

1. Make it simple and trustworthy to capture a healthcare visit or health document.
2. Turn captured information into understandable summaries and reviewable actions.
3. Improve completion of medication and follow-up reminders.
4. Let patients safely share selected information with caregivers.
5. Deliver reliable core behavior on both iOS and Android.

### 4.2 Proposed success measures

Targets must be confirmed after baseline analytics are available.

| Outcome | Proposed measure |
|---|---|
| Activation | Percentage of verified users who complete one core action within 24 hours: create a reminder, record a visit, or scan a document |
| Capture success | Percentage of submitted recordings and scans that reach a completed or clearly recoverable state |
| Time to value | Median time from recording upload to a viewable summary |
| Reminder engagement | Percentage of due reminders marked taken/completed, snoozed, or skipped |
| Caregiver connection | Percentage of sent invitations that become active care-team relationships |
| Reliability | Crash-free sessions and API success rate for core workflows |
| Trust | Account deletion success rate and support contacts related to incorrect sharing or inaccessible data |

Analytics must not include raw audio, transcripts, health-document contents, medication names, AI prompts, or other protected health information.

## 5. Non-goals

The MVP will not:

- Diagnose conditions, recommend treatment, or replace a clinician.
- Interpret results as medically normal or abnormal without clinician-authored context.
- Provide emergency response or guarantee caregiver intervention.
- Integrate directly with electronic health record systems.
- Support clinicians as a separate user role.
- Claim complete offline operation.
- Automatically create or change a medication plan without patient confirmation.
- Target children under 13.

## 6. Users and needs

### 6.1 Patient

An adult managing medications, appointments, or ongoing care.

Needs:

- Capture information without complex setup.
- Understand what happened and what to do next.
- Receive dependable reminders.
- Correct AI-generated content.
- Decide who can see health information.
- Delete information and the account.

### 6.2 Caregiver

A trusted family member, friend, or other non-clinical helper supporting one or more patients.

Needs:

- Accept a clear, patient-authorized invitation.
- Switch safely among connected patients.
- Understand what information is shared.
- See relevant reminders, summaries, and alerts.
- Avoid acting on stale, incomplete, or AI-generated information as if it were clinical advice.

### 6.3 Product and support operator

An authorized operator maintaining service reliability and responding to user requests.

Needs:

- Diagnose processing failures without unnecessary access to health content.
- Monitor system health and job status.
- Fulfill deletion and privacy requests.
- Audit sensitive access where required.

An operator-facing product interface is outside this MVP; operational needs may be met through secured cloud tooling.

## 7. Product principles

1. **Patient control:** sharing is explicit, revocable, and understandable.
2. **Human confirmation:** AI output is a draft or aid, not a clinical authority.
3. **Minimum necessary data:** collect and expose only what a workflow needs.
4. **Visible state:** processing, failure, and stale data are clearly labeled.
5. **Accessible defaults:** plain language, readable layouts, and localized experiences.
6. **Cross-platform parity:** core outcomes match across iOS and Android even when system interactions differ.

## 8. Scope and priority

Priority definitions:

- **P0:** required for MVP release.
- **P1:** important follow-up after MVP stability.
- **P2:** later opportunity.

| Capability | Priority | Current state |
|---|---:|---|
| Authentication and role selection | P0 | Partial; routing trusts client-held role state |
| Patient home and task overview | P0 | Partial; task completion controls are unfinished |
| Visit audio capture and AI summary | P0 | Implemented pipeline |
| Reminder creation and due notifications | P0 | Implemented, reliability hardening needed |
| Care-team invitation and access control | P0 | Partial; Cloud SQL and Firestore flows are asymmetric |
| Caregiver patient overview and alerts | P0 | Implemented |
| Privacy controls and account deletion | P0 | Partial; reauthentication and cross-store revocation need verification |
| RemiScan upload and OCR | P0 | Partial; displayed extraction is not yet dependable |
| Editable confirmation before actions from AI/scan | P0 | Gap |
| Subscription entitlement and limits | P1 | Partial |
| RemiVox health-information assistant | P1 | Partial; briefing UI exists, interactive Q&A is backend-only |
| Broad localization | P1 | Ten languages present; end-to-end quality unverified |
| Adherence trends and insights | P2 | Aspirational |
| Full offline-first sync | P2 | Documented but not implemented |
| Provider/EHR integrations | P2 | Aspirational |

## 9. Core user journeys

### 9.1 Patient onboarding

1. User opens RemiMinderAI and selects the patient role.
2. User creates an account or signs in.
3. The app presents legal terms, privacy information, and role-appropriate context.
4. The app requests permissions only when the related feature is used.
5. User reaches the patient home screen.

**Success:** the authenticated user reaches the correct role experience and can understand the next useful action.

### 9.2 Record and summarize a visit

1. Patient starts a visit recording.
2. App explains consent and requires affirmative confirmation.
3. App requests microphone access and shows recording state and duration.
4. Patient stops and submits the recording.
5. App shows upload and processing status.
6. Backend transcribes and creates a structured plain-language summary.
7. Patient reviews the summary, disclaimer, and proposed reminders/tasks.
8. Patient confirms or edits actions before they become active.
9. Shared caregivers see only information allowed by their current permission.

**Failure path:** interrupted uploads can be retried; failed processing shows a reason and recovery action without losing the entire visit record where technically possible.

### 9.3 Scan a health document

1. Patient chooses prescription, medication label, or lab document.
2. App explains image use and requests camera permission.
3. Patient captures and confirms a readable image.
4. App uploads and processes the image.
5. Extracted content is labeled as unverified.
6. Patient reviews and corrects fields.
7. Patient explicitly confirms any reminder or task creation.

**Success:** saved information comes from backend processing rather than mock client results and remains traceable to the source image.

### 9.4 Complete a reminder

1. Patient creates or confirms a reminder with schedule and notification options.
2. App schedules a local notification and synchronizes the reminder with the backend.
3. At due time, patient can take/complete, snooze, or skip.
4. Action synchronizes to the backend and updates the patient experience.
5. If configured and authorized, a missed-reminder rule generates a caregiver alert.

**Success:** duplicate notifications and duplicate completion actions are prevented, including after restart or token refresh.

### 9.5 Connect a caregiver

1. Patient enters caregiver details, chooses permissions, and sends an invitation.
2. Caregiver opens a valid app link or enters the invitation token.
3. Caregiver authenticates with the intended role.
4. App clearly presents the patient, relationship, permissions, and consent.
5. Caregiver accepts.
6. Both users see the active relationship.
7. Patient can change permissions or revoke access.

**Success:** revoked caregivers immediately lose backend access and disappear from active care-team views.

### 9.6 Caregiver monitors a patient

1. Caregiver opens their connected-patient list.
2. Caregiver selects a patient.
3. App shows authorized visits, reminders, and alerts.
4. Caregiver marks alerts read or removes them from their own view.
5. App clearly identifies delayed, processing, or unavailable information.

**Success:** the caregiver cannot retrieve data for an unconnected patient or data outside the granted permission level.

## 10. Functional requirements

### 10.1 Identity, authentication, and roles

- **AUTH-01 (P0):** Support patient and caregiver roles.
- **AUTH-02 (P0):** Support email/password authentication on both platforms.
- **AUTH-03 (P0):** Support Google sign-in where configured.
- **AUTH-04 (P0):** Support Sign in with Apple on iOS when other third-party sign-in is offered.
- **AUTH-05 (P0):** Store session credentials in platform-secure storage.
- **AUTH-06 (P0):** Route users only to the experience authorized by the backend profile.
- **AUTH-07 (P0):** Prevent silent role switching or role mismatch.
- **AUTH-08 (P0):** Support password recovery and explicit logout.
- **AUTH-09 (P0):** Require reauthentication where Firebase requires it for destructive account actions.

Acceptance criteria:

- Invalid, expired, or revoked credentials return the user to a recoverable sign-in state.
- A client-modified role cannot grant access to another role's protected API data.
- Login errors use actionable language without exposing account-existence or backend details.

### 10.2 Patient home and overview

- **HOME-01 (P0):** Show today's reminders and tasks in chronological order.
- **HOME-02 (P0):** Show the next relevant reminder and daily completion progress.
- **HOME-03 (P0):** Link every displayed task to a working completion or detail action.
- **HOME-04 (P0):** Show visit summaries, scanned documents, and lab-document entries in the overview.
- **HOME-05 (P0):** Distinguish pending, processing, completed, and failed content.
- **HOME-06 (P1):** Provide useful empty states and onboarding prompts based on uncompleted core actions.

### 10.3 Visit recording and summaries

- **VISIT-01 (P0):** Require recording consent for every recording session.
- **VISIT-02 (P0):** Request microphone permission contextually.
- **VISIT-03 (P0):** Display active recording, stop availability, elapsed time, and upload state.
- **VISIT-04 (P0):** Preserve one stable visit identifier across capture and processing.
- **VISIT-05 (P0):** Process audio asynchronously and expose status.
- **VISIT-06 (P0):** Produce a plain-language summary that separates discussed topics, instructions, medications, and follow-up actions when present.
- **VISIT-07 (P0):** Label AI-generated output and instruct users to verify it with their clinician.
- **VISIT-08 (P0):** Require confirmation before activating extracted reminders or tasks.
- **VISIT-09 (P0):** Allow deletion of a visit summary and associated media subject to the published retention policy.
- **VISIT-10 (P1):** Provide secure playback of an authorized recording.

Acceptance criteria:

- The app does not imply that a recording is safely uploaded before the backend acknowledges it.
- A worker or processing failure cannot leave a visit indefinitely marked as processing without escalation or retry.
- Caregivers cannot access a recording or summary without active permission.

The current backend creates tasks and reminders during summary processing before patient confirmation. That behavior does not satisfy VISIT-08 and must be gated or changed before MVP release.

### 10.4 RemiScan

- **SCAN-01 (P0):** Support capture of prescriptions, medication labels, and lab documents.
- **SCAN-02 (P0):** Require consent and camera permission before capture.
- **SCAN-03 (P0):** Let the user retake or cancel before upload.
- **SCAN-04 (P0):** Perform server-backed OCR and return explicit processing status.
- **SCAN-05 (P0):** Never present generated mock data as document extraction.
- **SCAN-06 (P0):** Label extracted fields as unverified until patient confirmation.
- **SCAN-07 (P0):** Allow correction of medication name, dosage, frequency, timing, and other extracted fields.
- **SCAN-08 (P0):** Require confirmation before creating reminders or tasks.
- **SCAN-09 (P0):** Avoid diagnostic interpretation of lab results.
- **SCAN-10 (P1):** Generate a plain-language, non-diagnostic document summary.

Acceptance criteria:

- Unsupported or unreadable documents produce a clear failure state rather than invented values.
- The saved record identifies source type, capture date, processing state, and user-confirmed fields.

The current scan screen can show success styling and mock values before verified backend extraction, while the client save path references a backend route that is not active. Mock and hard-coded health values must be removed from release code.

### 10.5 Reminders and notifications

- **REM-01 (P0):** Create, view, edit, and delete reminders.
- **REM-02 (P0):** Support medication and general care reminder types.
- **REM-03 (P0):** Support one-time and recurring schedules needed by the user interface.
- **REM-04 (P0):** Support complete/taken, snooze, and skip actions.
- **REM-05 (P0):** Synchronize notification actions with the backend.
- **REM-06 (P0):** Reconcile local notification schedules after login, edit, deletion, restart, and timezone change.
- **REM-07 (P0):** Prevent duplicate due notifications and duplicate caregiver alerts.
- **REM-08 (P0):** Explain when notification or exact-alarm permissions are disabled and provide a route to settings.
- **REM-09 (P1):** Let users configure notification preferences and send a test notification.

### 10.6 Care team, sharing, and caregiver experience

- **CARE-01 (P0):** Patients can invite a caregiver by a supported contact method.
- **CARE-02 (P0):** Invitations expire, are single-purpose, and cannot be accepted by an unintended role without correction.
- **CARE-03 (P0):** Patients choose documented permission levels before sending an invitation.
- **CARE-04 (P0):** Caregivers see the patient identity and permissions before accepting.
- **CARE-05 (P0):** Patients can review, modify, and revoke caregiver access.
- **CARE-06 (P0):** Backend authorization is the source of truth for every caregiver data request.
- **CARE-07 (P0):** Caregivers can view connected patients, authorized visits, reminders, and alerts.
- **CARE-08 (P0):** Caregivers can mark alerts read and delete their alert entries.
- **CARE-09 (P0):** Sensitive caregiver access is auditable.
- **CARE-10 (P1):** Caregivers can maintain private or appropriately shared notes with clear visibility labels.

### 10.7 RemiVox

- **VOX-01 (P1):** Accept text or speech questions about the patient's authorized RemiMinder data.
- **VOX-02 (P1):** Limit responses to supported reminders, tasks, and summaries.
- **VOX-03 (P1):** State when the answer is unavailable rather than infer medical advice.
- **VOX-04 (P1):** Apply the same authorization and privacy rules as visual screens.
- **VOX-05 (P1):** Make synthesized audio optional and controllable.

The active mobile experience currently provides a read-aloud briefing. VOX-01 interactive questions are a target requirement, not current mobile behavior.

### 10.8 Profile, subscription, and account lifecycle

- **ACCOUNT-01 (P0):** Let users view and update supported profile fields and language.
- **ACCOUNT-02 (P0):** Let users inspect notification and privacy settings.
- **ACCOUNT-03 (P0):** Provide permanent account deletion with explicit confirmation.
- **ACCOUNT-04 (P0):** Delete or de-identify Cloud SQL, object storage, Firestore, and authentication data according to the published policy.
- **ACCOUNT-05 (P0):** Expose a support path for data export and partial deletion requests.
- **SUB-01 (P1):** Show current plan, trial state, entitlements, and usage limits consistently.
- **SUB-02 (P1):** Enforce limits on the backend, not only in the client.
- **SUB-03 (P1):** Use platform-compliant purchase and account-management flows.

## 11. AI requirements and safety

- AI content must be labeled as AI-generated.
- AI content must include a concise medical disclaimer.
- The system must preserve the source relationship among recording/document, transcript/OCR, summary, and confirmed actions.
- Extracted actions must remain proposals until a user confirms them.
- The system must not fabricate missing medication, dosage, frequency, date, or lab values.
- Low-confidence or structurally invalid output must trigger review or failure handling.
- Prompts and model versions must be versioned for reproducibility.
- Model output must be validated against an explicit schema before persistence.
- Users must be able to report inaccurate output.
- AI usage must respect plan limits without losing already captured user media.
- Raw health content must not be used for advertising.
- Any use of customer data for model improvement requires separate, explicit policy and consent; it is not authorized by this PRD.

## 12. Platform requirements

### 12.1 Shared behavior

The same Flutter product should provide equivalent core journeys, data, terminology, accessibility, and authorization on iOS and Android. Platform parity means equivalent outcomes, not pixel-identical system UI.

### 12.2 iOS-specific

- Declare microphone, camera, speech, notification, and photo access descriptions as applicable.
- Support Apple authentication when required by App Store policy.
- Register and test the production invitation and billing-return link schemes.
- Use iOS notification categories for complete/taken and snooze actions.
- Handle audio-session interruption from calls, Siri, and other audio.
- Use Apple-compliant subscription purchase and management flows for digital features.
- Provide privacy-manifest and App Privacy responses matching actual SDK behavior.

### 12.3 Android-specific

- Request camera, microphone, notifications, and exact-alarm access contextually.
- Support boot rescheduling for valid local reminders.
- Use a consistent production application identifier and deep-link scheme.
- Keep notification channel identifiers consistent between manifest, FCM, and local notification code.
- Meet Google Play health-app, data-safety, account-deletion, and payments requirements.
- Verify behavior under battery optimization and manufacturer background restrictions.

### 12.4 Known parity issues to resolve before MVP

1. iOS invitation links are handled in Dart but the expected scheme is not fully registered.
2. iOS and Android currently use different application identifier conventions.
3. Android notification channel metadata and runtime channel identifiers differ.
4. Billing-return behavior and entitlement handling are not yet one verified cross-platform flow.
5. Release automation currently emphasizes Android; iOS release validation must be defined.

## 13. Accessibility and localization

- Meet WCAG 2.2 AA principles where applicable to mobile UI.
- Support dynamic text without clipping core actions.
- Provide semantic labels and logical focus order for screen readers.
- Do not rely on color alone for processing, warning, or completion state.
- Maintain adequate touch targets and contrast.
- Caption or provide text alternatives for audio-derived content.
- Localize permissions rationale, consent, disclaimers, errors, reminders, and invitation flows.
- Format dates, times, recurrence, and numbers according to locale.
- Require linguistic and medical-safety review before claiming full support for any language.

The repository contains resources for ten languages, but resource presence alone does not establish complete localization quality.

## 14. Privacy, security, and compliance

### 14.1 Required controls

- Encrypt all network traffic with HTTPS.
- Store authentication secrets only in platform-secure storage.
- Enforce Firebase token validation and backend authorization.
- Apply least-privilege patient/caregiver access for every request.
- Log sensitive caregiver access without placing health content in application logs.
- Encrypt managed storage using cloud-provider controls and document key ownership.
- Use short-lived, authorized access for private media.
- Define retention for recordings, images, transcripts, OCR, summaries, logs, and deleted accounts.
- Revoke access immediately when a care relationship ends.
- Protect internal job routes with rotated secrets or service identity.
- Rate-limit authentication-sensitive, invitation, AI, and media endpoints.
- Redact tokens and health information from crash reports, analytics, and logs.
- Maintain tested backup and restoration procedures.
- Maintain incident-response and privacy-request procedures.

### 14.2 Compliance boundary

The product may be designed toward HIPAA-ready controls, but the repository alone does not establish HIPAA compliance. A production claim requires documented administrative and technical safeguards, vendor agreements where applicable, risk assessment, access review, incident procedures, and validated deployment configuration.

RemiMinderAI must continue to state that it is not a medical device and not a substitute for professional medical advice unless legal and regulatory review changes that position.

## 15. Data and system requirements

### 15.1 Systems of record

| Data | Required source of truth |
|---|---|
| Identity and session | Firebase Authentication plus backend user profile |
| Visits, summaries, tasks, reminders, alerts | Cloud SQL through the FastAPI API |
| Audio and images | Private Google Cloud Storage objects referenced by Cloud SQL |
| Care-team authorization | Backend care-team records |
| Local reminders | Device schedule reconciled from backend reminder records |
| Entitlements | Backend-verified subscription state synchronized with the store provider |

This table defines the target system of record, not the current implementation. Today, mobile membership, invitations, connected-patient lists, some scanned documents, consent records, and notification-token behavior also use Firestore. Invitation creation may mirror Cloud SQL to Firestore, while acceptance, updates, or removal do not consistently update both. These flows must either be formally retained with documented consistency rules or migrated behind the backend API. Parallel stores must not independently grant authorization.

### 15.2 Processing

- Media processing is asynchronous and idempotent.
- Every job has queued, running, succeeded, retryable-failed, and terminal-failed states.
- Retries do not duplicate summaries, reminders, tasks, or alerts.
- Users can retrieve processing status after closing and reopening the app.
- Operational alerts identify stuck queues and elevated failure rates.

### 15.3 API contracts

- Mobile and backend paths, request schemas, and response schemas must be versioned or tested together.
- Missing client-referenced scan and billing endpoints must be implemented or removed from release flows.
- Shared endpoint constants must match the live API or be retired.
- Error responses must include a stable machine code and safe user-facing message.

## 16. Non-functional requirements

Final service-level targets require production baselines and load testing.

| Area | MVP requirement |
|---|---|
| Availability | Core APIs and background processing have defined monitoring and on-call ownership |
| Performance | Home and reminder views remain responsive on supported mid-range devices; network loading never blocks navigation indefinitely |
| Reliability | Core writes are idempotent and recover after transient network loss |
| Scalability | API and workers scale independently; queue depth is observable |
| Data integrity | No user can access another user's data without an active, authorized relationship |
| Compatibility | Define and test a supported iOS/Android OS matrix before store submission |
| Maintainability | CI runs formatting, static analysis, unit tests, and core integration tests |
| Observability | Structured logs, metrics, and traces exclude sensitive content by default |

## 17. Analytics and observability

### 17.1 Permitted product events

- Onboarding step completed
- Role selected
- Permission requested and granted/denied
- Reminder created, edited, due, completed, snoozed, or skipped
- Recording started, upload completed, processing succeeded/failed
- Scan submitted, processing succeeded/failed
- Invitation sent, accepted, expired, or revoked
- Subscription screen viewed and entitlement synchronized

Events use opaque internal identifiers and coarse categories. They must not contain names, email addresses, transcript text, OCR text, medication details, clinician details, or document images.

### 17.2 Operational monitoring

Monitor:

- API latency and error rate by safe route template
- Authentication and authorization failures
- Media upload failures
- Job queue depth, age, retries, and terminal failures
- Notification dispatch and action-sync failures
- Invitation acceptance failures
- Account-deletion completion across all stores

## 18. MVP release criteria

MVP is ready only when:

1. All P0 requirements have passed acceptance testing on supported iOS and Android devices.
2. Scan results come from a real supported backend flow; no mock extraction is user-visible.
3. AI-proposed reminders and tasks require user confirmation.
4. Role and caregiver permissions are enforced by backend tests.
5. Recording, scan, reminder, invitation, revocation, and deletion failure paths are tested.
6. iOS and Android deep links are verified from real email invitation links.
7. Notification actions and rescheduling are verified across restart and timezone change.
8. Privacy policy, store disclosures, permission text, and actual data behavior agree.
9. Account deletion is verified across Cloud SQL, object storage, Firestore, and Firebase Auth.
10. Monitoring can identify stuck processing jobs.
11. No unresolved release-blocking security findings remain.
12. Medical, privacy, and AI disclaimers have stakeholder approval.

## 19. Rollout approach

### Stage 1: Internal validation

- Seeded test accounts and synthetic health data only.
- Verify capture pipelines, notification reliability, access control, and deletion.
- Compare iOS and Android acceptance results.

### Stage 2: Limited beta

- Small invited cohort.
- Conservative AI limits and active processing monitoring.
- In-product feedback for inaccurate summaries and failed scans.
- Weekly review of reliability, privacy, and support signals.

### Stage 3: General availability

- Store listings and disclosures approved.
- Support and incident processes staffed.
- Capacity and cost controls validated.
- Feature flags allow risky AI or scan behavior to be disabled without an app update.

Remote operational kill switches for AI and scan behavior are a release target and are not present in the current implementation.

## 20. Risks and mitigations

| Risk | Mitigation |
|---|---|
| AI output is incomplete or incorrect | Schema validation, source traceability, disclaimers, editable review, explicit confirmation |
| Missed or duplicate medication notifications | Backend reconciliation, idempotency, device testing, visible permission health |
| Caregiver sees unauthorized information | Backend-enforced access, revocation tests, audit logs, minimum permissions |
| Recording consent is unclear | Per-session consent, persistent indicator, cancel/delete controls |
| Scan presents invented information | Remove mock results, confidence-aware extraction, fail closed |
| Parallel Cloud SQL and Firestore state diverges | Define one authorization source and consistency/migration plan |
| Worker outage stalls summaries | Queue-age monitoring, retries, cron fallback, terminal failure UX |
| Platform behavior diverges | Shared acceptance suite plus iOS/Android-specific release checklist |
| Compliance is overstated | Legal review and evidence-based claims only |
| Subscription flow violates store policy | Platform-compliant purchase design and server-side entitlement validation |
| Analytics disclosures conflict with implementation | Reconcile Firebase Analytics events with App Store and Play disclosures before release |

## 21. Open product decisions

Stakeholders must resolve:

1. Which features and usage limits belong to free, trial, and paid plans?
2. Are original visit recordings retained after transcription, and for how long?
3. Can caregivers hear original audio, or only view summaries by default?
4. What exact permissions distinguish `view` from `full` caregiver access?
5. Should patients approve every AI-generated task individually or approve a batch?
6. Which scan document fields are supported at MVP?
7. Which languages are launch-quality versus experimental?
8. Will invitations use universal/app links, custom schemes, or both?
9. Is Firestore a permanent product data dependency or a migration bridge?
10. Which iOS and Android versions and device classes are supported?
11. What are the approved processing-time and availability targets?
12. What retention, export, and partial-deletion policies will be published?

## 22. Later roadmap

Subject to validated user demand and safety review:

- Adherence trends and caregiver-configurable escalation.
- Clinician-approved care-plan templates.
- High-quality multilingual speech and summaries.
- Secure export of a patient-selected visit package.
- Offline drafting and resilient upload queues.
- Provider and standards-based health-data integrations.
- Enhanced RemiVox accessibility.
- Non-diagnostic longitudinal insights grounded in confirmed patient data.

## 23. Repository evidence and known implementation gaps

This PRD was derived from the current repository, including:

- Product overview: `README.md`
- Intended architecture: `ARCHITECTURE.md`
- Flutter routing and screens: `apps/mobile/lib/router/app_router.dart` and `apps/mobile/lib/features/`
- Mobile services: `apps/mobile/lib/core/services/`
- FastAPI application and routes: `apps/backend/main.py` and `apps/backend/route/`
- AI and media processing: `apps/backend/services/` and `apps/backend/workers/`
- Database schema: `apps/backend/schemas/migrations/`
- Android disclosure checklist: `apps/mobile/PLAY_CONSOLE_DATA_SAFETY.md`

Important gaps the delivery plan must account for:

- The current scan screen can show “Scan Successful” and mock or hard-coded health values before verified extraction; backend OCR integration is incomplete.
- Client-referenced `/api/scan-document` and billing checkout behavior do not have matching active backend routes.
- Medication-specific API behavior is incomplete; much of the current experience models medication as a reminder.
- The AI summary pipeline currently persists generated tasks and reminders without the patient-confirmation gate required by this PRD.
- Visit recording playback is unfinished.
- Full offline-first PowerSync behavior is described in architecture documentation but not implemented.
- Reminder behavior exists in both REST/Cloud SQL and legacy Firestore code.
- Role routing currently prioritizes client-held role state rather than a backend-authorized profile.
- Care-team membership, invitation, update, and removal operations are not consistently synchronized between Cloud SQL and Firestore.
- The mobile RemiVox experience provides a briefing but does not expose the backend's interactive question endpoint.
- Some patient screens described in documentation, including legacy history and visit-list screens, exist but are not routed in the active application.
- End-to-end and authorization test coverage is limited.
- Infrastructure documentation contains aspirational components not present as deployable configuration.
- Firebase Analytics is present while the Android data-safety checklist says app interactions are not collected; disclosures must be reconciled.

These gaps do not change the product vision; they define the work needed to satisfy the MVP requirements above.

The active React application under `apps/web/` and the archived Phase 1 implementation under `phase1/` are outside the mobile MVP defined by this PRD. The web invitation fallback remains an integration dependency until mobile links are the verified default.

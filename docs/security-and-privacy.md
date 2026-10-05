# Security and privacy

The platform holds identifiable data about children. This document describes the controls as built. It also marks the points that need a decision from the school, its DPO or its counsel.

> **Not legal advice.** The consent model is built to the stricter reading of both laws below. Have counsel confirm it, and re-check the PDPL executive regulations, before any real student data is loaded (spec §8.1).

## Regulatory position (spec §8.1)

| Instrument | What the platform does about it |
|---|---|
| **UAE PDPL**, Federal Decree-Law No. 45 of 2021 | The school is the controller and the platform operator is a processor, under a written DPA. Children's data is treated as sensitive: field-level encryption, access audit, purpose-bound consent records, a subject-access export and a retention schedule. |
| **Child Digital Safety law**, Federal Decree-Law No. 26 of 2025 (fully enforceable January 2027) | Data-processing consent for under-13s defaults to *not given* until a guardian records it, and the record is withdrawable. There is no behavioural profiling or advertising. The engine works only from demonstrated skills and stated requirements, and never infers personality, temperament or character. |
| **KHDA / ADEK expectations** | The inspection-evidence report maps skill domains onto inspection strands, which the school configures. |

## Tenant isolation

Isolation is enforced by PostgreSQL, not only by application code:

- Every table with school data has a `tenant_id`, `ENABLE` and **`FORCE ROW LEVEL SECURITY`**, and a policy `tenant_id = app_current_tenant()`.
- `app_current_tenant()` reads the transaction-local setting `app.tenant_id`. The API sets it from the authenticated token at the start of every transaction. Without it, a query returns nothing, and inserting a row for another tenant fails.
- Three views run with the caller's rights (`security_invoker`), so they cannot leak across tenants: `student_media_consent`, `media_renderable` and `publishable_students`.

### Database roles

| Role | Used by | Rights |
|---|---|---|
| `stem_owner` | Migrations, tenant provisioning | Owns the schema. Still bound by RLS, because the tables force it. |
| `stem_app` | API and worker | `NOBYPASSRLS`; DML on tables, except `UPDATE`/`DELETE` on `audit_events`. It cannot create tenants. |
| `stem_backup` | `scripts/backup.sh` only | Read-only (`pg_read_all_data`) with `BYPASSRLS`, because a backup must contain every tenant. Keep its credentials only on the backup host. |

`tests/test_rls.py` checks this at the database level:
- every tenant table has forced RLS and a policy;
- the runtime role cannot bypass RLS, even with raw SQL;
- it cannot write into another tenant;
- the audit log is append-only.

The restore drill re-checks the same properties on every restored backup.

## Authentication

| Who | How |
|---|---|
| Staff | The school's identity provider (OIDC: Entra ID or Google), or a password hashed with Argon2id |
| Programme admins and leaders | **MFA required.** TOTP for password sign-in. SSO sign-in counts as MFA only if the IdP's `amr` claim says so (the values are configurable). A session without a second factor has no admin or leader capabilities. |
| Parents | Single-use magic link (15 minutes) sent to the address on record, or a password |
| Students (Year 9+) | Staff-provisioned account. The portal shows the student's own record; they can add self-assessments, which count only after a teacher countersigns them. |

Sessions:
- **Access tokens** last 15 minutes and are held only in memory.
- **Refresh tokens:**
  - stored in an httpOnly, `Secure`, `SameSite=Strict` cookie scoped to the auth path;
  - rotated on every use;
  - reusing an old one revokes the whole family;
  - 12 hours idle, which is long enough for a competition day, and 7 days absolute.
- **Rate limits:** 10 attempts per minute per IP on each sign-in endpoint, and 600 requests per minute per IP elsewhere.

## Authorisation

Every endpoint is checked on the server in two steps:

1. **Capability**: does the role allow this action at all? If not, the response is 403.
2. **Scope**: is this record inside what the user may see? If not, the response is **404**, so IDs cannot be probed by changing a URL.
   - Teachers: their squads.
   - Parents: their own children.
   - Students: themselves.
   - Admins and leaders: the school.

A user can hold several roles (a teacher who is also a parent). Each role's scope applies only to that role's capabilities.

| Capability | Teacher | Programme admin | Leader | Parent | Student |
|---|:-:|:-:|:-:|:-:|:-:|
| View roster, run readiness, generate reports | ✓ own squads | ✓ | ✓ | | |
| Record attendance and results, verify skills, upload media | ✓ own squads | ✓ | | | |
| Propose a competition | ✓ | ✓ | | | |
| Manage competitions, squads, imports, inventory, consent records | | ✓ | | | |
| Draft parent messages | ✓ | ✓ | ✓ | | |
| **Release** parent messages, approve templates | | ✓ | ✓ | | |
| Emergency broadcast | | | ✓ | | |
| View budget | | ✓ | ✓ | | |
| Manage budget lines / **approve spend** | | manage | approve | | |
| School analytics, inspection export, subject-access export, webhooks, settings | | ✓ | ✓ | | |
| Manage users, view the full audit log | | | ✓ | | |
| Parent portal | | | | ✓ own children | |
| Student portal | | | | | ✓ self |

The matrix lives in `backend/app/permissions.py`, and `tests/test_authorization.py` asserts it. That suite tries to:
- reach records out of scope by ID;
- write to other squads;
- use forged and expired tokens;
- reach the parent portal outside the parent's own children.

## Audit

- **What is recorded.** Every write, and **every read of a student record** (spec §8.2), appends a row to `audit_events` in the same transaction. Each row records the user, role, action, entity, request ID, IP and user agent.
- **Append-only.** `stem_app` has no `UPDATE` or `DELETE` on the table. Purging goes only through the `purge_audit_events()` database function, which refuses anything younger than a year. The default retention is seven years.
- **Messages.** Message audit is complete: who drafted, who approved and released, the template version, the channel, delivery status, and whether the message was opened.
- **Who can view it.** Leaders see the whole audit log; admins see their own actions.

## Encryption

| Data | Protection |
|---|---|
| All traffic | TLS, terminated at the load balancer in front of `web`. The web app sends a strict CSP, `X-Frame-Options: DENY` and a strict referrer policy. |
| Database, object storage, backups | The provider's encryption at rest. Logical backups are additionally encrypted with `age` to public keys held offline (see [operations](operations.md#backups-and-the-restore-drill)). |
| Guardian email, phone and WhatsApp numbers; MFA secrets; webhook and OIDC client secrets | Field-level encryption (Fernet: AES-128-CBC with HMAC-SHA256), with a key from the secret manager. |
| Guardian email lookup | A keyed HMAC blind index, so sign-in and imports can match an email without decrypting the column |

Both keys can be rotated without downtime ([operations](operations.md#rotating-keys)).

## Consent

Consent is a record, not a flag (spec §8.2). Each record holds:
- the purpose and the form version;
- the decision, and who gave it, when and how;
- an optional edition;
- a withdrawal timestamp.

The current state is the most recent decision. Withdrawing takes effect at the next check, which happens at dispatch and at render, never only at the time of drafting.

| Purpose | Default with no record | Why |
|---|---|---|
| `media` | **Not given** | Opt-in; enforced in the data layer (below) |
| `communications` | Given, until a guardian declines or withdraws | Programme messaging is part of enrolment. Withdrawal blocks the next send automatically (acceptance criterion 13.1). |
| `travel`, `fee_authorisation` | **Not given** | Must be requested per edition |
| `data_processing` | **Not given for under-13s**; covered by the enrolment contract for 13+ | Child Digital Safety law: verifiable parental consent for under-13s |

**For counsel:** the 13+ default relies on the school's enrolment contract as the lawful basis. Confirm it. If counsel disagrees, record `data_processing` consent for all ages; the code then needs no change.

Opt-outs are separate from consent. Each message category has a one-tap opt-out link (a signed token, so no sign-in is needed), and it is honoured immediately.

## Media consent

Spec §5.7 says: "A student without consent is never shown in a generated report, a newsletter export, or a parent message to another family. This check must be in the data layer, not the UI." It is implemented like this:

- **Upload.** Tagging a student without media consent in a photo or video is refused.
- **Render.** Media is served only through the `media_renderable` view, which joins current consent. Withdrawing consent removes the media everywhere at once, including files already tagged.
- **Generated reports and exports.** These read student identity through `publishable_students`. A student without media consent is **pseudonymised** ("Student A"). They still count in totals, because removing them would make aggregates wrong and could itself reveal who they are by subtraction.
  - The one exception is the **student profile report**: it is that student's own record, given to their own family, and contains no media.
- **Parent messages.** A result message names team-mates from other families only if they have media consent. The others are counted ("and 1 other"), not named (`test_messaging.py::test_team_mates_without_media_consent_are_counted_not_named`).

**For counsel and the school:** pseudonymising, rather than omitting, is our interpretation of "never shown". Confirm it. Omission is a small change to the report builders, but it changes totals.

## Messaging safeguards (spec §7.3)

- **Human release.** Nothing sends without it. Release needs the release capability, a preview done first, and an approved template version. The approver's name is stored on the message and in the audit log.
- **Preview** shows exactly what three real families will receive.
- **Quiet hours** (default 20:00–07:00 in the school's time zone) and a **weekly cap** per family (default 3, rolling seven days) are re-checked at the moment of sending.
- **Negative news is never sent automatically.** Attendance concerns, non-selection and behaviour notes are fixed in code to a hand-off path, where a person sends them.
- **Siblings** are combined into one message per family.
- **Emergency broadcast** (leader only, with a stated reason) bypasses quiet hours and the cap. Nothing else.
- **Provider status webhooks** are authenticated: a WhatsApp signature, or Postmark credentials. Production refuses to start without the secret for each provider in use.

## Retention

The schedule is configured per school (`retention` in tenant settings) and applied nightly by the worker.

| Record | Default | What happens |
|---|---|---|
| Audit events | 84 months (7 years); never less than 12 | Deleted through `purge_audit_events()` |
| Message deliveries (what each family received) | 36 months | Deleted |
| Email/SMS/WhatsApp outbox | 3 months | Deleted |
| Generated report files | 3 months | Files and records deleted |
| Import batches (uploaded CSVs and diffs) | 12 months | Deleted |
| **Leaver's media** | 12 months after leaving | Files and records deleted |
| **Leaver's record** | 36 months after leaving | **Anonymised**: names, date of birth, house, MIS ID, login and guardian links removed. Results and award counts stay, so past season figures don't change. |

A student is marked as a leaver only when an MIS import is run with "mark leavers" ticked. A missing row in an import never removes anyone by accident.

## Subject-access export

An admin or leader can generate a complete bundle for one student in seconds. The acceptance test requires under 60 seconds. It is a ZIP containing an HTML summary and one JSON file per record type:
- the student and their guardians (contact details decrypted);
- squad memberships, attendance, results;
- skill awards (with evidence) and goals;
- flags and insights;
- consents and consent requests;
- messages received by the family;
- media they are tagged in;
- the **access log** of who has viewed the record.

Credentials and internal keys are excluded.

## Delivery pipeline

CI runs on every change and daily. Deploy is blocked unless every job passes, including:

- **secrets in git history**: gitleaks;
- **Python dependency vulnerabilities**: pip-audit;
- **Python static analysis**: bandit, medium severity and up;
- **JavaScript dependency vulnerabilities**: npm audit, high and up;
- **container images**: Trivy, failing on any fixable HIGH or CRITICAL.

The images apply OS security updates at build time, and the API image runs as a non-root user.

## Before go-live

The software is ready for these checks. Its own tests are not a substitute for them:

1. An **independent penetration test** (spec §8.3), before the first real record.
2. **Legal sign-off** on the consent defaults and the media interpretation above, and a DPA with each sub-processor (hosting, email, SMS, WhatsApp).
3. **Data residency.** Every store must be in a UAE region: database, Redis, object storage, backups, logs, and Sentry if used.
4. **Synthetic data only** in staging and demos (spec §10.3). The demo seeder generates fictional people.

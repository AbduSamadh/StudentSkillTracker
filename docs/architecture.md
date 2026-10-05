# Architecture

## Components

```
 phone / laptop ── HTTPS ──▶ web (nginx: PWA static files, CSP, /api proxy)
                                     │
                                     ▼
                             api (FastAPI, uvicorn) ──▶ PostgreSQL 16 (row-level security)
                                     │        │
                                     │        └──────▶ object storage (S3-compatible: media, report files)
                                     ▼
                              Redis ◀── worker (ARQ: dispatch, reports, nightly jobs)
                                     │
                                     └──▶ email / SMS / WhatsApp Cloud API, outbound webhooks
```

- **api** (`backend/app`) serves REST under `/api/v1`, with OpenAPI at `/api/v1/openapi.json` and docs at `/api/docs`. It holds no state between requests.
- **worker** runs the same code with `arq app.worker.WorkerSettings`:
  - queued jobs: message dispatch, report rendering, imports;
  - every minute: due scheduled sends and outbox retries;
  - nightly at 01:30 UTC: flags and the retention purge;
  - weekly: draft insights.
  - With `STEM_USE_JOB_QUEUE=false` (development, tests), jobs run in-process after the request commits.
- **web** (`frontend/`) is a static PWA. API data is cached by TanStack Query in IndexedDB, and the service worker caches the app shell. Authenticated responses never go into an HTTP cache.

## A request, end to end

1. **Authentication.** A 15-minute JWT access token, held in memory by the PWA. When it expires, the PWA calls `/auth/refresh` with an httpOnly, `SameSite=Strict` cookie scoped to `/api/v1/auth`, plus a custom header that a cross-site form cannot send. Refresh tokens rotate on every use; reusing an old one revokes the whole family.
2. **Context.** `app/deps.py` builds a `Ctx`: the principal (user, roles with their scopes, capabilities) and a database session bound to the token's tenant.
3. **Tenancy.** On every transaction, the session runs `set_config('app.tenant_id', …, true)`. Every tenant table has `FORCE ROW LEVEL SECURITY` with a policy on `app_current_tenant()`, so a query that forgets to filter still cannot see another school. The API connects as `stem_app`, which cannot bypass RLS.
4. **Authorisation.** Each endpoint checks a capability from the matrix in `app/permissions.py`, then scope:
   - a teacher reaches only students in their squads;
   - a parent only their own children;
   - a student only themselves.
   A missing capability returns 403. A record outside the user's scope returns **404**, so IDs cannot be probed.
5. **Audit.** Writes, and every read of a student record, append to `audit_events` in the same transaction.
6. **Commit, then side effects.** The transaction commits before the response is sent. Work the endpoint deferred (sends, webhooks, report rendering) only starts after commit, so a rolled-back request never sends a message.

## Data model

There are 18 core entities (spec §3), all carrying `tenant_id`:

| Area | Entities |
|---|---|
| Tenancy and identity | `tenants`, `users`, `role_assignments` (role + scope), `refresh_tokens`, `audit_events` |
| People | `students`, `guardians` (contact fields encrypted), `student_guardians` |
| Catalogue | `competitions`, `competition_editions` (one per season), `exam_windows`, `seasons` |
| Skills | `skills` (the taxonomy), `skill_requirements`, `skill_awards`, `skill_goals` |
| Squads and capture | `squads`, `squad_memberships`, `squad_target_editions`, `training_sessions`, `attendance`, `results`, `result_participants` |
| Parents | `consents`, `consent_requests`, `message_templates` (versioned), `messages`, `message_deliveries`, `communication_opt_outs`, `outbox_entries` |
| Operations | `assets`, `asset_loans`, `budget_lines`, `media_assets`, `media_subjects`, `report_jobs`, `import_batches`, `webhook_subscriptions`, `webhook_deliveries`, `student_flags`, `insights` |

The three skill relations stay separate, as the spec requires: *requirements* (competition or edition needs skill X at level L), *awards* (student demonstrated X at L, with evidence and verifier) and *goals* (student is working towards X at L).

## Skills engine (`app/services/readiness.py`, `recommendations.py`, `flags.py`)

- **Earning a skill.** Each award has a source with a confidence:
  - rubric score: high
  - teacher verification: high
  - artefact review: medium
  - self-assessment: low
  Rubric scores and self-assessments arrive as *proposed* and count for nothing until a named teacher confirms them. Teachers can confirm in bulk from the results screen.
- **Requirements.** Requirements are set on the competition. An edition inherits them and can override, add or remove any of them.
- **Readiness** is `Σ w·min(L_earned / L_required, 1) / Σ w` over the effective requirements. It counts only verified, unrevoked awards.
  - Every line of the result carries the requirement ID and the award behind it (verifier, date, evidence), so the UI can show exactly why a score is what it is.
  - An edition with no requirements has *no* score, not zero.
- **Gap lists.**
  - Per student: each requirement not met, and by how many levels.
  - Per squad: the *shared* gaps, ranked by how many students closing the gap would unblock. Training-session focus suggestions come from this, each with its reason.
- **Recommendations** list every check behind each one: eligibility, readiness against the threshold (default 65%), registration open, budget, no exam clash. "Not recommended" items say which check failed.
- **Flags** (stretch, plateau, attendance) record the rule and the facts that triggered them. They are about demonstrated skills and participation only. The engine never infers personality or character (spec §8.1).

## Results and the performance index (`app/services/performance.py`)

`Index = 100 · (0.72 · (F − P)/(F − 1) + 0.28 · min(log10 F / log10 300, 1)) · T`

- `P` is placement, `F` is field size, and `T` is the tier weight, configurable per school.
- If the field size is missing, the result has no index and gets a data-quality flag. A default is never substituted.

## Offline capture (`frontend/src/lib/offline`, `backend/app/api/v1/capture.py`)

- Attendance, results and skill tagging write to an IndexedDB outbox *before* any network attempt. The outbox flushes in order when the device reconnects, regains focus, or every 30 seconds.
- `createdAt` is strictly increasing per device, so even writes made in the same millisecond replay in the order they were made.
- If a write fails because of the network or a server error, the flush stops there. Later writes never overtake an earlier one.
- If the server rejects a write (a validation error, or a conflict such as editing a result already released to parents), the write is parked for review rather than retried forever.
- On the server, every capture POST carries a client idempotency key. A replay returns the canonical record, marked `Idempotent-Replay: true`, and never creates a duplicate.
- Concurrent edits resolve last-write-wins on the client's `client_modified_at`.
- Signing out with unsynced writes asks first. It then clears cached student data from the device.

## Parent messaging (`app/services/messaging/`)

```
draft ──preview (3 real recipients)──▶ release by a named approver ──▶ per-family deliveries ──▶ dispatch
                                                                          │ checked at send time:
                                                                          │ consent, opt-out, quiet hours,
                                                                          │ weekly cap, approved template
```

- **Templates** are versioned. Only an approved version can be released. They render with Jinja2's sandbox and `StrictUndefined`, so a missing variable is an error, never a blank.
- **Preview** is required before release. It shows exactly what three real families would receive, and which safeguards would hold or block each one.
- **Negative messages** (attendance concern, non-selection, behaviour note) are hard-coded to a manual hand-off path: the platform prepares the text, and a person sends it themselves.
- **Siblings** collapse into one delivery per guardian.
- **Channel order** is the guardian's preference, then email, then the in-app inbox. WhatsApp is used only with a Meta-approved template.
- **Status webhooks** (WhatsApp, email providers) update delivery status. They are verified by signature.

## Reporting (`app/services/reporting/`)

- **The `Figure` type.** Every number in every report is a `Figure`, which carries:
  - its denominator;
  - whether it is *measured* or *inferred*;
  - whether it was withheld.
- **Suppression rules.**
  - A percentage needs a base of at least 20.
  - Any count, numerator or remainder below 5 is withheld.
  - Schools can raise these floors but never lower them.
  - Withheld figures are listed with the reason. They are never silently dropped.
- **Reports and formats.** There are seven reports:
  - student profile
  - squad readiness
  - season review
  - cohort coverage
  - inspection evidence
  - plateau and stretch
  - kit utilisation
  Each exports to PDF (WeasyPrint, with Noto for Arabic), Excel, CSV and HTML. Each is branded and names the user who generated it.
- **Media consent.** Students without media consent are pseudonymised in generated reports ([why](security-and-privacy.md#media-consent)).

## Internationalisation and accessibility

- **Languages.** English and Arabic dictionaries are typed against each other, so a missing key fails the build.
- **Direction.** Switching language sets `lang` and `dir` on `<html>`. Layout uses logical properties (`start`/`end`, `ps`/`pe`) throughout, so RTL needs no separate stylesheet.
- **Arabic copy** uses gender-neutral forms. It needs native-speaker review before launch.
- **Accessibility.** Keyboard focus is always visible, a skip link leads to the content, and form controls have labels. Status is never shown by colour alone: claim badges use a dashed border for *inferred*.

## Observability

- Structured JSON logs carry a request ID.
- OpenTelemetry tracing is enabled with `STEM_OTEL_ENABLED`, and Sentry with `STEM_SENTRY_DSN`.
- `GET /api/v1/health` checks the database, Redis and object storage independently.

# STEM Programmes — competition and talent platform for schools

A multi-tenant platform for running a school's STEM competition programme. It covers competitions, squads, training, results, a skills engine that explains itself, reporting that will not publish misleading numbers, and parent communication with human release built in. It is built to the *School STEM Competition & Talent Platform — Build Specification*.

- **Teachers** run squads from a phone, including at venues with no signal. They record attendance, results and skills, and plan training from a shared gap list.
- **Programme admins** manage the catalogue, imports, kit, budget lines, templates and consent.
- **School leaders** get a six-number dashboard, approve spend, release parent messages and export inspection evidence.
- **Parents** see their own children's progress and consents in English or Arabic, and choose how they are contacted.
- **Students** (Year 9 and up) see their own skills, goals and next steps.

## What's in the repository

| Path | What it is |
|---|---|
| `backend/` | FastAPI API (Python 3.12, SQLAlchemy 2, Alembic, PostgreSQL 16 with row-level security, Redis/ARQ worker) |
| `backend/app/seed/data/skills_taxonomy.csv` | The skills taxonomy: 165 skills with a framework crosswalk ([docs](docs/skills-taxonomy.md)) |
| `backend/scripts/` | Database role setup, encrypted backup, timed restore drill |
| `frontend/` | React 18 + TypeScript PWA (Vite, TanStack Query, Tailwind), English/Arabic with RTL, offline capture |
| `e2e/` | Playwright walkthrough of the demo school |
| `deploy/`, `docker-compose.yml` | Local/demo stack: Postgres, Redis, MinIO, API, worker, web |
| `.github/workflows/ci.yml` | Tests, migration checks and security scans; deploy is blocked unless all pass |
| `docs/` | [Architecture](docs/architecture.md) · [Security and privacy](docs/security-and-privacy.md) · [Operations runbook](docs/operations.md) · [Skills taxonomy](docs/skills-taxonomy.md) |

## Quick start (Docker)

```sh
cp .env.example .env          # development defaults are fine for a local demo
docker compose up -d --build
docker compose run --rm api python -m app.cli seed-demo
open http://localhost:8080    # API docs: http://localhost:8080/api/docs
```

`seed-demo` builds a fictional school, *Falcon Heights Academy (demo)*, with school code `demo`. Every account's password is `Demo-Password-2026!`:

| Account | Role | Notes |
|---|---|---|
| `leader@demo.school.example` | School leader | MFA secret `STEMLEADERDEMOSECRETKEYA` |
| `admin@demo.school.example` | Programme admin | MFA secret `STEMADMINDEMOSECRETKEYAB` |
| `sara.haddad@demo.school.example` | Teacher — Robotics A and B | |
| `daniel.okafor@demo.school.example` | Teacher — Code Club, AI Lab | |
| `priya.nair@demo.school.example` | Teacher (Science), also a parent | One login, two roles |
| `aarav.sharma@demo.school.example` | Student, Year 10 | |
| `parent@family.example` | Parent | Arabic-speaking; magic link or password |

To get a current MFA code, add the secret to an authenticator app, or run `docker compose run --rm api python -m app.cli totp --secret <secret>`.

The demo is built to show the spec's own examples:
- Hamda's move from Developing to Secure, with the readiness trace behind it.
- Aarav's stretch flag, and Yousef's plateau.
- Four seniors sharing a gap ("closing this unblocks four students").
- An attendance concern that can only go to parents by hand.
- A competition that clashes with an exam window and with another entry.
- A budget waiting for the leader's approval.

## Local development

You need Python 3.12, Node 22.22 or later, PostgreSQL 16, Redis, and the system libraries for PDF export (`libpango-1.0-0 libpangoft2-1.0-0 fonts-noto-core`).

```sh
# Database roles (once per cluster), then a database owned by the schema owner
psql -U postgres -f backend/scripts/init_db.sql
psql -U postgres -c "CREATE DATABASE stemtrack OWNER stem_owner" -c "CREATE DATABASE stemtrack_test OWNER stem_owner"

# API on :8000
cd backend
python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
alembic upgrade head && python -m app.cli seed-demo
uvicorn app.main:app --reload

# PWA on :5173 (proxies /api to :8000)
cd frontend && npm ci && npm run dev
```

All configuration is environment variables prefixed `STEM_` (see `backend/app/config.py`). Development defaults work locally. In `production` or `staging`, the API refuses to start with the development secrets.

### Checks

| Command | What it runs |
|---|---|
| `cd backend && ruff check . && ruff format --check . && mypy app` | Lint, format, types |
| `cd backend && pytest` | 123 tests against a real Postgres as the non-superuser runtime role, so RLS is exercised |
| `cd frontend && npm run typecheck && npm test && npm run build` | Types, unit tests, production build |
| `cd e2e && npm ci && npx playwright test` | Browser walkthrough against a seeded stack (`E2E_BASE_URL`, default `vite preview` on :4173) |

CI runs all of these on every pull request. It also runs:
- a migration upgrade/check/downgrade round-trip;
- an encrypted backup and timed restore drill;
- the security scans: gitleaks, pip-audit, bandit, npm audit, and Trivy on both images.

Deploy happens only from `main`, and only when every job has passed.

## Acceptance criteria (spec §13.1)

| Criterion | Where it is demonstrated |
|---|---|
| A full competition day recorded offline on a phone syncs without loss or duplication | `test_capture_sync.py::test_full_competition_day_replayed_twice_has_no_duplicates`; e2e `13.1.1 attendance captured offline…`; frontend `queue.test.ts` (order preserved, same-millisecond writes, conflicts) |
| No student record is reachable outside the user's scope | `test_authorization.py` (scope, capability matrix, forged tokens) and `test_rls.py` (database-level isolation, even with raw SQL) |
| A readiness score is traceable to the exact awards and requirements, in the UI | `test_engine.py::test_every_line_is_traceable…`, `test_awards_readiness.py::test_readiness_trace…`; e2e `13.1.3 readiness is traced…` |
| No percentage on a base below 20; withheld figures shown as withheld, with the reason | `test_engine.py::test_percentage_on_base_below_twenty…`, `…withheld_figures_are_listed_never_dropped`, `test_reporting_media.py::test_leader_dashboard…` |
| No parent message without a named human approver in the audit log | `test_messaging.py::test_teacher_drafts_but_only_a_named_approver_releases`, `…every_send_passes_the_release_gate` |
| Consent withdrawal blocks the next scheduled send automatically | `test_messaging.py::test_consent_withdrawal_blocks_the_next_scheduled_send` |
| The whole interface renders correctly in Arabic with RTL | Typed EN/AR dictionaries (a missing key fails the build) plus `i18n.test.ts`; e2e `13.1.7` tests on desktop and phone |
| Subject-access export for one student in under 60 seconds | `test_reporting_media.py::test_subject_access_export_is_complete_and_fast` |
| A restore from backup into a clean environment succeeds in a timed drill | `backend/scripts/restore_drill.sh`, run in CI on every change; see [operations](docs/operations.md#backups-and-the-restore-drill) |
| A student with no media consent appears in no generated report, export or message | `test_reporting_media.py::test_student_without_media_consent_is_pseudonymised…`, `…media_upload_refuses…`, `…media_disappears_everywhere…`; enforced by database views |

The authorisation, suppression and consent criteria are verified exhaustively in the backend suite. The browser suite shows the user-facing ones end to end.

## Decisions taken on the spec's open questions (§13.4)

The spec asks for these to be settled before development. Each is a default you can change, not a fixed answer:

| Question | What was built | How to change it |
|---|---|---|
| Which MIS, and does it have an API? | CSV import at full parity: dry-run diff, match on MIS ID, idempotent, refuses stale previews. No vendor connector. | Write a connector that produces the same rows; the import pipeline is source-agnostic |
| Student portal in v1? | In, read-only, Year 9 and above | `student_portal_min_year` in tenant settings |
| Who approves parent messages? | Programme admins and leaders hold *release*; teachers draft. Emergency broadcast is leader-only. | The capability matrix in `backend/app/permissions.py` |
| Single school or group? | Built multi-tenant with row-level security from day one; one school is tenant one | `python -m app.cli provision-tenant` |
| Retention after a student leaves | Media deleted 12 months after leaving. The record is anonymised (not deleted) at 36 months, so historic season figures stay true. | `retention` in tenant settings; schedule in [security and privacy](docs/security-and-privacy.md#retention) |

## Before the first real student record

The software side is complete. These items are not code, and the spec is explicit that they come first:

- [ ] **Penetration test** by an independent tester, before any real data is loaded.
- [ ] **Legal review** of the consent model and data flows against the UAE PDPL (Federal Decree-Law No. 45 of 2021, whose executive regulations should be re-checked with counsel) and the Child Digital Safety law (Federal Decree-Law No. 26 of 2025, fully enforceable from January 2027). The defaults are built to the stricter reading; see [security and privacy](docs/security-and-privacy.md). This includes signing off how the media-consent rule is interpreted ([details](docs/security-and-privacy.md#media-consent)).
- [ ] **Hosting in a UAE region**: managed Postgres 16 with point-in-time recovery, managed Redis, object storage. Then a data processing agreement with each provider.
- [ ] **Curriculum review** of the skills taxonomy and its framework codes; [what to review](docs/skills-taxonomy.md#review-before-use).
- [ ] **Pilot** the taxonomy with one squad for a term before seeding it school-wide (spec §13.3).
- [ ] **WhatsApp**: a Meta Business account, and the nine message templates submitted for approval. Until then, sends fall back to email.
- [ ] **SMS**: a registered sender ID with the UAE regulator through the SMS provider.
- [ ] **Identity**: register the school's OIDC app (Entra ID or Google) and set the issuer, client and MFA claim values in Settings.
- [ ] **Secrets**: generate production secrets (see `.env.example`), and create the backup key pair with the private key held offline. Then run a first restore drill.
- [ ] **IP position**: if the builder is employed by the school or an education company, put intellectual property in writing first (spec §8.4).

## Known limitations

- **MIS sync**: the spec's nightly sync for schools whose MIS has an API is not built. CSV import is the supported route, and a connector only needs to produce the same rows (see above).

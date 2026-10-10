"""Synthetic demo school. Every person here is fictional; no real student data is used
(staging and demos must only ever hold synthetic data — spec §10.3).

Dates are generated relative to today so upcoming events stay upcoming.
Stories the demo is built to show (the examples from spec §4.4 and §6.3):
  * Hamda moved from Developing to Secure this half-term; next stretch is required for the final.
  * Yousef keeps participating but has no new verified skill in 90+ days (plateau).
  * Aarav is Advanced on every skill the Young Coders Cup requires (stretch).
  * Four senior roboticists share the same missing skill (shared gap list).
  * One student's attendance has dropped (attendance flag; message is manual only).
  * The Young Coders Cup clashes with an exam window and with a squad's other entry.
"""

import random
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.config import get_settings
from app.db import anonymous_session, tenant_session
from app.models import (
    Asset,
    AssetLoan,
    Attendance,
    BudgetLine,
    Competition,
    CompetitionEdition,
    Consent,
    ExamWindow,
    Guardian,
    Message,
    MessageDelivery,
    Result,
    ResultParticipant,
    RoleAssignment,
    Season,
    Skill,
    SkillAward,
    SkillRequirement,
    Squad,
    SquadMembership,
    SquadTargetEdition,
    Student,
    StudentGuardian,
    Tenant,
    TrainingSession,
    User,
)
from app.models.enums import (
    ApprovalStatus,
    AssetCondition,
    AttendanceStatus,
    AwardSource,
    AwardStatus,
    BudgetCategory,
    Channel,
    ConsentDecision,
    ConsentPurpose,
    DeliveryStatus,
    EntryFormat,
    InsightStatus,
    Language,
    MessageStatus,
    MessageType,
    Role,
    ScopeType,
    Tier,
)
from app.security import blind_index, encrypt_field, hash_password
from app.services.flags import sync_flags
from app.services.insights import generate_for_student
from app.services.performance import performance_index
from app.tenancy import load_settings

SLUG = "demo"
STORY_NAMES = {"Hamda", "Saeed", "Yousef", "Aarav", "Rohan"}
PASSWORD = "Demo-Password-2026!"
LEADER_TOTP = "STEMLEADERDEMOSECRETKEYA"
ADMIN_TOTP = "STEMADMINDEMOSECRETKEYAB"

GIRLS = [
    "Hamda",
    "Mariam",
    "Noor",
    "Aisha",
    "Layla",
    "Sara",
    "Fatima",
    "Zainab",
    "Priya",
    "Ananya",
    "Emily",
    "Sophie",
    "Hana",
    "Reem",
    "Maitha",
    "Shamma",
    "Isabella",
    "Olivia",
    "Amira",
    "Yasmin",
    "Leen",
    "Dana",
    "Ishita",
    "Chloe",
    "Grace",
    "Mira",
    "Salma",
    "Rania",
    "Nadia",
    "Elif",
]
BOYS = [
    "Yousef",
    "Aarav",
    "Omar",
    "Saeed",
    "Khalid",
    "Ahmed",
    "Rashid",
    "Hamdan",
    "Zayed",
    "Ali",
    "Arjun",
    "Rohan",
    "Vihaan",
    "James",
    "Oliver",
    "Lucas",
    "Adam",
    "Ibrahim",
    "Faisal",
    "Mohammed",
    "Ethan",
    "Noah",
    "Karim",
    "Tariq",
    "Hassan",
    "Daniel",
    "Leo",
    "Mateo",
    "Kabir",
    "Sami",
]
FAMILIES = [
    "Al Falasi",
    "Al Mansoori",
    "Al Suwaidi",
    "Al Mazrouei",
    "Al Nuaimi",
    "Al Ketbi",
    "Al Shamsi",
    "Al Hashimi",
    "Khan",
    "Sharma",
    "Patel",
    "Nair",
    "Iyer",
    "Haddad",
    "Khoury",
    "Saleh",
    "Rahman",
    "Hussain",
    "Smith",
    "Taylor",
    "Brown",
    "Wilson",
    "Fernandes",
    "D'Souza",
    "Ozturk",
    "Kaya",
    "Mensah",
    "Okafor",
    "Ali",
    "Hassan",
    "Qureshi",
    "Siddiqui",
    "Abdullah",
    "Bakr",
    "Farouk",
    "Lopez",
    "Garcia",
    "Rossi",
    "Novak",
    "Chen",
]


def _d(base: date, days: int) -> date:
    return base + timedelta(days=days)


def _at(d: date, hh: int = 15) -> datetime:
    return datetime.combine(d, time(hh, 30), tzinfo=UTC)


async def seed_demo() -> None:
    from app.cli import provision_tenant

    if get_settings().environment == "production":
        raise SystemExit("seed-demo creates fictional people and is refused in production.")
    tid = await provision_tenant(SLUG, "Falcon Heights Academy (demo)", "أكاديمية فالكون هايتس (تجريبية)")
    async with tenant_session(tid) as s:
        if await s.scalar(select(Student.id).limit(1)):
            print("demo already seeded")
            return
        rng = random.Random(42)  # noqa: S311
        today = datetime.now(UTC).date()
        year0 = today.year if today.month >= 8 else today.year - 1
        skills = {k.code: k for k in (await s.scalars(select(Skill))).all()}

        # ---------- Staff ----------
        def user(
            email: str, name: str, *roles: tuple[Role, ScopeType, uuid.UUID | None], totp: str | None = None
        ) -> User:
            u = User(email=email, display_name=name, password_hash=hash_password(PASSWORD))
            if totp:
                from app.security import encrypt_field as enc

                u.mfa_secret_enc, u.mfa_enabled = enc(totp), True
            s.add(u)
            return u

        leader = user("leader@demo.school.example", "Dr Mariam Al Suwaidi", totp=LEADER_TOTP)
        admin = user("admin@demo.school.example", "Omar Khalil", totp=ADMIN_TOTP)
        t_sara = user("sara.haddad@demo.school.example", "Sara Haddad")
        t_daniel = user("daniel.okafor@demo.school.example", "Daniel Okafor")
        t_priya = user("priya.nair@demo.school.example", "Priya Nair")
        await s.flush()
        for u, role in (
            (leader, Role.LEADER),
            (admin, Role.PROGRAMME_ADMIN),
            (t_sara, Role.TEACHER),
            (t_daniel, Role.TEACHER),
            (t_priya, Role.TEACHER),
        ):
            s.add(RoleAssignment(user_id=u.id, role=role, scope_type=ScopeType.SCHOOL))
        tenant = await s.get(Tenant, tid)
        assert tenant is not None
        settings = load_settings(tenant.settings)

        # ---------- Seasons & exam windows ----------
        prev = Season(
            name=f"{year0 - 1}-{str(year0)[2:]}",
            starts_on=date(year0 - 1, 8, 15),
            ends_on=date(year0, 7, 15),
            budget_envelope=Decimal("48000"),
        )
        cur = Season(
            name=f"{year0}-{str(year0 + 1)[2:]}",
            starts_on=date(year0, 8, 15),
            ends_on=date(year0 + 1, 7, 15),
            budget_envelope=Decimal("60000"),
        )
        s.add_all([prev, cur])
        s.add(ExamWindow(name="Term 1 assessments", starts_on=_d(today, 63), ends_on=_d(today, 67)))
        s.add(
            ExamWindow(
                name="Mock examinations (Years 11 and 13)",
                starts_on=_d(today, 105),
                ends_on=_d(today, 116),
                year_groups=[11, 13],
            )
        )
        await s.flush()

        # ---------- Competitions, editions, requirements ----------
        def comp(
            name: str, name_ar: str, organiser: str, discipline: str, tier: Tier, fmt: EntryFormat, field: int
        ) -> Competition:
            c = Competition(
                name=name,
                name_ar=name_ar,
                organiser=organiser,
                discipline=discipline,
                tier=tier,
                entry_format=fmt,
                typical_field_size=field,
                proposed_by_id=admin.id,
                approved_by_id=admin.id,
            )
            s.add(c)
            return c

        grc = comp(
            "Gulf Robotics Challenge",
            "تحدي الخليج للروبوتات",
            "Gulf Robotics Association",
            "Robotics",
            Tier.EMIRATE,
            EntryFormat.TEAM,
            28,
        )
        ycc = comp(
            "Young Coders Cup",
            "كأس المبرمجين الصغار",
            "Inter-school Computing Network",
            "Coding",
            Tier.INTER_SCHOOL,
            EntryFormat.INDIVIDUAL,
            40,
        )
        ai_award = comp(
            "National AI Innovation Award",
            "جائزة الابتكار الوطنية في الذكاء الاصطناعي",
            "National Innovation Council",
            "AI",
            Tier.NATIONAL,
            EntryFormat.TEAM,
            60,
        )
        sci_fair = comp(
            "School Science & Engineering Fair",
            "معرض العلوم والهندسة المدرسي",
            "Falcon Heights Academy",
            "Science",
            Tier.SCHOOL,
            EntryFormat.INDIVIDUAL,
            35,
        )
        eng_oly = comp(
            "International Junior Engineering Olympiad",
            "الأولمبياد الدولي للهندسة للناشئين",
            "Junior Engineering Olympiad Committee",
            "Engineering",
            Tier.INTERNATIONAL,
            EntryFormat.TEAM,
            120,
        )
        design = comp(
            "Inter-school Design Sprint",
            "سباق التصميم بين المدارس",
            "Design Educators Forum",
            "Design",
            Tier.INTER_SCHOOL,
            EntryFormat.TEAM,
            18,
        )
        await s.flush()

        def reqs(
            c: Competition,
            items: list[tuple[str, int, float, bool]],
            edition: CompetitionEdition | None = None,
        ) -> None:
            for code, lvl, w, core in items:
                s.add(
                    SkillRequirement(
                        competition_id=c.id,
                        edition_id=edition.id if edition else None,
                        skill_id=skills[code].id,
                        required_level=lvl,
                        weight=Decimal(str(w)),
                        is_core=core,
                    )
                )

        reqs(
            grc,
            [
                ("ROBO.BUILD.02", 2, 1, False),
                ("ROBO.SENS.02", 3, 3, True),
                ("ROBO.SENS.05", 2, 1, False),
                ("ROBO.CTRL.01", 3, 3, True),
                ("ROBO.CTRL.05", 3, 3, True),
                ("ROBO.STRAT.02", 2, 1, False),
                ("PROG.LOOP.02", 3, 1.5, True),
                ("PROG.COND.03", 2, 1, False),
                ("COL.TEAM.01", 2, 1, False),
                ("COL.COMM.03", 2, 1, False),
            ],
        )
        reqs(
            ycc,
            [
                ("PROG.ALGO.02", 2, 1, False),
                ("PROG.LOOP.02", 3, 2, True),
                ("PROG.COND.03", 3, 2, True),
                ("PROG.DATA.03", 2, 1, False),
                ("PROG.MOD.02", 2, 1, False),
                ("PROG.DBG.02", 2, 1, False),
                ("PROG.COMP.02", 2, 1, False),
            ],
        )
        reqs(
            ai_award,
            [
                ("AI.CONC.02", 3, 1, False),
                ("AI.DATA.01", 3, 1, False),
                ("AI.DATA.05", 2, 1, False),
                ("AI.ML.01", 3, 2, True),
                ("AI.ML.02", 2, 1, False),
                ("AI.ETH.01", 3, 2, True),
                ("AI.APP.01", 3, 1, False),
                ("COL.COMM.05", 3, 1, False),
                ("DES.UX.01", 2, 1, False),
            ],
        )
        reqs(
            sci_fair,
            [
                ("SCI.INQ.01", 2, 1, False),
                ("SCI.INQ.03", 3, 2, True),
                ("SCI.DATA.02", 3, 1, False),
                ("SCI.DATA.03", 2, 1, False),
                ("SCI.EXPL.01", 3, 2, True),
                ("SCI.COMM.01", 3, 1, False),
                ("SCI.COMM.03", 2, 1, False),
            ],
        )
        reqs(
            eng_oly,
            [
                ("ENG.DP.01", 3, 1, False),
                ("ENG.DP.03", 3, 1, False),
                ("ENG.DP.04", 3, 2, True),
                ("ENG.CAD.01", 3, 1, False),
                ("ENG.TEST.01", 3, 1, False),
                ("ENG.STRUCT.03", 2, 1, False),
                ("COL.TEAM.02", 3, 1, False),
            ],
        )
        reqs(
            design,
            [
                ("DES.UX.01", 2, 1, False),
                ("DES.UX.03", 2, 1, False),
                ("DES.CREAT.01", 3, 2, True),
                ("DES.VIS.01", 2, 1, False),
                ("COL.COMM.05", 2, 1, False),
            ],
        )

        grc_rubric = [
            {
                "key": "robot_design",
                "label": "Robot design",
                "max_score": 4,
                "skill_code": "ROBO.BUILD.02",
                "level_if_met": 3,
                "threshold": 0.75,
            },
            {
                "key": "programming",
                "label": "Programming",
                "max_score": 4,
                "skill_code": "PROG.LOOP.02",
                "level_if_met": 3,
                "threshold": 0.75,
            },
            {
                "key": "strategy",
                "label": "Mission strategy",
                "max_score": 4,
                "skill_code": "ROBO.STRAT.02",
                "level_if_met": 3,
                "threshold": 0.75,
            },
            {
                "key": "core_values",
                "label": "Core values",
                "max_score": 4,
                "skill_code": "COL.CIT.01",
                "level_if_met": 3,
                "threshold": 0.75,
            },
        ]

        def edition(
            c: Competition, season: Season, name: str, start: date, days: int = 1, **kw
        ) -> CompetitionEdition:  # noqa: ANN003
            e = CompetitionEdition(
                competition_id=c.id,
                season_id=season.id,
                name=name,
                tier=kw.pop("tier", c.tier),
                event_starts=start,
                event_ends=_d(start, days - 1),
                **kw,
            )
            s.add(e)
            return e

        grc_prev = edition(
            grc,
            prev,
            f"Gulf Robotics Challenge {year0 - 1} — Emirate Qualifier",
            _d(today, -330),
            2,
            venue="Dubai Exhibition Centre",
            entry_fee=Decimal("600"),
            expected_field_size=28,
            eligible_year_min=6,
            eligible_year_max=12,
            rubric=grc_rubric,
            stage="Emirate qualifier",
        )
        ycc_prev = edition(
            ycc,
            prev,
            f"Young Coders Cup {year0 - 1}",
            _d(today, -300),
            venue="Online",
            entry_fee=Decimal("100"),
            expected_field_size=40,
            eligible_year_min=7,
            eligible_year_max=11,
        )
        scrimmage = edition(
            grc,
            cur,
            f"GRC Practice Scrimmage {year0}",
            _d(today, -10),
            tier=Tier.SCHOOL,
            venue="School hall",
            expected_field_size=6,
            eligible_year_min=6,
            eligible_year_max=12,
            rubric=grc_rubric,
            stage="School scrimmage",
        )
        grc_cur = edition(
            grc,
            cur,
            f"Gulf Robotics Challenge {year0} — Emirate Qualifier",
            _d(today, 40),
            2,
            venue="Dubai Exhibition Centre",
            entry_fee=Decimal("650"),
            expected_field_size=30,
            eligible_year_min=6,
            eligible_year_max=12,
            registration_opens=_d(today, -30),
            registration_closes=_d(today, 25),
            rubric=grc_rubric,
            stage="Emirate qualifier",
            external_registration_url="https://robotics.example/register",
        )
        grc_final = edition(
            grc,
            cur,
            f"Gulf Robotics Challenge {year0 + 1} — National Final",
            _d(today, 160),
            2,
            tier=Tier.NATIONAL,
            venue="Abu Dhabi National Exhibition Centre",
            entry_fee=Decimal("900"),
            expected_field_size=24,
            eligible_year_min=6,
            eligible_year_max=12,
            registration_opens=_d(today, 60),
            registration_closes=_d(today, 130),
            stage="National final",
        )
        ycc_cur = edition(
            ycc,
            cur,
            f"Young Coders Cup {year0}",
            _d(today, 64),
            2,
            venue="Online",
            entry_fee=Decimal("120"),
            expected_field_size=40,
            eligible_year_min=7,
            eligible_year_max=11,
            registration_opens=_d(today, -20),
            registration_closes=_d(today, 45),
        )
        ai_cur = edition(
            ai_award,
            cur,
            f"National AI Innovation Award {year0 + 1}",
            _d(today, 130),
            venue="Abu Dhabi",
            entry_fee=Decimal("0"),
            expected_field_size=60,
            eligible_year_min=9,
            eligible_year_max=13,
            registration_opens=_d(today, -5),
            registration_closes=_d(today, 90),
        )
        sci_cur = edition(
            sci_fair,
            cur,
            f"School Science & Engineering Fair {year0 + 1}",
            _d(today, 105),
            venue="School hall",
            expected_field_size=35,
            eligible_year_min=5,
            eligible_year_max=12,
        )
        eng_cur = edition(
            eng_oly,
            cur,
            f"International Junior Engineering Olympiad {year0 + 1}",
            _d(today, 190),
            3,
            venue="Doha",
            entry_fee=Decimal("2400"),
            expected_field_size=120,
            eligible_year_min=10,
            eligible_year_max=13,
            registration_opens=_d(today, -10),
            registration_closes=_d(today, 120),
        )
        design_cur = edition(
            design,
            cur,
            f"Inter-school Design Sprint {year0 + 1}",
            _d(today, 130),
            venue="Design District",
            entry_fee=Decimal("150"),
            expected_field_size=18,
            eligible_year_min=7,
            eligible_year_max=12,
            registration_opens=_d(today, -15),
            registration_closes=_d(today, 50),
        )
        await s.flush()
        # The final asks for more than the qualifier: proportional control, and Advanced autonomy.
        reqs(grc, [("ROBO.CTRL.02", 3, 2, True), ("ROBO.CTRL.05", 4, 2, True)], grc_final)

        # ---------- Students & guardians ----------
        students: list[Student] = []
        guardians: list[Guardian] = []

        def guardian(
            name: str, email: str, lang: Language, channel: Channel, with_wa: bool = True
        ) -> Guardian:
            g = Guardian(
                full_name=name,
                email_enc=encrypt_field(email),
                email_hash=blind_index(email),
                phone_enc=encrypt_field(f"+97150{rng.randint(1000000, 9999999)}"),
                whatsapp_enc=encrypt_field(f"+97155{rng.randint(1000000, 9999999)}") if with_wa else None,
                language=lang,
                preferred_channel=channel,
                external_mis_id=f"G{len(guardians) + 1:05d}",
            )
            s.add(g)
            guardians.append(g)
            return g

        def student(given: str, family: str, year: int, gender: str, given_ar: str | None = None) -> Student:
            age = year + 5
            st = Student(
                external_mis_id=f"S{len(students) + 1:05d}",
                given_name=given,
                family_name=family,
                full_name_ar=given_ar,
                year_group=year,
                gender=gender,
                date_of_birth=date(today.year - age, rng.randint(1, 12), rng.randint(1, 28)),
                house=rng.choice(["Falcon", "Oryx", "Ghaf", "Dhow"]),
                enrolled_on=date(year0 - 3, 8, 25),
            )
            s.add(st)
            students.append(st)
            return st

        def link(st: Student, g: Guardian, rel: str = "parent", primary: bool = True) -> None:
            s.add(StudentGuardian(student=st, guardian=g, relationship_label=rel, is_primary_contact=primary))

        # Named story students.
        fam_falasi = guardian("Mariam Al Falasi", "parent@family.example", Language.AR, Channel.WHATSAPP)
        hamda = student("Hamda", "Al Falasi", 9, "F", "حمدة الفلاسي")
        saeed = student("Saeed", "Al Falasi", 6, "M", "سعيد الفلاسي")
        link(hamda, fam_falasi, "mother")
        link(saeed, fam_falasi, "mother")
        yousef = student("Yousef", "Al Mansoori", 8, "M", "يوسف المنصوري")
        link(
            yousef,
            guardian("Khalid Al Mansoori", "k.mansoori@family.example", Language.AR, Channel.WHATSAPP),
            "father",
        )
        aarav = student("Aarav", "Sharma", 10, "M")
        link(
            aarav, guardian("Neha Sharma", "neha.sharma@family.example", Language.EN, Channel.EMAIL), "mother"
        )
        rohan = student("Rohan", "Nair", 7, "M")
        g_priya = guardian("Priya Nair", "priya.nair@family.example", Language.EN, Channel.EMAIL)
        link(rohan, g_priya, "mother")
        # Everyone else: synthetic and deterministic.
        sibling_pool: list[Guardian] = []
        for i in range(150):
            gender = "F" if i % 2 == 0 else "M"
            given = rng.choice([n for n in (GIRLS if gender == "F" else BOYS) if n not in STORY_NAMES])
            family = rng.choice(FAMILIES)
            st = student(given, family, 3 + i % 11, gender)
            if sibling_pool and rng.random() < 0.12:
                link(st, sibling_pool.pop(), "parent")
                continue
            lang = Language.AR if family.startswith("Al ") or rng.random() < 0.15 else Language.EN
            ch = rng.choices([Channel.WHATSAPP, Channel.EMAIL, Channel.IN_APP], [5, 4, 1])[0]
            g = guardian(
                f"{rng.choice(GIRLS + BOYS)} {family}", f"family{i + 1:03d}@family.example", lang, ch
            )
            link(st, g)
            if rng.random() < 0.3:
                g2 = guardian(
                    f"{rng.choice(GIRLS + BOYS)} {family}",
                    f"family{i + 1:03d}b@family.example",
                    lang,
                    Channel.EMAIL,
                    with_wa=False,
                )
                link(st, g2, "parent", primary=False)
            sibling_pool.append(g)
        await s.flush()

        # Parent, teacher-who-is-a-parent, and student accounts.
        parent_user = User(
            email="parent@family.example",
            display_name="Mariam Al Falasi",
            locale=Language.AR,
            password_hash=hash_password(PASSWORD),
        )
        student_user = User(
            email="aarav.sharma@demo.school.example",
            display_name="Aarav Sharma",
            password_hash=hash_password(PASSWORD),
        )
        s.add_all([parent_user, student_user])
        await s.flush()
        fam_falasi.user_id, g_priya.user_id, aarav.user_id = parent_user.id, t_priya.id, student_user.id
        s.add_all(
            [
                RoleAssignment(
                    user_id=parent_user.id,
                    role=Role.PARENT,
                    scope_type=ScopeType.GUARDIAN,
                    scope_id=fam_falasi.id,
                ),
                RoleAssignment(
                    user_id=t_priya.id, role=Role.PARENT, scope_type=ScopeType.GUARDIAN, scope_id=g_priya.id
                ),
                RoleAssignment(
                    user_id=student_user.id,
                    role=Role.STUDENT,
                    scope_type=ScopeType.STUDENT,
                    scope_id=aarav.id,
                ),
            ]
        )

        # ---------- Consent: media is opt-in (~85%); under-13 data processing recorded ----------
        decided = _at(date(year0, 8, 28))
        for st in students:
            if st is aarav or st is hamda or rng.random() < 0.85:
                s.add(
                    Consent(
                        student_id=st.id,
                        purpose=ConsentPurpose.MEDIA,
                        version=1,
                        decision=ConsentDecision.GRANTED,
                        decided_at=decided,
                        method="mis_import",
                    )
                )
            if st.year_group + 5 < 13:
                s.add(
                    Consent(
                        student_id=st.id,
                        purpose=ConsentPurpose.DATA_PROCESSING,
                        version=1,
                        decision=ConsentDecision.GRANTED,
                        decided_at=decided,
                        method="paper",
                    )
                )

        # ---------- Squads ----------
        targets: list[tuple[Squad, list[CompetitionEdition]]] = []

        def squad(
            name: str, season: Season, discipline: str, coach: User, eds: list[CompetitionEdition]
        ) -> Squad:
            sq = Squad(name=name, season_id=season.id, discipline=discipline, lead_coach_user_id=coach.id)
            s.add(sq)
            targets.append((sq, eds))
            return sq

        robo_a = squad("Robotics A (Senior)", cur, "Robotics", t_sara, [grc_cur, grc_final, scrimmage])
        robo_b = squad("Robotics B (Junior)", cur, "Robotics", t_sara, [grc_cur, scrimmage])
        code = squad("Code Club", cur, "Coding", t_daniel, [ycc_cur])
        ai_lab = squad("AI Lab", cur, "AI", t_daniel, [ai_cur, design_cur])  # double-booked: clash demo
        sci = squad("Science Fair Team", cur, "Science", t_priya, [sci_cur])
        robo_prev = squad(f"Robotics {prev.name}", prev, "Robotics", t_sara, [grc_prev])
        code_prev = squad(f"Code Club {prev.name}", prev, "Coding", t_daniel, [ycc_prev])
        await s.flush()
        for sq, eds in targets:
            for e in eds:
                s.add(SquadTargetEdition(squad_id=sq.id, edition_id=e.id))
            s.add(
                RoleAssignment(
                    user_id=sq.lead_coach_user_id,
                    role=Role.TEACHER,
                    scope_type=ScopeType.SQUAD,
                    scope_id=sq.id,
                )
            )

        def pick(years: range, n: int, exclude: set) -> list[Student]:
            pool = [x for x in students if x.year_group in years and x.id not in exclude]
            rng.shuffle(pool)
            return pool[:n]

        used: set[uuid.UUID] = set()
        roles = {
            "Robotics": ["driver", "programmer", "builder", "presenter"],
            "Coding": ["programmer"],
            "AI": ["data lead", "programmer", "presenter"],
            "Science": ["researcher", "presenter"],
        }
        members: dict[uuid.UUID, list[Student]] = {}

        def enrol(sq: Squad, studs: list[Student], joined: date) -> None:
            members[sq.id] = studs
            for i, st in enumerate(studs):
                used.add(st.id)
                s.add(
                    SquadMembership(
                        squad_id=sq.id,
                        student_id=st.id,
                        joined_on=joined,
                        role=roles[sq.discipline or "Robotics"][i % len(roles[sq.discipline or "Robotics"])],
                        is_reserve=i >= 7,
                    )
                )

        senior_four = pick(range(9, 12), 4, {hamda.id, aarav.id})
        enrol(
            robo_a,
            [hamda, *senior_four, *pick(range(9, 12), 3, {hamda.id, aarav.id, *(x.id for x in senior_four)})],
            date(year0 - 1, 9, 1),
        )
        enrol(
            robo_b,
            [yousef, saeed, *pick(range(6, 9), 6, {yousef.id, saeed.id, *used})],
            date(year0 - 1, 9, 1),
        )
        enrol(code, [aarav, *pick(range(7, 11), 8, {aarav.id, *used})], date(year0 - 1, 9, 1))
        enrol(ai_lab, pick(range(9, 13), 6, used), date(year0, 9, 1))
        enrol(sci, [rohan, *pick(range(7, 12), 8, {rohan.id, *used})], date(year0, 9, 1))
        enrol(robo_prev, [hamda, yousef, *senior_four[:2]], date(year0 - 1, 9, 1))
        enrol(code_prev, [aarav, *members[code.id][1:4]], date(year0 - 1, 9, 1))
        await s.flush()

        # ---------- Skill awards (verified evidence) ----------
        def award(
            st: Student,
            code_: str,
            level: int,
            on: date,
            by: User,
            source: AwardSource = AwardSource.TEACHER,
            note: str | None = None,
            status: AwardStatus = AwardStatus.VERIFIED,
            **kw,
        ) -> SkillAward:  # noqa: ANN003
            a = SkillAward(
                student_id=st.id,
                skill_id=skills[code_].id,
                level=level,
                awarded_on=on,
                source=source,
                status=status,
                evidence_note=note,
                proposed_by_id=by.id,
                verified_by_id=by.id if status == AwardStatus.VERIFIED else None,
                verified_at=_at(on) if status == AwardStatus.VERIFIED else None,
                **kw,
            )
            s.add(a)
            return a

        req_codes = {
            "Robotics": [
                "ROBO.BUILD.02",
                "ROBO.SENS.02",
                "ROBO.SENS.05",
                "ROBO.CTRL.01",
                "ROBO.CTRL.05",
                "ROBO.STRAT.02",
                "PROG.LOOP.02",
                "PROG.COND.03",
                "COL.TEAM.01",
                "COL.COMM.03",
            ],
            "Coding": [
                "PROG.ALGO.02",
                "PROG.LOOP.02",
                "PROG.COND.03",
                "PROG.DATA.03",
                "PROG.MOD.02",
                "PROG.DBG.02",
                "PROG.COMP.02",
            ],
            "AI": [
                "AI.CONC.02",
                "AI.DATA.01",
                "AI.DATA.05",
                "AI.ML.01",
                "AI.ML.02",
                "AI.ETH.01",
                "AI.APP.01",
                "COL.COMM.05",
                "DES.UX.01",
            ],
            "Science": [
                "SCI.INQ.01",
                "SCI.INQ.03",
                "SCI.DATA.02",
                "SCI.DATA.03",
                "SCI.EXPL.01",
                "SCI.COMM.01",
                "SCI.COMM.03",
            ],
        }
        coach_of = {
            robo_a.id: t_sara,
            robo_b.id: t_sara,
            code.id: t_daniel,
            ai_lab.id: t_daniel,
            sci.id: t_priya,
        }
        special = {hamda.id, yousef.id, aarav.id, *(x.id for x in senior_four)}
        for sq in (robo_a, robo_b, code, ai_lab, sci):
            for st in members[sq.id]:
                if st.id in special:
                    continue
                ability = rng.uniform(0.35, 0.95)
                for c_ in req_codes[sq.discipline or "Robotics"]:
                    if rng.random() < 0.2:
                        continue
                    lvl = max(1, min(4, round(ability * 4 + rng.uniform(-0.8, 0.6))))
                    award(
                        st,
                        c_,
                        lvl,
                        _d(today, -rng.randint(5, 200)),
                        coach_of[sq.id],
                        note="Observed at training",
                    )

        # Hamda: Developing -> Secure on nested loops this half-term; strong elsewhere, missing ROBO.CTRL.02 for the final.
        award(
            hamda,
            "PROG.LOOP.02",
            2,
            _d(today, -140),
            t_sara,
            note="Loop with counter; exit condition needed help",
        )
        award(
            hamda,
            "PROG.LOOP.02",
            3,
            _d(today, -12),
            t_sara,
            note="Wrote nested loops with sensor exit at scrimmage",
        )
        award(hamda, "PROG.LOOP.02", 3, _d(today, -6), t_sara, note="Repeated independently in training")
        for c_, lvl in (
            ("ROBO.BUILD.02", 3),
            ("ROBO.SENS.02", 3),
            ("ROBO.SENS.05", 3),
            ("ROBO.CTRL.01", 3),
            ("ROBO.CTRL.05", 3),
            ("ROBO.STRAT.02", 3),
            ("PROG.COND.03", 3),
            ("COL.TEAM.01", 3),
            ("COL.COMM.03", 3),
        ):
            award(hamda, c_, lvl, _d(today, -rng.randint(20, 90)), t_sara, note="Verified at training")
        # Senior four: meet every qualifier requirement except the same two core control skills,
        # so they are "2 skills away" and closing either one makes all four ready.
        grc_levels = {
            "ROBO.BUILD.02": 2,
            "ROBO.SENS.02": 3,
            "ROBO.SENS.05": 2,
            "ROBO.STRAT.02": 2,
            "PROG.LOOP.02": 3,
            "PROG.COND.03": 2,
            "COL.TEAM.01": 2,
            "COL.COMM.03": 2,
        }
        for st in senior_four:
            for c_, lvl in grc_levels.items():
                award(
                    st,
                    c_,
                    min(4, lvl + rng.choice([0, 0, 1])),
                    _d(today, -rng.randint(15, 120)),
                    t_sara,
                    note="Verified at training",
                )
        # Yousef: plateau — last verified award long ago, still attending and competing.
        for c_ in ("ROBO.BUILD.02", "ROBO.SENS.02", "ROBO.CTRL.01", "COL.TEAM.01"):
            award(yousef, c_, 2, _d(today, -rng.randint(130, 170)), t_sara, note="Verified at training")
        # Aarav: Advanced on everything the Young Coders Cup requires.
        for c_ in req_codes["Coding"]:
            award(
                aarav,
                c_,
                4,
                _d(today, -rng.randint(10, 160)),
                t_daniel,
                note="Exceeds requirement in contest practice",
            )
        award(aarav, "PROG.COMP.01", 3, _d(today, -30), t_daniel, note="Solved 5/6 timed problems")
        # A self-assessment awaiting countersign (never counts until a teacher confirms).
        award(
            aarav,
            "PROG.PRAC.02",
            3,
            _d(today, -3),
            student_user,
            source=AwardSource.SELF,
            note="I use branches and pull requests for our team repo",
            status=AwardStatus.PROPOSED,
        )
        await s.flush()

        # ---------- Training sessions & attendance ----------
        weak_attender = members[sci.id][3]
        for sq in (robo_a, robo_b, code, ai_lab, sci):
            start = max(cur.starts_on + timedelta(days=10), _d(today, -60))
            d = start
            while d < today:
                ts = TrainingSession(
                    squad_id=sq.id,
                    starts_at=_at(d),
                    ends_at=_at(d, 17),
                    location="STEM Lab 2",
                    notes="Weekly squad training",
                    created_by_id=coach_of[sq.id].id,
                    skill_ids=[skills[c].id for c in rng.sample(req_codes[sq.discipline or "Robotics"], 2)],
                )
                s.add(ts)
                await s.flush()
                for st in members[sq.id]:
                    if st.id == weak_attender.id and d > _d(today, -50):
                        status = AttendanceStatus.ABSENT if rng.random() < 0.7 else AttendanceStatus.PRESENT
                    else:
                        status = rng.choices(
                            [
                                AttendanceStatus.PRESENT,
                                AttendanceStatus.LATE,
                                AttendanceStatus.ABSENT,
                                AttendanceStatus.EXCUSED,
                            ],
                            [85, 6, 5, 4],
                        )[0]
                    s.add(
                        Attendance(
                            session_id=ts.id,
                            student_id=st.id,
                            status=status,
                            recorded_by_id=coach_of[sq.id].id,
                        )
                    )
                d += timedelta(days=7)

        # ---------- Results ----------
        def result(
            ed: CompetitionEdition,
            sq: Squad | None,
            studs: list[Student],
            placement: int | None,
            field: int | None,
            by: User,
            rubric: dict | None = None,
            award_title: str | None = None,
            released: bool = True,
        ) -> Result:
            idx = performance_index(placement, field, settings.tier_weight(ed.tier))
            r = Result(
                edition_id=ed.id,
                squad_id=sq.id if sq else None,
                entry_name=sq.name if sq else None,
                placement=placement,
                field_size=field,
                rubric_scores=rubric or {},
                award_title=award_title,
                performance_index=idx.value,
                data_quality_flags=idx.flags,
                recorded_by_id=by.id,
                released_at=_at(ed.event_ends) if released else None,
                released_by_id=admin.id if released else None,
            )
            r.participants = [ResultParticipant(student_id=x.id) for x in studs]
            s.add(r)
            return r

        r_prev = result(
            grc_prev,
            robo_prev,
            members[robo_prev.id],
            4,
            28,
            t_sara,
            {"robot_design": 3, "programming": 2, "strategy": 3, "core_values": 4},
            award_title="Core Values Award",
        )
        result(ycc_prev, code_prev, [aarav], 2, 40, t_daniel)
        result(ycc_prev, code_prev, [members[code_prev.id][1]], 11, 40, t_daniel)
        result(
            ycc_prev, code_prev, [members[code_prev.id][2]], 25, None, t_daniel
        )  # missing field size: data-quality flag
        r_scrim = result(
            scrimmage,
            robo_a,
            members[robo_a.id][:4],
            2,
            6,
            t_sara,
            {"robot_design": 3, "programming": 4, "strategy": 3, "core_values": 3},
            released=False,
        )
        result(
            scrimmage,
            robo_b,
            [yousef, *members[robo_b.id][2:5]],
            4,
            6,
            t_sara,
            {"robot_design": 2, "programming": 2, "strategy": 2, "core_values": 3},
            released=False,
        )
        await s.flush()
        # Last season's rubric awards were confirmed; this season's scrimmage awards await batch confirmation.
        for st in members[robo_prev.id]:
            for key, code_ in (
                ("robot_design", "ROBO.BUILD.02"),
                ("strategy", "ROBO.STRAT.02"),
                ("core_values", "COL.CIT.01"),
            ):
                award(
                    st,
                    code_,
                    3,
                    grc_prev.event_ends,
                    t_sara,
                    source=AwardSource.RUBRIC,
                    result_id=r_prev.id,
                    rubric_criterion=key,
                    note=f"{key.replace('_', ' ').title()} scored at {grc_prev.name}",
                )
        from app.services.awards import propose_from_rubric

        await s.refresh(r_scrim, ["participants"])
        await propose_from_rubric(s, r_scrim, scrimmage)

        # ---------- Kit & inventory ----------
        assets = []
        for i in range(1, 9):
            assets.append(
                Asset(
                    tag=f"ROBO-KIT-{i:02d}",
                    name=f"Robotics kit #{i}",
                    category="Robotics kits",
                    unit_cost=Decimal("1850"),
                    purchased_on=date(year0 - 2, 9, 1),
                    service_due_on=_d(today, rng.randint(-20, 120)),
                )
            )
        for i in range(1, 7):
            assets.append(
                Asset(
                    tag=f"LAPTOP-{i:02d}",
                    name=f"Team laptop #{i}",
                    category="Laptops",
                    unit_cost=Decimal("3200"),
                )
            )
        assets += [
            Asset(
                tag="PRINT-3D-01",
                name="3D printer",
                category="Fabrication",
                unit_cost=Decimal("4200"),
                service_due_on=_d(today, -5),
            ),
            Asset(
                tag="FIELD-MAT-01",
                name="Competition field mat and models",
                category="Fields",
                unit_cost=Decimal("950"),
            ),
            Asset(
                tag="CALIPER-01",
                name="Digital calipers",
                category="Measurement",
                calibration_due_on=_d(today, 30),
            ),
            Asset(
                tag="BATT-AA",
                name="Rechargeable AA batteries",
                category="Consumables",
                is_consumable=True,
                quantity_on_hand=18,
                reorder_threshold=24,
            ),
            Asset(
                tag="FIL-PLA",
                name="PLA filament (1 kg)",
                category="Consumables",
                is_consumable=True,
                quantity_on_hand=6,
                reorder_threshold=4,
                unit_cost=Decimal("95"),
            ),
        ]
        s.add_all(assets)
        await s.flush()
        for i, a in enumerate(assets[:4]):
            s.add(
                AssetLoan(
                    asset_id=a.id,
                    edition_id=grc_prev.id,
                    squad_id=robo_prev.id,
                    quantity=1,
                    issued_at=_at(grc_prev.event_starts, 7),
                    due_back_on=_d(grc_prev.event_ends, 1),
                    returned_at=_at(_d(grc_prev.event_ends, 1), 9),
                    returned_quantity=1,
                    return_condition=AssetCondition.NEEDS_REPAIR if i == 2 else AssetCondition.GOOD,
                    issued_by_id=admin.id,
                    returned_to_id=admin.id,
                    notes="Drive motor damaged in round 3" if i == 2 else None,
                )
            )
        assets[2].condition = AssetCondition.NEEDS_REPAIR
        s.add(
            AssetLoan(
                asset_id=assets[0].id,
                edition_id=scrimmage.id,
                squad_id=robo_a.id,
                quantity=1,
                issued_at=_at(scrimmage.event_starts, 8),
                due_back_on=scrimmage.event_ends,
                issued_by_id=admin.id,
            )
        )

        # ---------- Budget ----------
        s.add_all(
            [
                BudgetLine(
                    season_id=prev.id,
                    edition_id=grc_prev.id,
                    category=BudgetCategory.ENTRY_FEE,
                    description="Team entry",
                    planned_amount=Decimal("600"),
                    actual_amount=Decimal("600"),
                    status=ApprovalStatus.APPROVED,
                    approved_by_id=leader.id,
                    approved_at=_at(_d(today, -360)),
                ),
                BudgetLine(
                    season_id=prev.id,
                    category=BudgetCategory.KIT,
                    description="Robotics kits refresh",
                    planned_amount=Decimal("14800"),
                    actual_amount=Decimal("14800"),
                    status=ApprovalStatus.APPROVED,
                    approved_by_id=leader.id,
                    approved_at=_at(_d(today, -380)),
                ),
                BudgetLine(
                    season_id=cur.id,
                    edition_id=grc_cur.id,
                    category=BudgetCategory.ENTRY_FEE,
                    description="Two team entries",
                    planned_amount=Decimal("1300"),
                    status=ApprovalStatus.APPROVED,
                    approved_by_id=leader.id,
                    approved_at=_at(_d(today, -20)),
                ),
                BudgetLine(
                    season_id=cur.id,
                    edition_id=grc_cur.id,
                    category=BudgetCategory.TRANSPORT,
                    description="Coach hire, return",
                    planned_amount=Decimal("1200"),
                    actual_amount=Decimal("1150"),
                    status=ApprovalStatus.APPROVED,
                    approved_by_id=leader.id,
                    approved_at=_at(_d(today, -20)),
                ),
                BudgetLine(
                    season_id=cur.id,
                    edition_id=grc_cur.id,
                    category=BudgetCategory.COVER,
                    description="Cover for two staff (Friday)",
                    planned_amount=Decimal("800"),
                    status=ApprovalStatus.APPROVED,
                    approved_by_id=leader.id,
                    approved_at=_at(_d(today, -18)),
                ),
                BudgetLine(
                    season_id=cur.id,
                    category=BudgetCategory.KIT,
                    description="Vision sensors for AI Lab (x6)",
                    planned_amount=Decimal("4500"),
                    status=ApprovalStatus.PROPOSED,
                ),
                BudgetLine(
                    season_id=cur.id,
                    edition_id=eng_cur.id,
                    category=BudgetCategory.ACCOMMODATION,
                    description="Doha hotel, 3 nights (6 students, 2 staff)",
                    planned_amount=Decimal("9600"),
                    status=ApprovalStatus.PROPOSED,
                ),
            ]
        )

        # ---------- A sent message for the audit trail ----------
        from app.models import MessageTemplate

        logistics_t = await s.scalar(select(MessageTemplate).where(MessageTemplate.key == "event_logistics"))
        msg = Message(
            message_type=MessageType.LOGISTICS,
            template_id=logistics_t.id if logistics_t else None,
            status=MessageStatus.COMPLETED,
            title=f"Logistics: {grc_prev.name}",
            student_ids=[x.id for x in members[robo_prev.id]],
            edition_id=grc_prev.id,
            variables={
                "meet_time": "06:45",
                "pickup_time": "17:30",
                "kit_list": "Lunch, water, school PE kit",
            },
            created_by_id=t_sara.id,
            previewed_at=_at(_d(grc_prev.event_starts, -6)),
            released_by_id=admin.id,
            released_at=_at(_d(grc_prev.event_starts, -5), 9),
        )
        s.add(msg)
        await s.flush()
        for st in members[robo_prev.id]:
            for sg in (
                await s.scalars(select(StudentGuardian).where(StudentGuardian.student_id == st.id))
            ).all():
                gd = await s.get(Guardian, sg.guardian_id)
                exists = await s.scalar(
                    select(MessageDelivery.id).where(
                        MessageDelivery.message_id == msg.id, MessageDelivery.guardian_id == sg.guardian_id
                    )
                )
                if gd is None or exists:
                    continue
                s.add(
                    MessageDelivery(
                        message_id=msg.id,
                        guardian_id=gd.id,
                        student_ids=[st.id],
                        language=gd.language,
                        channel_planned=gd.preferred_channel,
                        channel_used=Channel.EMAIL,
                        channels_attempted=["email"],
                        status=DeliveryStatus.READ,
                        template_version=1,
                        rendered_subject=f"{grc_prev.name}: arrangements",
                        rendered_body="(historical demo message)",
                        sent_at=msg.released_at,
                        delivered_at=msg.released_at,
                        opened_at=msg.released_at,
                    )
                )
                await s.flush()

        await s.flush()
        # ---------- Flags and insights ----------
        active = list((await s.scalars(select(Student))).all())
        await sync_flags(s, settings, active, today)
        participants = {x.id for v in members.values() for x in v}
        for st in active:
            if st.id in participants:
                for ins in await generate_for_student(s, settings, st, today):
                    if st.id == hamda.id:
                        ins.status, ins.reviewed_by_id, ins.reviewed_at = (
                            InsightStatus.APPROVED,
                            t_sara.id,
                            _at(today, 8),
                        )
        await s.commit()

    async with anonymous_session() as anon:
        t = await anon.scalar(select(Tenant).where(Tenant.slug == SLUG))
    print(
        "\nDemo school ready (tenant slug: demo). Password for every account: " + PASSWORD + "\n"
        "  leader@demo.school.example      Leader — MFA secret " + LEADER_TOTP + "\n"
        "  admin@demo.school.example       Programme admin — MFA secret " + ADMIN_TOTP + "\n"
        "  sara.haddad@demo.school.example Teacher (Robotics A & B)\n"
        "  daniel.okafor@demo.school.example Teacher (Code Club, AI Lab)\n"
        "  priya.nair@demo.school.example  Teacher (Science) and a parent\n"
        "  aarav.sharma@demo.school.example Student (Year 10)\n"
        "  parent@family.example           Parent (magic link, or password)\n"
        "Get a current MFA code with:  python -m app.cli totp --secret <secret>\n"
        f"tenant id: {t.id if t else '?'}"
    )

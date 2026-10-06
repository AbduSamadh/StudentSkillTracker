# Skills taxonomy

The spec calls the taxonomy the one thing to build first, "if you build nothing else" (§12): every later feature inherits its value or its weakness. It lives in one file, `backend/app/seed/data/skills_taxonomy.csv`, and is loaded into each school when the school is provisioned.

## Structure (spec §4.1)

| Column | Meaning |
|---|---|
| `code` | Stable identifier, `DOMAIN.STRAND.NN`, for example `PROG.LOOP.02`. **Never renamed or reused.** Awards, requirements and goals point at it. |
| `domain` | One of Robotics, AI, Coding, Engineering, Science, Design, Collaboration |
| `strand` | A sub-group inside the domain |
| `name` | The teacher-facing description of what the student can do, written as an observable behaviour |
| `parent_label_en` / `parent_label_ar` | The plain-language version parents see in messages and the portal |
| `typical_year_group` | Where the skill normally sits (3–12). Used for age-appropriate recommendations and stretch flags. |
| `framework_refs` | `;`-separated external codes, for example `CSTA:2-AP-10;ISTE:1.5c` |

The levels are the same for every skill: **Emerging → Developing → Secure → Advanced** (1–4). A requirement asks for a level, and an award records one.

## What is in it

There are 165 skills, inside the spec's 120–180 band:

| Domain | Skills | Strands |
|---|---:|---|
| Coding | 36 | Algorithms, Repetition, Selection, Variables and data structures, Decomposition and modularity, Testing and debugging, Professional practice, Web development, Competitive programming |
| Robotics | 28 | Mechanical build, Sensors, Control and autonomy, Electronics, Mission strategy, Maintenance and safety |
| AI | 22 | AI concepts, Data for AI, Machine learning, Generative AI, Ethics and responsibility, Applications |
| Engineering | 22 | Design process, Computer-aided design, Fabrication, Structures and mechanisms, Testing and measurement, Sustainability and cost |
| Science | 22 | Scientific inquiry, Data and analysis, Explanation and argument, Laboratory practice, Science communication |
| Collaboration | 20 | Teamwork, Communication, Self-management, Community and digital citizenship |
| Design | 15 | User-centred design, Visual communication, Creative thinking, Documentation and portfolio |

Skills span typical years 3–12, concentrated in years 6–10, where competition entry is heaviest.

## Framework crosswalk

Each skill maps to the external codes it gives evidence for. The same verified awards can then be reported against any of them without re-tagging: the Skills page (*Report against another framework*) or `GET /api/v1/skills/coverage?framework=CSTA`.

| Prefix | Framework | References |
|---|---|---:|
| `CSTA:` | CSTA K–12 Computer Science Standards (2017), standard identifiers such as `2-AP-10` | 71 across 41 standards |
| `ISTE:` | ISTE Standards for Students, indicators such as `1.5c` | 50 across 17 indicators |
| `NGSS:` | Next Generation Science Standards performance expectations, such as `MS-ETS1-1` | 46 across 20 expectations |
| `IBATL:` | IB Approaches to Learning skill categories: Thinking, Communication, Social, Self-management, Research | 40 |
| `MOEAI:` | UAE Ministry of Education AI curriculum domains, **in our own shorthand**. `FND` foundational concepts, `DATA` data and algorithms, `SW` software and tools, `ETH` ethics, `APP` real-world applications, `INNOV` innovation and project design, `POL` policy and community. | 27 |

A school can add its own scheme: use a new prefix, for example `FHA:`, in `framework_refs`. It then appears in the selector automatically.

Eleven skills deliberately have no external reference. They are mostly workshop and laboratory safety, soldering, technical drawing and remote driving, which these frameworks do not cover.

## Review before use

The taxonomy is a draft built from the spec's guidance, not a curriculum-approved document. Before the pilot:

1. **Check every framework code** against the current published standard. In particular, replace the `MOEAI:` shorthand with the Ministry's official outcome identifiers once the school has the curriculum documents.
2. **Check year placement** against the school's own schemes of work. `typical_year_group` drives stretch flags, so a skill placed two years too early makes every student look behind.
3. **Have a native Arabic-speaking teacher review `parent_label_ar`.** The labels are written to be gender-neutral, in plain language a parent understands.
4. **Pilot with one squad for a term** (spec §13.3). Watch for skills nobody tags (too fine, or not observable) and skills everyone already has (too coarse).

## Changing it

- **Edit the CSV and re-provision** (`python -m app.cli provision-tenant` with the same slug). `load_taxonomy` upserts by `code`, so names, labels, year groups and references can be revised safely. Admins can also edit a skill through the API (`PATCH /api/v1/skills/{id}`).
- **Never change a code.** The API refuses it.
- **Retire a skill** with `is_active: false`. It keeps every award earned against it, but disappears from skill pickers and coverage. Reloading the CSV does not bring it back; only skills the load creates start active.
- **To split a skill**, add the new codes and retire the old one. Existing awards stay against the old code, where they were earned.

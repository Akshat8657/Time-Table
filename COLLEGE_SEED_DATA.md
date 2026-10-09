# 🏫 COLLEGE SEED DATA — School of IT (Real AURO Data)

> **Claude Code:** this file holds the REAL college data to load into the system (the seed step after Phase 1).
> It works together with TIMETABLE_MASTER_PLAN.md (the master plan always wins on rules).
> **This is V3 — FINAL. Every earlier open question is answered. There are NO ❓ left. Do not ask the user again; seed exactly as written here.**
> Teacher accounts: use dummy emails like firstname.lastname@school-it.test — NO real emails. Real emails can be set later by the college.

Last updated: 09 Oct 2026 (V4 — External Faculty CORRECTED: it is TWO different people, one per track. Split the single "External Faculty" account into "External Faculty (AI&ML)" and "External Faculty (Cyber)": every AI-track Major (+its Lab) goes to the AI&ML account, every Cyber-track Major (+its Lab) to the Cyber account, in all 4 B.Sc. classes. Wherever this file says "External Faculty" in a subjects table, read it as: AI-track option → External Faculty (AI&ML), Cyber-track option → External Faculty (Cyber), and the shared track-group LAB splits the same way per track. Everything else unchanged from V3.)

---

## 1. Active Term

- **Odd Term 2026** (July–December 2026).
- Running semesters: Sem 1, 3, 5, 7 (B.Sc. IT) and Sem 1, 3 (M.Sc. IT, M.Sc. AI) — 8 classes total.
- Semester dates: **01 July 2026 – 30 Nov 2026** (user: default stands until the Sem 1 exam schedule is decided; easy to edit later on the Active Term screen).

## 2. Timing Structure (from the real timetables)

| Slot | Time |
|---|---|
| Period 1 | 10:00 – 11:00 |
| break | 11:00 – 11:05 |
| Period 2 | 11:05 – 12:05 |
| break | 12:05 – 12:10 |
| Period 3 | 12:10 – 1:10 |
| **LUNCH** | **1:10 – 1:40** |
| Period 4 | 1:40 – 2:40 |
| break | 2:40 – 2:45 |
| Period 5 | 2:45 – 3:45 |
| break | 3:45 – 3:50 |
| Period 6 | 3:50 – 4:50 |

- College hours: 10:00 – 4:50. Lecture = 1 hour. Short break = 5 min. Lunch = 30 min (1:10–1:40).
- Working days: **Monday–Saturday**; Sunday = holiday. Per-class Saturday OFF is set day-wise below.
- A "double block" = 2 consecutive periods; a break or even LUNCH between them does not break the block (SOL III = P3+P4).

## 3. Day-wise Schedule (per class) — CONFIRMED

| Class | Mon | Tue | Wed | Thu | Fri | Sat |
|---|---|---|---|---|---|---|
| B.Sc. Sem 1 | full (4:50) | full | full | full | full | OFF |
| B.Sc. Sem 3 | full | full | till 2:40 | till 2:40 | till 2:40 | OFF |
| B.Sc. Sem 5 | till 2:40 | till 2:40 | till 2:40 | full | full | OFF |
| B.Sc. Sem 7 | full | full | till 2:40 | **OFF (OJT)** | **OFF (OJT)** | **OFF (OJT)** |
| M.Sc. IT Sem 1 | full | full | full | full | full | OFF |
| M.Sc. IT Sem 3 | full | full | full | full | full | OFF |
| M.Sc. AI Sem 1 | full | full | full | full | full | OFF |
| M.Sc. AI Sem 3 | full | full | full | full | full | OFF |

(User confirmed: all M.Sc. classes = Mon–Fri full day, Saturday OFF.)

## 4. Teachers (master list) — FINAL

### HOD
| Name | Role | Specialization | Teaches |
|---|---|---|---|
| Dr. Papri Das | **HOD, School of IT** (also teaches — teacher mode applies) | IoT, Networks, Mathematics, Quantum | IoT for Industry 4.0, Big Data & IoT, Fundamentals of Mathematics, Computer Networks, Intro to Quantum Computing |

### Regular Faculty
| Name | Specialization | Teaches |
|---|---|---|
| Dr. Chirag Mehta | Programming (Python, JAVA, Full Stack) | Python Programming (+Lab), OOP using JAVA (+Lab), Advance JAVA, Full Stack Development (+Lab), Advanced Python (M.Sc. IT elective) |
| Dr. Dhaval Thaker | Data Mining, Databases, DevOps, RL | Data Mining, NO SQL, Reinforcement Learning, Intro to DevOps (+Lab), Science of Living I (B.Sc. Sem 1) |
| Dr. Juhi Khengar | Web, OS, Databases | RTOS, Advanced Web Development, Fundamentals of DBMS (+Lab), Operating Systems |
| Dr. Sunil Kumar | AI, Blockchain, Big Data | Agentic AI & Autonomous Systems (+Lab), Blockchain Technology (+Lab), Big Data Analytics (+Lab) |
| Ms. Shreya Chakrabarty | Cyber Security, Mobile | Mobile Computing, Network Security, Cyber Security & Digital Forensics, Digital Literacy, Data Governance & Privacy Engineering |
| **Maitri Hingu** | Software Engineering, Cloud, Quantum, UI/UX | **ALONE** on all previously shared subjects (Prof. Anurag Dixit has LEFT the college — do NOT create his account, NO co-teacher): SEC 1 Computer Organization & Architecture, UI/UX Design (+Lab), Intro to Quantum Computing (with Dr. Papri Das as the other option — seed Papri Das as primary, Maitri Hingu as co-teacher for Minor 7 only), Software Testing & QA, Cloud AI & MLOps (+Lab) |
| Ms. Juhikadevi Bhanjdeo | Science of Living | Science of Living I (M.Sc.) |
| Ms. Subarna Ghosh | Science of Living | Science of Living III (M.Sc. + B.Sc. Sem 3) |
| Dr. Richa Chaudhary | Psychology/Behavior | Dynamics of Human Behavior |
| Dr. Srinita Bhattacharjee | Communication | Communication Skills |
| Prof. Nimesh Joshi | Indian Culture | Foundation of Indian Culture I |
| Mr. Sushant Singh | Physical Education | PE, Sports & Yoga (ALL semesters, B.Sc. + M.Sc.) |
| **External Faculty (AI&ML)** | AI & Machine Learning track | All AI-track Majors (+Labs) in B.Sc. Sem 1, 3, 5, 7 |
| **External Faculty (Cyber)** | Cyber Security & Forensics track | All Cyber-track Majors (+Labs) in B.Sc. Sem 1, 3, 5, 7 |

### Visiting Faculty (hard day limits) — CONFIRMED
| Name | Teaches | Allowed days |
|---|---|---|
| Anjana Prajapati (VF) | AI & Machine Learning (M.Sc. AI Sem 1), Data Structures & Algorithms using C (+Lab, B.Sc. Sem 1) | **Mon + Tue + Wed** |
| Falguni Thakker (VF) | Generative AI & Foundation Models (M.Sc. AI Sem 3), Data Visualization (+Lab, B.Sc. Sem 3), Digital Image Processing (+Lab, B.Sc. Sem 7) | **Mon + Tue + Wed** |

## 5. Fixed Slots — FINAL (all double blocks)

| Session | Day + Periods | Covers (COMBINED where listed) | Teacher |
|---|---|---|---|
| SOL I (B.Sc.) | Monday P5+P6 (2:45–4:50) | B.Sc. Sem 1 | Dr. Dhaval Thaker |
| PE I (B.Sc.) | Tuesday P5+P6 (2:45–4:50) | B.Sc. Sem 1 | Mr. Sushant Singh |
| PE III (B.Sc.) | Monday P5+P6 (2:45–4:50) | B.Sc. Sem 3 | Mr. Sushant Singh |
| SOL III (B.Sc.) | Thursday P3+P4 (12:10–1:10 + 1:40–2:40, lunch between) | B.Sc. Sem 3 | Ms. Subarna Ghosh |
| SOL I (M.Sc.) | Wednesday P5+P6 (2:45–4:50) | **COMBINED: M.Sc. IT Sem 1 + M.Sc. AI Sem 1** | Ms. Juhikadevi Bhanjdeo |
| SOL III (M.Sc.) | Thursday P3+P4 (12:10–2:40, lunch between) | **COMBINED: M.Sc. IT Sem 3 + M.Sc. AI Sem 3** | Ms. Subarna Ghosh |
| PE (M.Sc.) | Monday P5+P6 (2:45–4:50) | **COMBINED: all M.Sc. classes (IT+AI, Sem 1+3)** | Mr. Sushant Singh |

**PE Monday note:** B.Sc. Sem 3 PE and M.Sc. PE are BOTH Monday P5+P6 with Mr. Sushant Singh. PE is a sports-ground session, so seed them as ONE combined session covering B.Sc. Sem 3 + all M.Sc. classes (no teacher clash). If the college later splits them, the HOD edits the slot.
- B.Sc. Sem 5 and Sem 7: **NO PE, NO SOL** — mark both classes with the "This class has none" switch.

## 6. Subjects — B.Sc. IT (tracks: AI & ML / Cyber Security & Forensics)

Track Majors = ELECTIVE GROUP: AI-track and Cyber-track subjects run in the SAME parallel slots, both taught by **External Faculty**. Theory subject = credits lectures/week; its LAB = 1 double block/week (1 credit, no warning).

### B.Sc. Sem 1 (Batch 2026) — 23 credits
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| Major 1 [TRACK GROUP]: Intro to AI & ML with Python ∥ Information Security Fundamentals | 3 | Elective group, parallel | External Faculty |
| Major 1 LAB (both tracks) | 1 | Lab | External Faculty |
| Major 2: Data Structures & Algorithms using C | 3 | Core | Anjana Prajapati (VF) |
| Major 2 LAB | 1 | Lab | Anjana Prajapati (VF) |
| Minor 1: Fundamentals of DBMS | 3 | Core | Dr. Juhi Khengar |
| Minor 1 LAB | 1 | Lab | Dr. Juhi Khengar |
| MD-1: Fundamentals of Mathematics | 2 | Core | Dr. Papri Das |
| MD-2: Dynamics of Human Behavior | 2 | Core | Dr. Richa Chaudhary |
| AEC 1: Communication Skills | 2 | Core | Dr. Srinita Bhattacharjee |
| SEC 1: Computer Organization & Architecture | 2 | Core | Maitri Hingu |
| VAC 1: Science of Living I | 2 | SOL — fixed | Dr. Dhaval Thaker |
| PE, Sports & Yoga – I | 1 | PE — fixed | Mr. Sushant Singh |

### B.Sc. Sem 3 (Batch 2025) — 23 credits
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| Major 5 [TRACK GROUP]: Deep Learning ∥ IT Application & Data Security | 3 | Elective group, parallel | External Faculty |
| Major 5 LAB | 1 | Lab | External Faculty |
| Major 6: OOP using JAVA | 3 | Core | Dr. Chirag Mehta |
| Major 6 LAB | 1 | Lab | Dr. Chirag Mehta |
| Major 7: Operating Systems | 4 | Core | Dr. Juhi Khengar |
| SEC 3: Data Visualization | 1 | Core | Falguni Thakker (VF) |
| SEC 3 LAB | 1 | Lab | Falguni Thakker (VF) |
| MD 5: Computer Networks | 2 | Core | Dr. Papri Das |
| MD 6: Digital Literacy | 2 | Core | Ms. Shreya Chakrabarty |
| AEC 3: Communication Skills / French / Sanskrit / Gujarati / Hindi | 2 | Core | **UNASSIGNED for now** (user: teacher set later; seeding must not fail because of this) |
| VAC 3: Science of Living III | 2 | SOL — fixed | Ms. Subarna Ghosh |
| PE, Sports & Yoga – III | 1 | PE — fixed | Mr. Sushant Singh |

### B.Sc. Sem 5 (Batch 2024) — 24 credits — NO PE/SOL
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| Major 11 [TRACK GROUP]: Pattern Recognition & Anomaly Detection / LLM Dev & Deployment ∥ Digital Forensics | 3 | Elective group, parallel | External Faculty |
| Major 11 LAB | 1 | Lab | External Faculty |
| Major 12: Blockchain Technology | 3 | Core | Dr. Sunil Kumar |
| Major 12 LAB | 1 | Lab | Dr. Sunil Kumar |
| Major 13: Full Stack Development | 3 | Core | Dr. Chirag Mehta |
| Major 13 LAB | 1 | Lab | Dr. Chirag Mehta |
| Minor 4: Data Governance & Privacy Engineering | 4 | Core | Ms. Shreya Chakrabarty |
| Minor 5: Introduction to DevOps | 3 | Core | Dr. Dhaval Thaker |
| Minor 5 LAB | 1 | Lab | Dr. Dhaval Thaker |
| SEC 5: UI/UX Design | 1 | Core | Maitri Hingu |
| SEC 5 LAB | 1 | Lab | Maitri Hingu |
| VAC 5: Foundation of Indian Culture – I | 2 | Core | Prof. Nimesh Joshi |

### B.Sc. Sem 7 (Batch 2023) — 22 credits — classes Mon–Wed only; Thu–Sat OFF (OJT) — NO PE/SOL
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| Major 17 [TRACK GROUP]: Pattern Recognition & Anomaly Detection ∥ Cryptography & Web App Security | 3 | Elective group, parallel | External Faculty |
| Major 17 LAB | 1 | Lab | External Faculty |
| Major 18: Big Data Analytics | 3 | Core | Dr. Sunil Kumar |
| Major 18 LAB | 1 | Lab | Dr. Sunil Kumar |
| Major 19: Digital Image Processing | 3 | Core | Falguni Thakker (VF) |
| Major 19 LAB | 1 | Lab | Falguni Thakker (VF) |
| Minor 7: Introduction to Quantum Computing | 4 | Core | Dr. Papri Das (primary) + Maitri Hingu (co-teacher) |
| OJT 1: On the Job Training – Major Project II | 6 | **Outside the weekly grid** — represented by the Thu/Fri/Sat OFF days; no slots | — |

## 7. Subjects — M.Sc. IT

4-credit L=3+P=2 subjects = 3 theory lectures + 1 lab double block per week, same teacher.
Elective options NOT listed below are NOT running this term — seed only what is written.

### M.Sc. IT Sem 1 — 23 credits
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| Advanced Web Development | 4 | Core (3 theory + 1 lab block) | Dr. Juhi Khengar |
| NO SQL | 4 | Core (3 + 1 lab block) | Dr. Dhaval Thaker |
| Mobile Computing | 4 | Core (4 theory) | Ms. Shreya Chakrabarty |
| Elective-I (running option): Network Security | 4 | Elective (3 + 1 lab block) | Ms. Shreya Chakrabarty |
| Elective-II (running option): Advanced Python Programming | 4 | Elective (3 + 1 lab block) | Dr. Chirag Mehta |
| Science of Living I | 2 | SOL — fixed, COMBINED with M.Sc. AI Sem 1 | Ms. Juhikadevi Bhanjdeo |
| PE & Yoga – I (Audit) | 1 | PE — fixed, COMBINED (all M.Sc.) | Mr. Sushant Singh |

### M.Sc. IT Sem 3 — 27 credits
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| Big Data & IoT | 4 | Core (3 + 1 lab block) | Dr. Papri Das |
| Software Testing & QA | 4 | Core (4 theory) | Maitri Hingu |
| Advance JAVA Programming | 4 | Core (3 + 1 lab block) | Dr. Chirag Mehta |
| Elective-V (running option): Cyber Security & Digital Forensics | 4 | Elective (3 + 1 lab block) | Ms. Shreya Chakrabarty |
| Elective-VI (running option): Blockchain Technology | 4 | Elective (3 + 1 lab block) | Dr. Sunil Kumar |
| Internship | 4 | **Outside the timetable — no slots, no OFF days** | — |
| Science of Living III | 2 | SOL — fixed, COMBINED with M.Sc. AI Sem 3 | Ms. Subarna Ghosh |
| PE & Yoga – III (Audit) | 1 | PE — fixed, COMBINED (all M.Sc.) | Mr. Sushant Singh |

## 8. Subjects — M.Sc. AI

### M.Sc. AI Sem 1 — 23 credits
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| AI & Machine Learning | 4 | Core | Anjana Prajapati (VF) |
| Python Programming | 3 | Core | Dr. Chirag Mehta |
| Python Programming LAB | 1 | Lab | Dr. Chirag Mehta |
| Internet of Things for Industry 4.0 | 3 | Core | Dr. Papri Das |
| IoT LAB | 1 | Lab | Dr. Papri Das |
| Real Time Operating System | 4 | Core | Dr. Juhi Khengar |
| Elective-I (running option): Data Mining | 4 | Elective (3 + 1 lab block) | Dr. Dhaval Thaker |
| Science of Living I | 2 | SOL — fixed, COMBINED with M.Sc. IT Sem 1 | Ms. Juhikadevi Bhanjdeo |
| PE, Sports & Yoga – I (Audit) | 1 | PE — fixed, COMBINED (all M.Sc.) | Mr. Sushant Singh |

### M.Sc. AI Sem 3 — 27 credits
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| Reinforcement Learning | 4 | Core | Dr. Dhaval Thaker |
| Cloud AI and MLOps | 3 | Core | Maitri Hingu |
| Cloud AI & MLOps LAB | 1 | Lab | Maitri Hingu |
| Agentic AI & Autonomous Systems | 3 | Core | Dr. Sunil Kumar |
| Agentic AI LAB | 1 | Lab | Dr. Sunil Kumar |
| Generative AI & Foundation Models | 4 | Core | Falguni Thakker (VF) |
| Elective-III (running option): Blockchain Technology | 4 | Elective (3 + 1 lab block) | Dr. Sunil Kumar |
| Internship | 4 | **Outside the timetable — no slots, no OFF days** | — |
| Science of Living III | 2 | SOL — fixed, COMBINED with M.Sc. IT Sem 3 | Ms. Subarna Ghosh |
| PE & Yoga – III (Audit) | 1 | PE — fixed, COMBINED (all M.Sc.) | Mr. Sushant Singh |

## 9. Seeding Order

1. Active term = **Odd 2026**, dates 01 July 2026 – 30 Nov 2026.
2. Confirm School of IT + the 3 courses exist; day-wise schedule per Section 3 (including OFF days).
3. Create all teachers (Section 4) with dummy @school-it.test emails, specializations, visiting days. **No account for Prof. Anurag Dixit.**
4. Create all subjects per class (Sections 6–8) with credits, lab flags, elective groups (track groups parallel), and teacher assignments (Minor 7: Papri Das + co-teacher Maitri Hingu).
5. Leave B.Sc. Sem 3 AEC 3 unassigned (no teacher) — this must not block seeding.
6. Mark B.Sc. Sem 5 and Sem 7 as "no PE/SOL".
7. Create the fixed slots per Section 5, including the COMBINED sessions.
8. Internship/OJT: no subjects-with-slots — OJT is covered by Sem 7's OFF days; M.Sc. Internship has no timetable presence.

# 🏫 COLLEGE SEED DATA — School of IT (Real AURO Data)

> **Claude Code:** this file holds the REAL college data to load into the system after Phase 1 is complete.
> It works together with TIMETABLE_MASTER_PLAN.md (the master plan always wins on rules).
> Items marked ❓ are not confirmed yet — ask the user before seeding those, or seed with the stated default.
> Teacher accounts: use dummy emails like firstname.lastname@school-it.test — NO real emails. Real emails can be set later by the college.

Last updated: 06 Oct 2026 (V2 — user confirmed B.Sc. PE/SOL fixed slots; B.Sc. Sem 5 AND Sem 7 have NO PE and NO SOL at all)

---

## 1. Active Term

- **Odd Term 2026** (July–December 2026).
- Running semesters: Sem 1, 3, 5, 7 (B.Sc. IT) and Sem 1, 3 (M.Sc. IT, M.Sc. AI).
- Semester start/end dates: ❓ (default if not given: 01 July 2026 – 30 Nov 2026).

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

- College hours: 10:00 – 4:50. Lecture = 1 hour. Short break = 5 min. Lunch = 30 min.
- Working days: Monday–Saturday available; **Sunday = holiday**. Most teaching semesters keep **Saturday off** (see day-wise settings below).
- A "double block" = 2 consecutive periods. Note from real timetables: a double block may sit AROUND lunch (Period 3 + Period 4), as SOL III does. The Fixed Slots screen must allow this.

## 3. Day-wise End Times / Off Days (per semester, from real timetables)

| Class | Mon | Tue | Wed | Thu | Fri | Sat |
|---|---|---|---|---|---|---|
| B.Sc. Sem 1 | full (4:50) | full | full | full | full | off |
| B.Sc. Sem 3 | full | full | till 2:40 ❓ | till 2:40 ❓ | till 2:40 ❓ | off |
| B.Sc. Sem 5 | till 2:40 | till 2:40 | till 2:40 | full | full | off |
| B.Sc. Sem 7 | full | full | till 2:40 | **OJT (no classes)** | **OJT** | **OJT** |
| M.Sc. IT Sem 1 | ❓ | ❓ | ❓ | ❓ | ❓ | off ❓ |
| M.Sc. IT Sem 3 | ❓ | ❓ | ❓ | ❓ | ❓ | off ❓ |
| M.Sc. AI Sem 1 | ❓ | ❓ | ❓ | ❓ | ❓ | off ❓ |
| M.Sc. AI Sem 3 | ❓ | ❓ | ❓ | ❓ | ❓ | off ❓ |

(❓ rows/cells: confirm with the user; defaults can be "full day".)

## 4. Teachers (master list)

### HOD
| Name | Role | Specialization | Teaches |
|---|---|---|---|
| Dr. Papri Das | **HOD, School of IT** (also teaches) | IoT, Networks, Mathematics, Quantum | IoT for Industry 4.0, Big Data & IoT, Fundamentals of Mathematics, Computer Networks, Intro to Quantum Computing (shared) |

### Regular Faculty
| Name | Specialization | Teaches |
|---|---|---|
| Dr. Chirag Mehta | Programming (Python, JAVA, Full Stack) | Python Programming, OOP using JAVA (+Lab), Advance JAVA, Full Stack Development (+Lab) |
| Dr. Dhaval Thaker | Data Mining, Databases, DevOps, RL | Data Mining, NO SQL, Reinforcement Learning, Intro to DevOps (+Lab), Science of Living I (B.Sc.) |
| Dr. Juhi Khengar | Web, OS, Databases | RTOS, Advanced Web Development, Fundamentals of DBMS (+Lab), Operating Systems, UI/UX Design (shared) |
| Dr. Sunil Kumar | AI, Blockchain, Big Data | Agentic AI & Autonomous Systems, Blockchain Technology (+Lab), Big Data Analytics (+Lab) |
| Ms. Shreya Chakrabarty | Cyber Security, Mobile | Mobile Computing, Network Security, Cyber Security & Digital Forensics, Digital Literacy, Data Governance & Privacy Engineering |
| Maitri Hingu | ❓ | Shared subjects: SEC 1 Computer Org, UI/UX Design, Intro to Quantum, Software Testing & QA, Cloud AI & MLOps |
| Prof. Anurag Dixit | ❓ | Shared with Maitri Hingu (see ❓ Q2) |
| Ms. Juhikadevi Bhanjdeo | Science of Living | Science of Living I (M.Sc.) |
| Ms. Subarna Ghosh | Science of Living | Science of Living III (M.Sc. + B.Sc.) |
| Dr. Richa Chaudhary | Psychology/Behavior | Dynamics of Human Behavior |
| Dr. Srinita Bhattacharjee | Communication | Communication Skills |
| Prof. Nimesh Joshi | Indian Culture | Foundation of Indian Culture I |
| Mr. Sushant Singh | Physical Education | PE, Sports & Yoga (all semesters) |
| IBM Faculty ❓ | Specialization tracks (AI&ML / Cyber) | All B.Sc. specialization Majors (+Labs) — see ❓ Q4 |

### Visiting Faculty (hard day limits)
| Name | Teaches | Days (from timetables) |
|---|---|---|
| Anjana Prajapati (VF) | AI & Machine Learning (M.Sc. AI Sem 1), Data Structures & Algorithms using C + Lab (B.Sc. Sem 1) | Tue + Wed ❓ confirm |
| Falguni Thakker (VF1) | Generative AI & Foundation Models (M.Sc. AI Sem 3), Data Visualization + Lab (B.Sc. Sem 3), Digital Image Processing + Lab (B.Sc. Sem 7) | Mon + Tue + Wed ❓ confirm |

## 5. Subjects — B.Sc. IT (specialization tracks: AI & ML / Cyber Security & Forensics)

B.Sc. "Major" track subjects work like an ELECTIVE GROUP: the AI-track subject and the Cyber-track subject run in the SAME parallel slots, both by IBM Faculty.
Weekly pattern: theory subject = its credits in lectures; its LAB = 1 double block per week.

### B.Sc. Sem 1 (Batch 2026) — Total 23 credits
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| Major 1 [TRACK GROUP]: Intro to AI & ML with Python (AI) ∥ Information Security Fundamentals (Cyber) | 3 | Elective group, parallel | IBM Faculty |
| Major 1 LAB (both tracks) | 1 | Lab (double block) | IBM Faculty |
| Major 2: Data Structures & Algorithms using C | 3 | Core | Anjana Prajapati (VF) |
| Major 2 LAB | 1 | Lab | Anjana Prajapati (VF) |
| Minor 1: Fundamentals of DBMS | 3 | Core | Dr. Juhi Khengar |
| Minor 1 LAB | 1 | Lab | Dr. Juhi Khengar |
| MD-1: Fundamentals of Mathematics | 2 | Core | Dr. Papri Das |
| MD-2: Dynamics of Human Behavior | 2 | Core | Dr. Richa Chaudhary |
| AEC 1: Communication Skills | 2 | Core | Dr. Srinita Bhattacharjee |
| SEC 1: Computer Organization & Architecture | 2 | Core | Maitri Hingu / Prof. Anurag Dixit ❓ |
| VAC 1: Science of Living I | 2 | **SOL — fixed double block** | Dr. Dhaval Thaker |
| PE, Sports & Yoga – I | 1 | **PE — fixed double block** | Mr. Sushant Singh ❓ |

Fixed slots (CONFIRMED by user): **SOL I = Monday P5+P6 (2:45–4:50)** • **PE I = Tuesday P5+P6 (2:45–4:50)**

### B.Sc. Sem 3 (Batch 2025) — Total 23 credits
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| Major 5 [TRACK GROUP]: Deep Learning (AI) ∥ IT Application & Data Security (Cyber) | 3 | Elective group, parallel | IBM Faculty |
| Major 5 LAB | 1 | Lab | IBM Faculty |
| Major 6: OOP using JAVA | 3 | Core | Dr. Chirag Mehta |
| Major 6 LAB | 1 | Lab | Dr. Chirag Mehta |
| Major 7: Operating Systems | 4 | Core | Dr. Juhi Khengar |
| SEC 3: Data Visualization | 1 | Core | Falguni Thakker (VF) |
| SEC 3 LAB | 1 | Lab | Falguni Thakker (VF) |
| MD 5: Computer Networks | 2 | Core | Dr. Papri Das |
| MD 6: Digital Literacy | 2 | Core | Ms. Shreya Chakrabarty |
| AEC 3: Communication Skills / French / Sanskrit / Gujarati / Hindi | 2 | Core | ❓ teacher not specified |
| VAC 3: Science of Living III | 2 | **SOL — fixed double block** | Ms. Subarna Ghosh |
| PE, Sports & Yoga – III | 1 | **PE — fixed double block** | Mr. Sushant Singh |

Fixed slots (CONFIRMED by user): **PE III = Monday P5+P6 (2:45–4:50)** • **SOL III = Thursday P3+P4 (12:10–1:10 + 1:40–2:40, lunch in between)**

### B.Sc. Sem 5 (Batch 2024) — Total 24 credits (no PE/SOL this sem)
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| Major 11 [TRACK GROUP]: Pattern Recognition & Anomaly Detection / LLM Dev & Deployment (AI) ∥ Digital Forensics (Cyber) | 3 | Elective group, parallel | IBM Faculty |
| Major 11 LAB | 1 | Lab | IBM Faculty |
| Major 12: Blockchain Technology | 3 | Core | Dr. Sunil Kumar |
| Major 12 LAB | 1 | Lab | Dr. Sunil Kumar |
| Major 13: Full Stack Development | 3 | Core | Dr. Chirag Mehta |
| Major 13 LAB | 1 | Lab | Dr. Chirag Mehta |
| Minor 4: Data Governance & Privacy Engineering | 4 | Core | Ms. Shreya Chakrabarty |
| Minor 5: Introduction to DevOps | 3 | Core | Dr. Dhaval Thaker |
| Minor 5 LAB | 1 | Lab | Dr. Dhaval Thaker |
| SEC 5: UI/UX Design | 1 | Core | Maitri Hingu / Dr. Juhi Khengar ❓ |
| SEC 5 LAB | 1 | Lab | Maitri Hingu / Dr. Juhi Khengar ❓ |
| VAC 5: Foundation of Indian Culture – I | 2 | Core | Prof. Nimesh Joshi |

### B.Sc. Sem 7 (Batch 2023) — Total 22 credits — classes only Mon–Wed, Thu–Sat = OJT — NO PE/SOL (confirmed)
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| Major 17 [TRACK GROUP]: Pattern Recognition & Anomaly Detection (AI) ∥ Cryptography & Web App Security (Cyber) | 3 | Elective group, parallel | IBM Faculty |
| Major 17 LAB | 1 | Lab | IBM Faculty |
| Major 18: Big Data Analytics | 3 | Core | Dr. Sunil Kumar |
| Major 18 LAB | 1 | Lab | Dr. Sunil Kumar |
| Major 19: Digital Image Processing | 3 | Core | Falguni Thakker (VF) |
| Major 19 LAB | 1 | Lab | Falguni Thakker (VF) |
| Minor 7: Introduction to Quantum Computing | 4 | Core | Dr. Papri Das / Maitri Hingu ❓ |
| OJT 1: On the Job Training – Major Project II | 6 | **OJT — blocks Thu+Fri+Sat fully, no slots needed** | — |

## 6. Subjects — M.Sc. IT

### M.Sc. IT Sem 1 — Total 23 credits
(4-credit subjects here are L=3 + P=2 style: 3 theory lectures + 1 practical double block per week, same teacher)
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| Advanced Web Development | 4 | Core (3 theory + 1 lab block) | Dr. Juhi Khengar |
| NO SQL | 4 | Core (3 theory + 1 lab block) | Dr. Dhaval Thaker |
| Mobile Computing | 4 | Core (4 theory) | Ms. Shreya Chakrabarty |
| Elective-I [GROUP]: Software Engg & PM (IT) ∥ Statistical Computing (DS) ∥ **Network Security (Cyber)** | 4 | Elective group, parallel (3+1 lab block) | Network Security → Ms. Shreya Chakrabarty; others ❓ not running? |
| Elective-II [GROUP]: **Advanced Python (IT)** ∥ Data Analysis & Viz (DS) ∥ Web App Security (Cyber) | 4 | Elective group, parallel (3+1 lab block) | Advanced Python → Dr. Chirag Mehta ❓; others ❓ |
| Science of Living I | 2 | **SOL — fixed double block** | Ms. Juhikadevi Bhanjdeo |
| PE & Yoga – I (Audit) | 1 | **PE — fixed double block** | Mr. Sushant Singh ❓ |

Fixed slot (user-given): **SOL I = Wednesday P5+P6 (2:45–4:50)** — combined for all Sem 1 classes ❓ confirm

### M.Sc. IT Sem 3 — Total 27 credits
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| Big Data & IoT | 4 | Core (3+1 lab block) | Dr. Papri Das |
| Software Testing & QA | 4 | Core (4 theory) | Maitri Hingu / Prof. Anurag Dixit ❓ |
| Advance JAVA Programming | 4 | Core (3+1 lab block) | Dr. Chirag Mehta |
| Elective-V [GROUP]: **Cyber Security & Digital Forensics (IT)** ∥ Data Analytics using R (DS) ∥ Ethical Hacking (Cyber) | 4 | Elective group, parallel (3+1 lab block) | CS&DF → Ms. Shreya Chakrabarty; others ❓ |
| Elective-VI [GROUP]: **Blockchain Technology (IT)** ∥ Data Warehousing & Mining (DS) ∥ Digital Forensics (Cyber) | 4 | Elective group, parallel (3+1 lab block) | Blockchain → Dr. Sunil Kumar; others ❓ |
| Internship | 4 | ❓ in timetable or outside? | — |
| Science of Living III | 2 | **SOL — fixed double block** | Ms. Subarna Ghosh |
| PE & Yoga – III (Audit) | 1 | **PE — fixed double block** | Mr. Sushant Singh ❓ |

Fixed slot (user-given): **SOL III = Thursday P3+P4 (12:10–2:40 window, lunch in between)** — combined for all Sem 3 classes ❓ confirm

## 7. Subjects — M.Sc. AI

### M.Sc. AI Sem 1 — Total 23 credits
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| AI & Machine Learning | 4 | Core (3 theory + tutorial) | Anjana Prajapati (VF) |
| Python Programming | 3 | Core | Dr. Chirag Mehta |
| Python Programming LAB | 1 | Lab | Dr. Chirag Mehta |
| Internet of Things for Industry 4.0 | 3 | Core | Dr. Papri Das |
| IoT LAB | 1 | Lab | Dr. Papri Das |
| Real Time Operating System | 4 | Core | Dr. Juhi Khengar |
| Elective-I [GROUP]: **Data Mining** ∥ Business Intelligence | 4 | Elective group, parallel (3+1 lab block) | Data Mining → Dr. Dhaval Thaker; BI ❓ not running? |
| Science of Living I | 2 | **SOL — fixed double block, Wed P5+P6** | Ms. Juhikadevi Bhanjdeo |
| PE, Sports & Yoga – I (Audit) | 1 | **PE — fixed double block** ❓ slot | Mr. Sushant Singh ❓ |

### M.Sc. AI Sem 3 — Total 27 credits
| Subject | Cr | Type | Teacher |
|---|---|---|---|
| Reinforcement Learning | 4 | Core | Dr. Dhaval Thaker |
| Cloud AI and MLOps | 3 | Core | Maitri Hingu / Prof. Anurag Dixit ❓ |
| Cloud AI & MLOps LAB | 1 | Lab | same teacher ❓ |
| Agentic AI & Autonomous Systems | 3 | Core | Dr. Sunil Kumar |
| Agentic AI LAB | 1 | Lab | Dr. Sunil Kumar ❓ |
| Generative AI & Foundation Models | 4 | Core | Falguni Thakker (VF) |
| Elective-III [GROUP]: **Blockchain Technology** ∥ Quantum Computing for AI | 4 | Elective group, parallel (3+1 lab block) | Blockchain → Dr. Sunil Kumar; Quantum ❓ not running? |
| Internship | 4 | ❓ in timetable or outside? | — |
| Science of Living III | 2 | **SOL — fixed double block, Thu P3+P4** | Ms. Subarna Ghosh |
| PE & Yoga – III (Audit) | 1 | **PE — fixed double block** ❓ slot | Mr. Sushant Singh ❓ |

## 8. Seeding Rules (for the seed step)

1. Create School of IT with HOD = Dr. Papri Das (she also teaches — teacher mode applies).
2. Create all teachers above with dummy @school-it.test emails, specializations, and visiting flags/days.
3. Create 3 courses + the 8 active-term classes with all subjects, credits, lab flags, and elective/track groups exactly as above.
4. Mark PE and SOL subjects with the PE/SOL rule (master plan Section 8 — always 2-lecture double block, credits never decide count).
5. Enter the timing structure (Section 2), day-wise end times/off days (Section 3), and the known fixed slots.
6. Where ❓ remains, ask the user first (or use the stated default).
7. Active term = Odd 2026.

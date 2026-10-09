export type Role = 'admin' | 'hod' | 'teacher'

export interface User {
  id: string
  email: string
  full_name: string
  role: Role
  school_id: string | null
  school_name: string | null
  specialization: string | null
  phone: string | null
  photo_url: string | null
  is_visiting: boolean
  is_active: boolean
  must_change_password: boolean
}

/** Durations (seconds), not clock times - so a wrong computer clock can never confuse the timers. */
export interface SessionInfo {
  remember_me: boolean
  idle_timeout_seconds: number
  idle_expires_in_seconds: number
  absolute_expires_in_seconds: number
}

export interface AuthPayload {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: User
  session: SessionInfo
}

export interface MePayload {
  user: User
  session: SessionInfo
}

export interface Notice {
  tone: 'info' | 'warning' | 'success'
  message: string
}

// =====================================================================================
//  Phase 1 - shapes of what the API sends (the Python backend is the single source of truth)
// =====================================================================================
export interface Term {
  id: string
  kind: 'odd' | 'even'
  year: number
  name: string
  is_active: boolean
  activated_at: string | null
}

export interface School {
  id: string
  name: string
  is_active: boolean
}

export interface SemesterInfo {
  id: string
  number: number
  running: boolean
}

export interface CourseInfo {
  id: string
  name: string
  code: string | null
  num_semesters: number
  is_active: boolean
  semesters: SemesterInfo[]
}

export interface SchoolTree extends School {
  hods: number
  hod_names: string[]
  teachers: number
  courses: CourseInfo[]
}

export interface StructureTree {
  active_term: Term | null
  schools: SchoolTree[]
}

export interface Account {
  id: string
  email: string
  full_name: string
  role: 'hod' | 'teacher'
  school_id: string | null
  school_name: string | null
  specialization: string | null
  phone: string | null
  photo_url: string | null
  is_visiting: boolean
  visiting_days: number[]
  max_weekly_hours: number | null
  is_active: boolean
  must_change_password: boolean
  created_at: string | null
  subjects_count: number
  subject_ids?: string[]
}

export interface CreatedAccount {
  user: Account
  temp_password: string
}

export interface SubjectRow {
  id: string
  semester_id: string
  course_id: string
  course_name: string
  semester_number: number
  class_label: string
  name: string
  code: string
  credits: number
  semester_hours: number
  is_lab: boolean
  is_elective: boolean
  special_kind: 'PE' | 'SOL' | null
  elective_group_id: string | null
  elective_group_name: string | null
  assignment_id: string | null
  teacher_id: string | null
  teacher_name: string | null
  co_teacher_id: string | null
  co_teacher_name: string | null
}

export interface ElectiveGroup {
  id: string
  name: string
  semester_id: string
  class_label: string
  members: { id: string; name: string; code: string; credits: number }[]
  warnings: string[]
}

export interface GridPeriod {
  number: number
  start: string
  end: string
}

export interface GridInfo {
  periods: GridPeriod[]
  break_minutes: number
  lunch: { start: string; end: string } | null
  college_start: string
  college_end: string
  notes: string[]
}

export interface DayInfo {
  day: number
  name: string
  short: string
  working: boolean
  is_off: boolean
  end_time: string | null
  periods_available: number
}

export interface ClassTiming {
  semester_id: string
  course: string
  number: number
  label: string
  days: DayInfo[]
}

export interface CollegeTiming {
  college_start: string
  college_end: string
  lecture_minutes: number
  break_minutes: number
  days_per_week: number
}

export interface TimingPayload {
  college: CollegeTiming
  school: { id: string; name: string; lunch_start: string | null; lunch_end: string | null } | null
  grid: GridInfo
  classes: ClassTiming[]
  active_term: Term | null
}

export interface FixedSlot {
  id: string
  semester_id: string
  /** all covered classes joined with " + " (one name for a normal slot) */
  class_label: string
  /** a COMBINED session covers several classes together (one teacher, one session) */
  semester_ids: string[]
  classes: { semester_id: string; class_label: string; subject_id: string | null; subject_name: string | null }[]
  combined: boolean
  day: number
  day_name: string
  start_period: number
  length: number
  kind: 'PE' | 'SOL' | 'OTHER'
  label: string
  subject_id: string | null
  subject_name: string | null
  teacher_id: string | null
  teacher_name: string | null
  co_teacher_id: string | null
  co_teacher_name: string | null
  note: string | null
  time: string | null
  notes: string[]
  problems: string[]
}

export interface FixedSlotsPayload {
  active_term: Term | null
  grid: GridInfo
  days_per_week: number
  classes: { semester_id: string; label: string; days: DayInfo[]; has_pe: boolean; has_sol: boolean; no_pe_sol: boolean }[]
  slots: FixedSlot[]
}

export interface CalendarEntry {
  id: string
  scope: 'college' | 'school'
  school_id: string | null
  school_name: string | null
  kind: 'holiday' | 'event'
  title: string
  date_from: string
  date_to: string
  full_day: boolean
  start_time: string | null
  end_time: string | null
  note: string | null
  is_preloaded: boolean
  classes: { semester_id: string; label: string }[]
  can_edit: boolean
}

export interface AssignmentTeacher {
  id: string
  name: string
  role: 'hod' | 'teacher'
  is_visiting: boolean
  visiting_days: number[]
  max_weekly_hours: number | null
  weekly_load: number
}

export interface AssignmentSubject extends SubjectRow {
  eligible_teachers: { id: string; name: string }[]
}

export interface ImportRow {
  row: number
  label: string
  status: 'ok' | 'error' | 'imported' | 'failed'
  errors: string[]
}

export interface ImportResult {
  dry_run: boolean
  total: number
  valid: number
  invalid: number
  imported: number
  failed: number
  rows: ImportRow[]
  credentials?: { row: number; name: string; email: string; temp_password: string }[]
  warnings?: string[]
}

export interface ProfilePayload {
  profile: Account
  available_subjects: { id: string; name: string; code: string; course: string; semester: number; running: boolean; label: string }[]
}

// ---------------------------------------------------------------------- Phase 2: timetable drafts
export interface DraftSummary {
  id: string
  name: string
  note: string | null
  is_primary: boolean
  updated_at: string
  placements: number
  percent: number
  required: number
  placed: number
  missing: number
  clashes: number
  warnings: number
}

export interface DraftClassRow {
  semester_id: string
  label: string
  subjects: number
  locked_sessions: number
  drafts: DraftSummary[]
}

export interface DraftsPayload {
  active_term: Term | null
  classes: DraftClassRow[]
  max_drafts_per_class?: number
}

export interface PlacementEntry {
  id: string
  subject_id: string
  subject_name: string
  subject_code: string
  is_lab: boolean
  teacher_id: string | null
  teacher_name: string | null
  co_teacher_id: string | null
  co_teacher_name: string | null
}

export interface Placement {
  key: string
  day: number
  start_period: number
  length: number
  time: string | null
  parallel: boolean
  group_name: string | null
  is_lab: boolean
  entries: PlacementEntry[]
  problems: string[]
  warnings: string[]
}

export interface LockedItem {
  key: string
  day: number
  start_period: number
  length: number
  kind: 'PE' | 'SOL' | 'OTHER'
  label: string
  time: string | null
  teacher_name: string | null
  co_teacher_name: string | null
  combined: boolean
  classes: string[]
}

export type PlaceStatus = 'missing' | 'complete' | 'over'

export interface PaletteSubject {
  id: string
  name: string
  code: string
  credits: number
  is_lab: boolean
  special_kind: 'PE' | 'SOL' | null
  group_id: string | null
  group_name: string | null
  teacher_name: string | null
  co_teacher_name: string | null
  required: number
  placed: number
  status: PlaceStatus
}

export interface PaletteGroup {
  id: string
  name: string
  member_ids: string[]
  required: number
  placed: number
  status: PlaceStatus
  kind: string
  is_lab: boolean
}

export interface DraftDetail {
  draft: { id: string; name: string; note: string | null; is_primary: boolean; semester_id: string; class_label: string; updated_at: string | null }
  siblings: { id: string; name: string; is_primary: boolean }[]
  active_term: Term
  grid: GridInfo
  days: DayInfo[]
  locked: LockedItem[]
  placements: Placement[]
  subjects: PaletteSubject[]
  groups: PaletteGroup[]
  completeness: { required: number; placed: number; percent: number; missing: number }
  problems: { clashes: number; warnings: number }
  no_pe_sol: boolean
}

export interface CellOption {
  state: 'ok' | 'warn' | 'blocked'
  hard: string[]
  soft: string[]
}

export interface OptionsPayload {
  unit: { subject_ids: string[]; names: string[]; length: number }
  cells: Record<string, CellOption>
  swaps: Record<string, { ok: boolean; reasons: string[]; warnings: string[] }>
}

export interface ConflictItem {
  id: string
  kind: 'clash' | 'missing' | 'warning'
  code: string
  message: string
  semester_id: string
  class_label: string
  draft_id: string | null
  draft_name: string | null
  is_primary: boolean
  day: number | null
  day_name: string | null
  start_period: number | null
  length: number | null
  placement_key: string | null
  subjects: string | null
}

export interface ConflictsPayload {
  active_term: Term | null
  scope?: 'primary' | 'all'
  items: ConflictItem[]
  counts: { clashes: number; missing: number; warnings: number }
  classes: { semester_id: string; label: string }[]
}

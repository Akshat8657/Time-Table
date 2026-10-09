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
  /** the HOD's override of the lectures needed per week (null = same as credits) */
  weekly_lectures: number | null
  /** what the timetable really needs: the override, or the credits */
  weekly_effective: number
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
  college_name?: string | null
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
  /** pinned by hand (semi-automatic mode): the generator keeps it, the editor refuses to move it */
  is_locked: boolean
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
  /** lectures needed per week (the credits unless the HOD set a weekly-lectures value) */
  weekly_lectures: number
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

// ---------------------------------------------------------------------- Phase 3: automatic generation
export interface GenRule {
  name: string
  label: string
  default: number
}

export interface WeightsPayload {
  weights: Record<string, number>
  rules: GenRule[]
}

export interface GenRuleScore {
  rule: string
  label: string
  weight: number
  satisfaction: number
  points_lost: number
  /** Phase 6: for the teachers' preferred-slots rule - what was honoured, and for whom */
  detail?: string
  teachers?: { teacher_id: string; name: string; stars: [number, number] | null; first: [number, number] | null; last: [number, number] | null }[]
}

export interface GenClassResult {
  label: string
  percent: number
  placed: number
  required: number
  missing: number
}

export interface GenUnplaced {
  class_label: string
  unit: string
  missing: number
  needed: number
  message: string
}

export interface GenSolution {
  id: string
  label: string
  score: number
  is_used: boolean
  in_use: boolean
  complete: boolean
  breakdown: {
    rules: GenRuleScore[]
    classes: Record<string, GenClassResult>
    unplaced: GenUnplaced[]
    placed: number
    required: number
  }
  drafts: { draft_id: string; semester_id: string; name: string }[]
}

export interface GenFinding {
  kind: string
  about: string
  message: string
}

export interface GenRun {
  id: string
  name: string
  mode: 'fresh' | 'keep_locked'
  status: 'running' | 'done' | 'failed' | 'cancelled'
  progress: number
  message: string | null
  error: string | null
  precheck: GenFinding[]
  weights: Record<string, number>
  created_at: string
  finished_at: string | null
  solutions: GenSolution[]
}

export interface GenRunsPayload {
  active_term: Term | null
  runs: GenRun[]
}

export interface CopyableTerm {
  id: string
  name: string
  kind: 'odd' | 'even'
  primaries: number
  drafts: number
  same_kind: boolean
}

// ====================================================================================================== Phase 4
export interface PublishClass {
  semester_id: string
  class_label: string
  primary: { id: string; name: string } | null
  published: { version: number; published_at: string; published_by: string | null; source_draft_name: string | null; rows: number; lectures: number } | null
  state: 'no_draft' | 'not_published' | 'in_sync' | 'changed' | 'draft_removed'
  changes: { added_count: number; removed_count: number; added: string[]; removed: string[] } | null
  blockers: string[]
  blocker_count: number
  completeness: { required: number; placed: number; percent: number; missing: number } | null
  can_publish: boolean
}

export interface PublishStatus {
  active_term: Term | null
  classes: PublishClass[]
  summary: Partial<Record<'no_draft' | 'not_published' | 'in_sync' | 'changed' | 'draft_removed' | 'unpublished' | 'can_publish', number>>
}

export interface PublishResult {
  event: { id: string; number: number; scope: string }
  published: { semester_id: string; class_label: string; version: number; lectures: number; added: number; removed: number; first: boolean }[]
  skipped: { semester_id: string; class_label: string; reasons: string[]; up_to_date: boolean }[]
  notified: number
  teachers_changed: number
  message: string
}

export interface PublishEvent {
  id: string
  number: number
  scope: 'all' | 'class'
  published_at: string
  published_by: string | null
  notified: number
  classes: { class_label: string; version: number; lectures: number; added: number; removed: number; first: boolean }[]
}

export interface ViewItem {
  subject_id: string | null
  title: string
  code: string | null
  is_lab: boolean
  teacher_id: string | null
  teacher_name: string | null
  co_teacher_id: string | null
  co_teacher_name: string | null
  // Phase 5: when the teacher of this item is absent that date
  absent?: boolean
  absent_teacher_name?: string
  substitute_id?: string
  substitute_name?: string
}

export interface ViewCell {
  key: string
  kind: 'lecture' | 'fixed' | 'extra' | 'substitution'
  fixed_kind: string | null
  day: number
  day_name: string
  start_period: number
  length: number
  time: string | null
  items: ViewItem[]
  classes: { id: string; label: string }[]
  combined: boolean
  combined_with: string | null
  parallel: boolean
  parallel_with?: string[]
  is_lab: boolean
  date: string | null
  status: 'holiday' | 'event' | 'not_running' | 'cancelled' | null
  status_note: string | null
  extra: { id: string; date: string; reason: string | null } | null
  // Phase 5 overlay
  substitutions?: { id: string; status: string; absent_id: string; absent_name: string | null; substitute_id: string | null; substitute_name: string | null; subjects: string[] }[]
  substitution?: { for_id: string; for_name: string | null }
  adjusted?: { kind: 'move' | 'cancel'; from?: number; to?: number; reason: string | null; swap?: boolean }
  moved_from?: number
  moved_from_time?: string
  mine_absent?: boolean
}

export interface DayBanner {
  id: string
  kind: 'holiday' | 'event'
  title: string
  full_day: boolean
  window: string
}

export interface DayHeader {
  day: number
  name: string
  short: string
  date: string
  is_today: boolean
  banners: DayBanner[]
}

export interface ViewStats {
  lectures: number
  hours: number
  cancelled: number
  extra: number
}

export interface DayStats {
  covering: number
  my_absent: number
  needs_sub: number
  cancelled_today: number
  moved: number
}

export interface PublishedInfo {
  term: Term | null
  published: boolean
  message?: string
  publications?: { semester_id: string; class_label: string; version: number; published_at: string; published_by: string | null; source_draft_name: string | null }[]
  last_published_at?: string | null
}

export interface WeekView extends PublishedInfo {
  monday: string
  days: DayHeader[]
  grid: GridInfo
  cells: ViewCell[]
  stats: ViewStats
  day_stats?: DayStats
  owner?: { id: string; name: string; school?: string }
  off_days?: Record<string, Record<string, { is_off: boolean; end_time: string | null }>>
}

export interface TodayView extends PublishedInfo {
  date: string
  weekday: number
  weekday_name: string
  is_today: boolean
  is_working_day: boolean
  banners: DayBanner[]
  grid: GridInfo
  cells: ViewCell[]
  stats: ViewStats
  day_stats?: DayStats
  current_key: string | null
  next_key: string | null
  week_lectures: number
}

export interface MasterView extends WeekView {
  scope: 'week' | 'today'
  date: string | null
  day: number | null
  classes: { id: string; label: string; published: boolean }[]
  filters: { semester_id: string | null; teacher_id: string | null; day: number | null }
  school: { id: string; name: string }
}

export interface ViewOptions {
  term: Term | null
  published: boolean
  classes: { id: string; label: string; published: boolean; version: number | null; published_at: string | null }[]
  teachers: { id: string; name: string; role: string; is_active: boolean; is_visiting: boolean; lectures: number }[]
}

export interface NotificationItem {
  id: string
  kind: string
  title: string
  body: string | null
  link: string | null
  data: Record<string, unknown>
  is_read: boolean
  read_at: string | null
  created_at: string
}

export interface NotificationsPayload {
  notifications: NotificationItem[]
  unread: number
  has_more: boolean
}

export interface PushConfig {
  enabled: boolean
  public_key: string | null
  devices: number
}

export interface HoursSubject {
  subject_id: string
  semester_id: string
  class_label: string
  name: string
  code: string
  is_lab: boolean
  special_kind: 'PE' | 'SOL' | null
  group_id: string | null
  teachers: { id: string; name: string }[]
  required: number
  weekly: number
  scheduled: number
  lost: number
  extra: number
  deliverable: number
  delivered: number
  remaining: number
  shortfall: number
  surplus: number
  status: 'ok' | 'short' | 'unpublished' | 'no_dates'
  note: string | null
  extra_lectures_needed: number
  percent_delivered: number
  percent_planned: number
  dates?: { start: string; end: string } | null
}

export interface HoursSummary {
  subjects: number
  short_subjects: number
  short_hours: number
  unpublished_classes: number
  worst: { subject_id: string; name: string; class_label: string; shortfall: number }[]
}

export interface HoursClass {
  semester_id: string
  class_label: string
  published: boolean
  dates: { start: string; end: string } | null
  short_hours: number
  short_subjects: number
  subjects: HoursSubject[]
}

export interface HoursPayload {
  term: Term | null
  today: string
  classes: HoursClass[]
  summary: HoursSummary
}

export interface MyHoursPayload {
  term: Term | null
  today: string
  subjects: HoursSubject[]
  summary: HoursSummary
}

export interface ExtraSlot {
  date: string
  weekday: number
  weekday_name: string
  start_period: number
  length: number
  time: string | null
  score: number
  reasons: string[]
  subjects: { id: string; name: string }[]
  class_label: string
}

export interface SuggestPayload {
  subject: HoursSubject
  needed: number
  suggestions: ExtraSlot[]
  unfilled: number
  message: string
  group: { id: string; name: string }[] | null
}

export interface ExtraLecture {
  id: string
  semester_id: string
  class_label: string
  subjects: string[]
  date: string
  start_period: number
  length: number
  time: string | null
  reason: string | null
  teacher_name: string | null
  can_cancel: boolean
}

// ====================================================================================================== Phase 5
export interface AffectedSlot {
  id?: string
  start_period: number
  length: number
  label: string
  time: string | null
  classes: string[]
  status: 'open' | 'covered' | 'exists'
  note?: string
}

export interface MarkAbsentResult {
  absence_id: string
  teacher_id: string
  teacher_name: string | null
  date: string
  affected: AffectedSlot[]
  to_cover: number
  message: string
}

export interface MyAbsence {
  id: string
  date: string
  reason: string | null
  source: string
  past: boolean
}

export interface LeaveRequest {
  id: string
  teacher_id: string
  teacher_name: string | null
  date_from: string
  date_to: string
  days: number
  reason: string | null
  status: 'pending' | 'approved' | 'rejected' | 'cancelled'
  decided_by: string | null
  decided_by_name: string | null
  decided_at: string | null
  decision_note: string | null
  created_at: string
}

export interface AbsenceRow {
  id: string
  teacher_id: string
  teacher_name: string | null
  date: string
  reason: string | null
  source: string
  open: number
  offering: number
  filled: number
  unfilled: number
}

export interface Substitution {
  id: string
  lecture_date: string
  start_period: number
  length: number
  time: string | null
  label: string
  classes: string[]
  absent_teacher_id: string
  absent_teacher_name: string | null
  status: 'open' | 'offering' | 'filled' | 'unfilled' | 'cancelled'
  mode: 'manual' | 'auto' | null
  substitute_id: string | null
  substitute_name: string | null
  offer_deadline: string | null
  candidates?: number | null
}

export interface SubCandidate {
  teacher_id: string
  name: string
  score: number
  reasons: string[]
  load_today: number
  can_teach: boolean
  taught_before: boolean
  is_visiting: boolean
  /** Phase 6: the teacher marked THIS period "not available" - listed last, offered only as a last resort */
  marked_unavailable?: boolean
  starred?: boolean
  over_daily_max?: boolean
}

export interface SubOffer extends Substitution {
  offer_id: string
  reasons: string[]
  expires_at: string
}

export interface MyOffersPayload {
  offers: SubOffer[]
  covering: Substitution[]
}

export interface AdjustSuggestion {
  kind: 'move' | 'swap'
  new_start?: number
  swap_with_start?: number
  time: string | null
  reasons: string[]
  direction?: string
  with_label?: string
}

export interface AdjustSuggestPayload {
  lecture: { label: string; start_period: number; length: number; time: string | null }
  moves: AdjustSuggestion[]
  swaps: AdjustSuggestion[]
  message: string
}

export interface MonthlySummary {
  month: string
  month_label: string
  school: { id: string; name: string }
  teachers: { teacher_id: string; name: string; absences: number; covered: number; needed_cover: number }[]
  totals: { absences: number; covered: number; filled: number; unfilled: number }
}

// ---------------------------------------------------------------------- Phase 6: preferences, reports, audit
export type PrefKind = 'preferred' | 'unavailable'

export interface PrefWindow {
  id: string
  status: 'open' | 'closed'
  /** open AND the deadline has not passed */
  is_open: boolean
  opened_at: string
  deadline: string
  deadline_text: string
  closed_at: string | null
  closed_reason: 'manual' | 'deadline' | null
  reopened_count: number
  note: string | null
  seconds_left: number
}

export interface PrefPayload {
  teacher: { id: string; name: string; is_visiting: boolean; visiting_days: number[]; specialization: string | null }
  grid: GridInfo
  days: number
  periods: number
  locked_days: number[]
  /** "day:period" -> kind; a slot that is not here is plain Available */
  slots: Record<string, PrefKind>
  prefer_first: boolean
  avoid_last: boolean
  max_per_day: number | null
  limit: number
  unavailable_count: number
  preferred_count: number
  summary: string
  /** locked sessions (PE / SOL / combined) that fall on a 'not available' slot or pass the daily maximum - they stay as they are */
  fixed_notes: string[]
  window: PrefWindow | null
  can_edit: boolean
  reason: string | null
  submitted: boolean
}

export interface PrefTeacherRow {
  id: string
  name: string
  email: string
  is_visiting: boolean
  filled: boolean
  submitted_at: string | null
  has_prefs: boolean
  unavailable: number
  preferred: number
  max_per_day: number | null
  prefer_first: boolean
  avoid_last: boolean
  summary: string
  over_limit: boolean
}

export interface PrefOverview {
  school: { id: string; name: string }
  settings: { max_unavailable: number; reminder_hours: number }
  window: PrefWindow | null
  filled: number
  total: number
  teachers: PrefTeacherRow[]
  history: { id: string; status: string; opened_at: string; deadline: string; closed_reason: string | null; filled: number }[]
}

export type ReportName = 'workload' | 'rankings' | 'free-teachers' | 'hours' | 'cancelled' | 'substitutions' | 'compare'

export interface ReportScope {
  schools: { id: string; name: string }[]
  all: boolean
}

export interface ReportBase {
  report: string
  title: string
  term: Term | null
  scope: ReportScope
  scope_text: string
  meta: string
  empty_text?: string
}

export interface WorkloadRow {
  teacher_id: string
  teacher: string
  school: string
  school_id: string
  role: string
  is_visiting: boolean
  specialization: string | null
  periods: number
  hours: number
  lectures: number
  teaching_days: number
  max_day: number
  capacity: number
  free_periods: number
  norm_hours: number
  load_pct: number
  status: 'under' | 'within' | 'over' | 'visiting' | 'none'
  visiting_limit: number | null
  subs_covered: number
}

export interface WorkloadSummary {
  teachers: number
  regular: number
  visiting: number
  total_hours: number
  avg_hours: number
  avg_pct: number
  under: number
  within: number
  over: number
  published: boolean
  norm_hours: number | null
  min_hours: number | null
  norm_label: string
  norm_source: string
}

export interface WorkloadReport extends ReportBase {
  rows: WorkloadRow[]
  summary: WorkloadSummary
  norm: { norm_hours: number; min_hours: number; label: string; source: string } | null
  per_school: { school_id: string; school: string; teachers: number; avg_hours: number; avg_pct: number; under: number; within: number; over: number; norm_hours: number }[]
  note?: string
}

export interface RankRow {
  rank: number
  teacher_id: string
  teacher: string
  school: string
  role: string
  hours?: number
  lectures?: number
  teaching_days?: number
  max_day?: number
  load_pct?: number
  status?: string
  free_periods?: number
  capacity?: number
  subs_covered?: number
}

export interface RankingsReport extends ReportBase {
  lists: { busiest: RankRow[]; most_free: RankRow[]; substitutes: RankRow[] }
  summary: WorkloadSummary
}

export interface FreeTeacher {
  teacher_id: string
  name: string
  school: string
  school_id: string
  role: string
  is_visiting: boolean
  specialization: string | null
  free_periods: { period: number; time: string | null }[]
  free_mask: boolean[]
  free_count: number
  load_today: number
  marked_unavailable: boolean
  free_text: string
}

export interface FreeReport extends ReportBase {
  date: string
  weekday: number
  now: boolean
  period: number | null
  rows: FreeTeacher[]
  schools: { id: string; name: string; periods: { period: number; time: string | null }[]; time: string | null; period: number | null; banners: { title: string; kind: string }[] }[]
  notes: string[]
  summary: { free: number; date: string }
}

export interface HoursReportRow {
  school: string
  school_id: string
  class: string
  semester_id: string
  subject: string
  code: string
  teachers: string
  required: number
  delivered: number
  deliverable: number
  shortfall: number
  lost: number
  extra: number
  percent_delivered: number
  percent_planned: number
  status: string
  state: string
}

export interface HoursReportClass {
  school: string
  class: string
  required: number
  delivered: number
  deliverable: number
  /** hours counted toward the requirement (each subject capped at what it needs) */
  taught: number
  planned: number
  shortfall: number
  percent_delivered: number
  percent_planned: number
  short_subjects: number
  subjects: number
}

export interface HoursReport extends ReportBase {
  today: string
  rows: HoursReportRow[]
  classes: HoursReportClass[]
  summary: {
    subjects: number
    short_subjects: number
    short_hours: number
    required: number
    delivered: number
    deliverable: number
    taught: number
    planned: number
    percent_delivered: number
    percent_planned: number
    classes: number
    unpublished_classes: number
  }
}

export interface CancelledReport extends ReportBase {
  rows: { date: string; weekday: string; school: string; class: string; subject: string; period: number; time: string; length: number; teachers: string; reason: string; by: string }[]
  per_day: { day: number; label: string; count: number }[]
  lost: { school: string; class: string; lost_hours: number; cancelled_by_hod: number }[]
  summary: {
    total: number
    month: string
    month_label: string
    top_teachers: { name: string; count: number }[]
    top_classes: { name: string; count: number }[]
    lost_hours_total: number
  }
}

export interface SubstitutionsReport extends ReportBase {
  rows: { teacher: string; school: string; absent_days: number; lectures_needing_cover: number; subs_covered: number; periods_covered: number }[]
  trend: { month: string; label: string; needs: number; filled: number }[]
  summary: {
    month: string
    month_label: string
    absences: number
    absent_teachers: number
    needs: number
    filled: number
    unfilled: number
    cancelled: number
    fill_rate: number
    filled_auto: number
    filled_manual: number
    avg_minutes_to_fill: number | null
  }
}

export interface CompareRow {
  school: string
  school_id: string
  teachers: number
  classes: number
  published_classes: number
  avg_hours: number
  avg_pct: number
  under: number
  within: number
  over: number
  norm_hours: number
  short_subjects: number
  short_hours: number
  absences: number
  sub_needs: number
  sub_filled: number
  fill_rate: number
  cancelled: number
  pref_filled: number | null
  pref_pct: number | null
}

export interface CompareReport extends ReportBase {
  rows: CompareRow[]
  month: string
  month_label: string
}

export interface NormInfo {
  college: { norm_hours: number; min_hours: number; label: string | null } | null
  school: { norm_hours: number; min_hours: number; label: string | null } | null
  effective: { norm_hours: number; min_hours: number; label: string; source: string }
  built_in: { norm_hours: number; min_hours: number }
}

export interface AuditEntry {
  id: number
  at: string
  action: string
  entity: string
  entity_id: string | null
  summary: string
  details: Record<string, unknown> | null
  via: 'ui' | 'ai' | 'system'
  actor: { id: string | null; name: string; email: string | null; role: string | null }
  school: { id: string; name: string } | null
}

export interface AuditPage {
  entries: AuditEntry[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface AuditFacets {
  actions: { value: string; count: number }[]
  entities: { value: string; count: number }[]
  people: { id: string; name: string; email: string | null; role: string | null; count: number }[]
  schools: { id: string; name: string }[]
  vias: string[]
  total: number
  first: string | null
  last: string | null
}

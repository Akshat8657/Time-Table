/**
 * Sidebars of the three roles - straight from Section 6 of the master plan. Every entry is a real screen.
 */
import {
  Bell, BookOpen, Bot, Building2, CalendarCheck, CalendarClock, CalendarDays, CalendarOff, CalendarRange, ChartColumn,
  CircleUser, ClipboardList, Clock, House, Inbox, LayoutDashboard, Link2, Network, Pin, Repeat, ScrollText, Send, ShieldAlert, SlidersHorizontal, Table2,
  Timer, Users, WandSparkles, type LucideIcon,
} from 'lucide-react'
import type { Role } from './types'

export type ScreenKey =
  | 'active-term' | 'college-setup' | 'structure' | 'timing' | 'subjects' | 'teachers' | 'assignment'
  | 'fixed-slots' | 'holidays' | 'profile' | 'drafts' | 'conflicts'
  | 'publish' | 'timetables' | 'hours' | 'my-timetable' | 'my-subjects' | 'notifications'
  | 'substitutions' | 'absent-leave' | 'offers' | 'preferences' | 'my-preferences' | 'reports' | 'audit-log' | 'ai-assistant'

export interface NavItem {
  label: string
  path: string
  icon: LucideIcon
  about: string
  /** a real working screen (the dashboard of the role, or - with `screen` - one of the pages) */
  live?: boolean
  /** which Phase 1 page shows this item (see SCREENS in App.tsx) */
  screen?: ScreenKey
  /** sub-items (HOD "My Schedule" = teacher mode) */
  children?: NavItem[]
}

export const HOME: Record<Role, string> = {
  admin: '/admin/dashboard',
  hod: '/hod/dashboard',
  teacher: '/teacher/home',
}

export const ROLE_LABEL: Record<Role, string> = {
  admin: 'Administrator',
  hod: 'Head of Department',
  teacher: 'Teacher',
}

export const ROLE_PREFIX: Record<Role, string> = {
  admin: '/admin',
  hod: '/hod',
  teacher: '/teacher',
}

const ADMIN: NavItem[] = [
  { label: 'Dashboard', path: '/admin/dashboard', icon: LayoutDashboard, live: true, about: 'The college at a glance.' },
  { label: 'Active Term', path: '/admin/active-term', icon: CalendarCheck, live: true, screen: 'active-term', about: 'Which term is running (odd or even + year) and the semester dates.' },
  { label: 'College Setup', path: '/admin/college-setup', icon: Building2, live: true, screen: 'college-setup', about: 'Schools, courses and semesters - and the HOD of each school.' },
  { label: 'Timing Setup', path: '/admin/timing-setup', icon: Clock, live: true, screen: 'timing', about: "College hours, lecture length, short breaks, working days and each class's day-wise schedule." },
  { label: 'Subjects & Credits', path: '/admin/subjects', icon: BookOpen, live: true, screen: 'subjects', about: 'Subjects with credits, semester hours, labs and elective groups.' },
  { label: 'Teachers', path: '/admin/teachers', icon: Users, live: true, screen: 'teachers', about: 'HOD and teacher accounts and profiles - specialization and visiting-faculty settings.' },
  { label: 'Assignment', path: '/admin/assignment', icon: Link2, live: true, screen: 'assignment', about: 'Which teacher teaches which subject in which class.' },
  { label: 'Fixed Slots', path: '/admin/fixed-slots', icon: Pin, live: true, screen: 'fixed-slots', about: 'Lock slots by hand - PE and SOL double blocks, or anything else.' },
  { label: 'Holidays & Events', path: '/admin/holidays-events', icon: CalendarDays, live: true, screen: 'holidays', about: 'College-wide holidays, plus events that block a day or a few slots.' },
  { label: 'Generate Timetable', path: '/admin/generate', icon: WandSparkles, live: true, screen: 'drafts', about: 'Generate the weekly timetable automatically, or build and adjust it by hand (drag and drop, live clash checks).' },
  { label: 'Conflict Report', path: '/admin/conflicts', icon: ShieldAlert, live: true, screen: 'conflicts', about: 'Every clash and every lecture still missing in the timetable drafts.' },
  { label: 'Publish', path: '/admin/publish', icon: Send, live: true, screen: 'publish', about: 'Make the primary drafts live for the teachers, class by class or all at once.' },
  { label: 'Timetables', path: '/admin/timetables', icon: Table2, live: true, screen: 'timetables', about: 'The published timetable by class, by teacher and for the whole school - with filters and PDF / Excel downloads.' },
  { label: 'Semester Hours', path: '/admin/semester-hours', icon: Timer, live: true, screen: 'hours', about: 'Hours taught versus hours required, short subjects in red, and clash-free extra lectures for holidays.' },
  { label: 'Substitutions', path: '/admin/substitutions', icon: Repeat, live: true, screen: 'substitutions', about: 'Mark absences, assign or auto-offer substitutes, approve leave, one-day changes and the daily sheet.' },
  { label: 'Teacher Preferences', path: '/admin/preferences', icon: ClipboardList, live: true, screen: 'preferences', about: "Open a window for teachers to say when they can and cannot teach, and see who has filled." },
  { label: 'Reports', path: '/admin/reports', icon: ChartColumn, live: true, screen: 'reports', about: 'Workload against the UGC / AICTE norm, busiest and free teachers, free-teacher finder, hours progress, cancelled sessions, substitutions and school comparison.' },
  { label: 'Audit Log', path: '/admin/audit-log', icon: ScrollText, live: true, screen: 'audit-log', about: 'A record of every important change - who, what and when. Filter by person, action and date.' },
  { label: 'Notifications', path: '/admin/notifications', icon: Bell, live: true, screen: 'notifications', about: 'Everything sent to you.' },
  { label: 'AI Assistant', path: '/admin/ai-assistant', icon: Bot, live: true, screen: 'ai-assistant', about: 'Ask about teachers, free periods and timetables - in English or Hinglish.' },
]

const HOD_MY_SCHEDULE: NavItem[] = [
  { label: 'My Timetable', path: '/hod/my-schedule/timetable', icon: CalendarRange, live: true, screen: 'my-timetable', about: 'Your own weekly timetable, as a teacher.' },
  { label: 'My Profile', path: '/hod/my-schedule/profile', icon: CircleUser, live: true, screen: 'profile', about: 'Your details, photo, specialization and the subjects you can teach.' },
  { label: 'My Preferences', path: '/hod/my-schedule/preferences', icon: SlidersHorizontal, live: true, screen: 'my-preferences', about: 'Mark your slots as available, preferred or not available.' },
  { label: 'Absent & Leave', path: '/hod/my-schedule/absent-leave', icon: CalendarOff, live: true, screen: 'absent-leave', about: 'Mark yourself absent and send leave requests.' },
  { label: 'Offers', path: '/hod/my-schedule/offers', icon: Inbox, live: true, screen: 'offers', about: 'Substitution offers waiting for your answer, and lectures you are covering.' },
  { label: 'My Subjects', path: '/hod/my-schedule/subjects', icon: BookOpen, live: true, screen: 'my-subjects', about: 'Hours you have taught versus hours required, per subject.' },
]

const HOD: NavItem[] = [
  { label: 'Dashboard', path: '/hod/dashboard', icon: LayoutDashboard, live: true, about: 'Your school at a glance.' },
  { label: 'Active Term', path: '/hod/active-term', icon: CalendarCheck, live: true, screen: 'active-term', about: 'Which term is running (odd or even + year) and the semester dates.' },
  { label: 'School Structure', path: '/hod/school-structure', icon: Network, live: true, screen: 'structure', about: 'The courses and semesters of your school (set by the administrator).' },
  { label: 'My School Teachers', path: '/hod/teachers', icon: Users, live: true, screen: 'teachers', about: 'Accounts and profiles of the teachers in your school.' },
  { label: 'Subjects & Credits', path: '/hod/subjects', icon: BookOpen, live: true, screen: 'subjects', about: 'Subjects with credits, semester hours, labs and elective groups.' },
  { label: 'Assignment', path: '/hod/assignment', icon: Link2, live: true, screen: 'assignment', about: 'Which teacher teaches which subject in which class.' },
  { label: 'School Timing', path: '/hod/timing', icon: Clock, live: true, screen: 'timing', about: "Your school's lunch time and each class's day-wise schedule." },
  { label: 'Fixed Slots', path: '/hod/fixed-slots', icon: Pin, live: true, screen: 'fixed-slots', about: 'Lock slots by hand - PE and SOL double blocks, or anything else.' },
  { label: 'Events & Holidays', path: '/hod/events-holidays', icon: CalendarDays, live: true, screen: 'holidays', about: 'School events and extra holidays for your classes.' },
  { label: 'Generate Timetable', path: '/hod/generate', icon: WandSparkles, live: true, screen: 'drafts', about: 'Generate the weekly timetable automatically, or build and adjust it by hand (drag and drop, live clash checks).' },
  { label: 'Conflict Report', path: '/hod/conflicts', icon: ShieldAlert, live: true, screen: 'conflicts', about: 'Every clash and every lecture still missing in the timetable drafts.' },
  { label: 'Publish', path: '/hod/publish', icon: Send, live: true, screen: 'publish', about: 'Make the primary drafts live for the teachers, class by class or all at once.' },
  { label: 'Timetables', path: '/hod/timetables', icon: Table2, live: true, screen: 'timetables', about: 'The published timetable by class, by teacher and for the whole school - with filters and PDF / Excel downloads.' },
  { label: 'Semester Hours', path: '/hod/semester-hours', icon: Timer, live: true, screen: 'hours', about: 'Hours taught versus hours required, short subjects in red, and clash-free extra lectures for holidays.' },
  { label: 'Substitutions', path: '/hod/substitutions', icon: Repeat, live: true, screen: 'substitutions', about: 'Mark absences, assign or auto-offer substitutes, approve leave, one-day changes and the daily sheet.' },
  { label: 'Teacher Preferences', path: '/hod/preferences', icon: ClipboardList, live: true, screen: 'preferences', about: "Open a window for your teachers to say when they can and cannot teach, and see who has filled." },
  { label: 'Reports', path: '/hod/reports', icon: ChartColumn, live: true, screen: 'reports', about: "Workload against the UGC / AICTE norm, busiest and free teachers, free-teacher finder, hours progress, cancelled sessions and substitutions of your school." },
  { label: 'Notifications', path: '/hod/notifications', icon: Bell, live: true, screen: 'notifications', about: 'Everything sent to you.' },
  { label: 'AI Assistant', path: '/hod/ai-assistant', icon: Bot, live: true, screen: 'ai-assistant', about: 'Ask about teachers, free periods and timetables - in English or Hinglish.' },
  {
    label: 'My Schedule',
    path: '/hod/my-schedule',
    icon: CalendarClock,
    about: 'Teacher Mode - everything you do as a teacher.',
    children: HOD_MY_SCHEDULE,
  },
]

const TEACHER: NavItem[] = [
  { label: 'Home', path: '/teacher/home', icon: House, live: true, about: "Today's schedule at a glance." },
  { label: 'My Timetable', path: '/teacher/timetable', icon: CalendarRange, live: true, screen: 'my-timetable', about: 'Your weekly timetable, including combined sessions and co-taught lectures.' },
  { label: 'My Profile', path: '/teacher/profile', icon: CircleUser, live: true, screen: 'profile', about: 'Your details, photo, specialization and the subjects you can teach.' },
  { label: 'My Preferences', path: '/teacher/preferences', icon: SlidersHorizontal, live: true, screen: 'my-preferences', about: 'Mark your slots as available, preferred or not available.' },
  { label: 'Absent & Leave', path: '/teacher/absent-leave', icon: CalendarOff, live: true, screen: 'absent-leave', about: 'Mark yourself absent and send leave requests.' },
  { label: 'Offers', path: '/teacher/offers', icon: Inbox, live: true, screen: 'offers', about: 'Substitution offers waiting for your answer, and lectures you are covering.' },
  { label: 'My Subjects', path: '/teacher/subjects', icon: BookOpen, live: true, screen: 'my-subjects', about: 'Hours you have taught versus hours required, per subject.' },
  { label: 'AI Assistant', path: '/teacher/ai-assistant', icon: Bot, live: true, screen: 'ai-assistant', about: 'Quick answers about your day and about free teachers.' },
  { label: 'Notifications', path: '/teacher/notifications', icon: Bell, live: true, screen: 'notifications', about: 'Everything that changed for you.' },
]

export const NAV: Record<Role, NavItem[]> = { admin: ADMIN, hod: HOD, teacher: TEACHER }

/** every clickable screen of a role (sub-items included, group headers excluded) */
export function leaves(role: Role): NavItem[] {
  return NAV[role].flatMap((item) => (item.children ? item.children : [item]))
}

export function findItem(role: Role, pathname: string): NavItem | undefined {
  // a sub-page (the grid editor of a draft: /hod/generate/<id>) belongs to its parent menu item
  return (
    leaves(role).find((item) => item.path === pathname) ??
    leaves(role).find((item) => item.screen === 'drafts' && pathname.startsWith(item.path + '/'))
  )
}

/** Is this a real screen of that role? (used before trusting a remembered "last opened view") */
export function isKnownPath(role: Role, path: string): boolean {
  const clean = path.split(/[?#]/)[0]
  return leaves(role).some((item) => item.path === clean)
}

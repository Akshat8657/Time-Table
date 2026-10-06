/**
 * Sidebars of the three roles - straight from Section 6 of the master plan.
 * Every entry is a placeholder screen in Phase 0 and says which phase delivers it.
 */
import {
  Bell, BookOpen, Bot, Building2, CalendarClock, CalendarDays, CalendarOff, CalendarRange, ChartColumn,
  CircleUser, Clock, House, Inbox, LayoutDashboard, Link2, Pin, Repeat, ScrollText, SlidersHorizontal, Users,
  WandSparkles, type LucideIcon,
} from 'lucide-react'
import type { Role } from './types'

export interface NavItem {
  label: string
  path: string
  icon: LucideIcon
  /** the roadmap phase that delivers this screen (null = not scheduled yet) */
  phase: string | null
  about: string
  /** real screen already in Phase 0 */
  live?: boolean
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
  { label: 'Dashboard', path: '/admin/dashboard', icon: LayoutDashboard, phase: null, live: true, about: 'The college at a glance.' },
  { label: 'College Setup', path: '/admin/college-setup', icon: Building2, phase: 'Phase 1', about: 'Schools, courses and semesters - and the HOD of each school.' },
  { label: 'Timing Setup', path: '/admin/timing-setup', icon: Clock, phase: 'Phase 1', about: 'College hours, lecture length, short breaks and working days.' },
  { label: 'Subjects & Credits', path: '/admin/subjects', icon: BookOpen, phase: 'Phase 1', about: 'Subjects with credits, semester hours, labs and elective groups.' },
  { label: 'Teachers', path: '/admin/teachers', icon: Users, phase: 'Phase 1', about: 'Teacher accounts and profiles - specialization and visiting-faculty settings.' },
  { label: 'Assignment', path: '/admin/assignment', icon: Link2, phase: 'Phase 1', about: 'Which teacher teaches which subject in which class.' },
  { label: 'Fixed Slots', path: '/admin/fixed-slots', icon: Pin, phase: 'Phase 1', about: 'Lock slots by hand - PE and SOL double blocks, or anything else.' },
  { label: 'Holidays & Events', path: '/admin/holidays-events', icon: CalendarDays, phase: 'Phase 1', about: 'College-wide holidays, plus events that block a day or a few slots.' },
  { label: 'Generate Timetable', path: '/admin/generate', icon: WandSparkles, phase: 'Phase 2-3', about: 'Build clash-free timetables, compare scored drafts and publish.' },
  { label: 'Substitutions', path: '/admin/substitutions', icon: Repeat, phase: 'Phase 5', about: 'Absences, substitution offers and the daily substitution sheet.' },
  { label: 'Reports', path: '/admin/reports', icon: ChartColumn, phase: 'Phase 6', about: 'Workload, free-teacher finder, hours progress and more.' },
  { label: 'Audit Log', path: '/admin/audit-log', icon: ScrollText, phase: null, about: 'A record of every important change - who, what and when.' },
  { label: 'AI Assistant', path: '/admin/ai-assistant', icon: Bot, phase: 'Phase 7', about: 'Ask about teachers, free periods and timetables - in English or Hinglish.' },
]

const HOD_MY_SCHEDULE: NavItem[] = [
  { label: 'My Timetable', path: '/hod/my-schedule/timetable', icon: CalendarRange, phase: 'Phase 4', about: 'Your own weekly timetable, as a teacher.' },
  { label: 'My Preferences', path: '/hod/my-schedule/preferences', icon: SlidersHorizontal, phase: 'Phase 6', about: 'Mark your slots as available, preferred or not available.' },
  { label: 'Absent & Leave', path: '/hod/my-schedule/absent-leave', icon: CalendarOff, phase: 'Phase 5', about: 'Mark yourself absent and send leave requests.' },
  { label: 'Offers', path: '/hod/my-schedule/offers', icon: Inbox, phase: 'Phase 5', about: 'Substitution offers waiting for your answer.' },
  { label: 'My Subjects', path: '/hod/my-schedule/subjects', icon: BookOpen, phase: 'Phase 4', about: 'Hours you have taught versus hours required, per subject.' },
]

const HOD: NavItem[] = [
  { label: 'Dashboard', path: '/hod/dashboard', icon: LayoutDashboard, phase: null, live: true, about: 'Your school at a glance.' },
  { label: 'My School Teachers', path: '/hod/teachers', icon: Users, phase: 'Phase 1', about: 'Accounts and profiles of the teachers in your school.' },
  { label: 'Subjects & Credits', path: '/hod/subjects', icon: BookOpen, phase: 'Phase 1', about: 'Subjects with credits, semester hours, labs and elective groups.' },
  { label: 'Assignment', path: '/hod/assignment', icon: Link2, phase: 'Phase 1', about: 'Which teacher teaches which subject in which class.' },
  { label: 'School Timing', path: '/hod/timing', icon: Clock, phase: 'Phase 1', about: "Your school's lunch time and semester-wise early release." },
  { label: 'Fixed Slots', path: '/hod/fixed-slots', icon: Pin, phase: 'Phase 1', about: 'Lock slots by hand - PE and SOL double blocks, or anything else.' },
  { label: 'Events & Holidays', path: '/hod/events-holidays', icon: CalendarDays, phase: 'Phase 1', about: 'School events and extra holidays for your classes.' },
  { label: 'Generate Timetable', path: '/hod/generate', icon: WandSparkles, phase: 'Phase 2-3', about: 'Build clash-free timetables, compare scored drafts and publish.' },
  { label: 'Substitutions', path: '/hod/substitutions', icon: Repeat, phase: 'Phase 5', about: 'Absences, substitution offers and the daily substitution sheet.' },
  { label: 'Reports', path: '/hod/reports', icon: ChartColumn, phase: 'Phase 6', about: 'Workload, free-teacher finder, hours progress and more.' },
  { label: 'AI Assistant', path: '/hod/ai-assistant', icon: Bot, phase: 'Phase 7', about: 'Ask about teachers, free periods and timetables - in English or Hinglish.' },
  {
    label: 'My Schedule',
    path: '/hod/my-schedule',
    icon: CalendarClock,
    phase: null,
    about: 'Teacher Mode - everything you do as a teacher.',
    children: HOD_MY_SCHEDULE,
  },
]

const TEACHER: NavItem[] = [
  { label: 'Home', path: '/teacher/home', icon: House, phase: null, live: true, about: "Today's schedule at a glance." },
  { label: 'My Timetable', path: '/teacher/timetable', icon: CalendarRange, phase: 'Phase 4', about: 'Your weekly timetable and the timetables of the classes you teach.' },
  { label: 'My Profile', path: '/teacher/profile', icon: CircleUser, phase: 'Phase 1', about: 'Your details, photo and specialization.' },
  { label: 'My Preferences', path: '/teacher/preferences', icon: SlidersHorizontal, phase: 'Phase 6', about: 'Mark your slots as available, preferred or not available.' },
  { label: 'Absent & Leave', path: '/teacher/absent-leave', icon: CalendarOff, phase: 'Phase 5', about: 'Mark yourself absent and send leave requests.' },
  { label: 'Offers', path: '/teacher/offers', icon: Inbox, phase: 'Phase 5', about: 'Substitution offers waiting for your answer.' },
  { label: 'My Subjects', path: '/teacher/subjects', icon: BookOpen, phase: 'Phase 4', about: 'Hours you have taught versus hours required, per subject.' },
  { label: 'AI Assistant', path: '/teacher/ai-assistant', icon: Bot, phase: 'Phase 7', about: 'Quick answers about your day and about free teachers.' },
  { label: 'Notifications', path: '/teacher/notifications', icon: Bell, phase: 'Phase 4', about: 'Everything that changed for you.' },
]

export const NAV: Record<Role, NavItem[]> = { admin: ADMIN, hod: HOD, teacher: TEACHER }

/** every clickable screen of a role (sub-items included, group headers excluded) */
export function leaves(role: Role): NavItem[] {
  return NAV[role].flatMap((item) => (item.children ? item.children : [item]))
}

export function findItem(role: Role, pathname: string): NavItem | undefined {
  return leaves(role).find((item) => item.path === pathname)
}

/** Is this a real screen of that role? (used before trusting a remembered "last opened view") */
export function isKnownPath(role: Role, path: string): boolean {
  const clean = path.split(/[?#]/)[0]
  return leaves(role).some((item) => item.path === clean)
}

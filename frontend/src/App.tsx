import { lazy, Suspense, type ComponentType } from 'react'
import { Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { useAuth } from './auth/AuthProvider'
import { PublicOnly, RequireAuth, RequireRole, RoleRedirect } from './auth/guards'
import { CookieBanner } from './consent/CookieBanner'
import { AppShell } from './layouts/AppShell'
import { HOME, NAV, ROLE_PREFIX, leaves, type ScreenKey } from './lib/nav'
import type { Role } from './lib/types'
import { FullScreenLoader } from './components/FullScreen'
import LoginPage from './pages/LoginPage'

// Loaded only when needed, so the first screen (the login) arrives quickly.
const ChangePasswordPage = lazy(() => import('./pages/ChangePasswordPage'))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'))
const AdminDashboard = lazy(() => import('./pages/DashboardPage').then((m) => ({ default: m.AdminDashboard })))
const HodDashboard = lazy(() => import('./pages/DashboardPage').then((m) => ({ default: m.HodDashboard })))
const TeacherDashboard = lazy(() => import('./pages/DashboardPage').then((m) => ({ default: m.TeacherDashboard })))

// The Phase 1 screens. Each one is loaded only when it is opened, so the first page appears quickly.
const SCREENS: Record<ScreenKey, ComponentType> = {
  'active-term': lazy(() => import('./pages/ActiveTermPage')),
  'college-setup': lazy(() => import('./pages/CollegeSetupPage')),
  structure: lazy(() => import('./pages/SchoolStructurePage')),
  timing: lazy(() => import('./pages/TimingPage')),
  subjects: lazy(() => import('./pages/SubjectsPage')),
  teachers: lazy(() => import('./pages/TeachersPage')),
  assignment: lazy(() => import('./pages/AssignmentPage')),
  'fixed-slots': lazy(() => import('./pages/FixedSlotsPage')),
  holidays: lazy(() => import('./pages/HolidaysPage')),
  profile: lazy(() => import('./pages/MyProfilePage')),
  drafts: lazy(() => import('./pages/DraftsPage')),
  conflicts: lazy(() => import('./pages/ConflictsPage')),
  publish: lazy(() => import('./pages/PublishPage')),
  timetables: lazy(() => import('./pages/TimetablesPage')),
  hours: lazy(() => import('./pages/HoursPage')),
  'my-timetable': lazy(() => import('./pages/MyTimetablePage')),
  'my-subjects': lazy(() => import('./pages/MySubjectsPage')),
  notifications: lazy(() => import('./pages/NotificationsPage')),
  substitutions: lazy(() => import('./pages/SubstitutionsPage')),
  'absent-leave': lazy(() => import('./pages/AbsentLeavePage')),
  offers: lazy(() => import('./pages/OffersPage')),
  preferences: lazy(() => import('./pages/PreferencesPage')),
  'my-preferences': lazy(() => import('./pages/MyPreferencesPage')),
  reports: lazy(() => import('./pages/ReportsPage')),
  'audit-log': lazy(() => import('./pages/AuditLogPage')),
  'ai-assistant': lazy(() => import('./pages/AssistantPage')),
}

// the weekly grid editor of one draft (a sub-page of "Generate Timetable")
const DraftEditor = lazy(() => import('./pages/DraftEditorPage'))

function screenFor(item: { screen?: ScreenKey }, Dashboard: ComponentType): ComponentType {
  return item.screen ? SCREENS[item.screen] : Dashboard
}

/** One signed-in area per role. Every menu item of Section 6 is a real screen (or the dashboard of that role). */
function roleBranch(role: Role, Dashboard: ComponentType) {
  const prefix = ROLE_PREFIX[role]
  const relative = (path: string) => path.slice(prefix.length + 1)

  return (
    <Route
      key={role}
      path={prefix}
      element={
        <RequireAuth>
          <RequireRole roles={[role]}>
            <AppShell />
          </RequireRole>
        </RequireAuth>
      }
    >
      <Route index element={<Navigate to={HOME[role]} replace />} />
      {leaves(role).map((item) => {
        const Screen = screenFor(item, Dashboard)
        return <Route key={item.path} path={relative(item.path)} element={<Screen />} />
      })}
      {role !== 'teacher' && <Route path="generate/:draftId" element={<DraftEditor />} />}
      {NAV[role]
        .filter((item) => item.children)
        .map((group) => (
          <Route key={group.path} path={relative(group.path)} element={<Navigate to={group.children![0].path} replace />} />
        ))}
      <Route path="*" element={<NotFoundPage inShell />} />
    </Route>
  )
}

/** Links inside notifications are the same for everybody ("/my/timetable"); each role lands on its own screen. */
const MY_SCREENS: Record<string, Partial<Record<Role, string>>> = {
  timetable: { teacher: '/teacher/timetable', hod: '/hod/my-schedule/timetable' },
  subjects: { teacher: '/teacher/subjects', hod: '/hod/my-schedule/subjects' },
  notifications: { teacher: '/teacher/notifications', hod: '/hod/notifications', admin: '/admin/notifications' },
  offers: { teacher: '/teacher/offers', hod: '/hod/my-schedule/offers' },
  'absent-leave': { teacher: '/teacher/absent-leave', hod: '/hod/my-schedule/absent-leave' },
  preferences: { teacher: '/teacher/preferences', hod: '/hod/my-schedule/preferences' },
}

const CONSOLE: Partial<Record<Role, string>> = { hod: '/hod/substitutions', admin: '/admin/substitutions' }
const PREF_CONSOLE: Partial<Record<Role, string>> = { hod: '/hod/preferences', admin: '/admin/preferences' }

/** A notification link that is the same for everybody ("/substitutions") lands each manager on their own console. */
function ConsoleRedirect({ map = CONSOLE }: { map?: Partial<Record<Role, string>> }) {
  const { user } = useAuth()
  const { search } = useLocation()   // "?date=..." survives the redirect
  const target = user && map[user.role]
  return <Navigate to={target ? target + search : user ? HOME[user.role] : '/'} replace />
}

function MyRedirect() {
  const { user } = useAuth()
  const { screen = '' } = useParams()
  const to = user ? MY_SCREENS[screen]?.[user.role] : undefined
  return <Navigate to={to ?? (user ? HOME[user.role] : '/')} replace />
}

export default function App() {
  return (
    <>
      <Suspense fallback={<FullScreenLoader />}>
      <Routes>
        <Route path="/" element={<RoleRedirect />} />
        <Route
          path="/login"
          element={
            <PublicOnly>
              <LoginPage />
            </PublicOnly>
          }
        />
        <Route
          path="/change-password"
          element={
            <RequireAuth>
              <ChangePasswordPage />
            </RequireAuth>
          }
        />
        <Route
          path="/my/:screen"
          element={
            <RequireAuth>
              <MyRedirect />
            </RequireAuth>
          }
        />
        <Route
          path="/substitutions"
          element={
            <RequireAuth>
              <ConsoleRedirect />
            </RequireAuth>
          }
        />
        <Route
          path="/preference-window"
          element={
            <RequireAuth>
              <ConsoleRedirect map={PREF_CONSOLE} />
            </RequireAuth>
          }
        />
        {roleBranch('admin', AdminDashboard)}
        {roleBranch('hod', HodDashboard)}
        {roleBranch('teacher', TeacherDashboard)}
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
      </Suspense>
      <CookieBanner />
    </>
  )
}

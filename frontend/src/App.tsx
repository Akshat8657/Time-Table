import { lazy, type ComponentType } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { PublicOnly, RequireAuth, RequireRole, RoleRedirect } from './auth/guards'
import { CookieBanner } from './consent/CookieBanner'
import { AppShell } from './layouts/AppShell'
import { HOME, NAV, ROLE_PREFIX, leaves, type ScreenKey } from './lib/nav'
import type { Role } from './lib/types'
import ChangePasswordPage from './pages/ChangePasswordPage'
import { AdminDashboard, HodDashboard, TeacherDashboard } from './pages/DashboardPage'
import LoginPage from './pages/LoginPage'
import NotFoundPage from './pages/NotFoundPage'
import PlaceholderPage from './pages/PlaceholderPage'

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
}

function screenFor(item: { screen?: ScreenKey; live?: boolean }, Dashboard: ComponentType): ComponentType {
  if (item.screen) return SCREENS[item.screen]
  return item.live ? Dashboard : PlaceholderPage
}

/** One signed-in area per role. Menu items of Section 6 are real screens, a dashboard, or a placeholder for a later phase. */
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
      {NAV[role]
        .filter((item) => item.children)
        .map((group) => (
          <Route key={group.path} path={relative(group.path)} element={<Navigate to={group.children![0].path} replace />} />
        ))}
      <Route path="*" element={<NotFoundPage inShell />} />
    </Route>
  )
}

export default function App() {
  return (
    <>
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
        {roleBranch('admin', AdminDashboard)}
        {roleBranch('hod', HodDashboard)}
        {roleBranch('teacher', TeacherDashboard)}
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
      <CookieBanner />
    </>
  )
}

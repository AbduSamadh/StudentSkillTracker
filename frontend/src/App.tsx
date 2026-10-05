import type { ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { PortalLayout, StaffLayout } from './components/Layout'
import { Loading } from './components/ui'
import { useAuth } from './lib/auth'
import { BudgetPage, AuditPage, ImportsPage, InventoryPage, SettingsPage, UsersPage } from './pages/admin/Admin'
import { AttendancePage, ConfirmAwardsPage, QuickTagPage, ResultEntryPage } from './pages/capture/Capture'
import CompetitionsPage, { CompetitionDetailPage, EditionDetailPage } from './pages/Competitions'
import DashboardPage from './pages/Dashboard'
import Login, { AuthCallback, OptOutPage, ParentLogin } from './pages/Login'
import MessagesPage, { ComposePage, EmergencyPage, MessageDetailPage, TemplatesPage } from './pages/Messages'
import ParentHome, { ChildPage, PortalMessages, PortalPreferences, StudentHome } from './pages/portal/Portal'
import ReportsPage, { FlagsPage, InsightsPage } from './pages/Reports'
import SkillsPage from './pages/SkillsPage'
import SquadsPage, { SquadDetailPage } from './pages/Squads'
import StudentProfilePage from './pages/StudentProfile'
import StudentsPage from './pages/Students'

function RequireAuth({ children }: { children: ReactNode }) {
  const { ready, authed } = useAuth()
  const location = useLocation()
  if (!ready) return <Loading />
  if (!authed) {
    const portal = location.pathname.startsWith('/portal')
    return <Navigate to={portal ? '/portal/login' : '/login'} replace state={{ from: location.pathname }} />
  }
  return <>{children}</>
}

function RequireCap({ cap, children }: { cap: string; children: ReactNode }) {
  const { can } = useAuth()
  return can(cap) ? <>{children}</> : <Navigate to="/" replace />
}

/** Each role lands somewhere useful: leaders on the dashboard, teachers on their squads. */
function Home() {
  const { can, hasRole, isStaff, me } = useAuth()
  if (can('view_school_analytics')) return <Navigate to="/dashboard" replace />
  if (isStaff) return <Navigate to="/squads" replace />
  if (hasRole('parent')) return <Navigate to="/portal" replace />
  if (me?.student_portal) return <Navigate to="/me" replace />
  return <Navigate to="/login" replace />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/auth/callback" element={<AuthCallback />} />
      <Route path="/portal/login" element={<ParentLogin />} />
      <Route path="/portal/opt-out" element={<OptOutPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Home />
          </RequireAuth>
        }
      />
      <Route
        element={
          <RequireAuth>
            <StaffLayout />
          </RequireAuth>
        }
      >
        <Route path="/dashboard" element={<RequireCap cap="view_school_analytics"><DashboardPage /></RequireCap>} />
        <Route path="/students" element={<RequireCap cap="view_roster"><StudentsPage /></RequireCap>} />
        <Route path="/students/:id" element={<RequireCap cap="view_roster"><StudentProfilePage /></RequireCap>} />
        <Route path="/squads" element={<RequireCap cap="view_roster"><SquadsPage /></RequireCap>} />
        <Route path="/squads/:id" element={<RequireCap cap="view_roster"><SquadDetailPage /></RequireCap>} />
        <Route path="/capture/attendance" element={<RequireCap cap="record_attendance"><AttendancePage /></RequireCap>} />
        <Route path="/capture/result" element={<RequireCap cap="record_results"><ResultEntryPage /></RequireCap>} />
        <Route path="/capture/tag" element={<RequireCap cap="verify_skills"><QuickTagPage /></RequireCap>} />
        <Route path="/capture/confirm" element={<RequireCap cap="verify_skills"><ConfirmAwardsPage /></RequireCap>} />
        <Route path="/competitions" element={<RequireCap cap="view_roster"><CompetitionsPage /></RequireCap>} />
        <Route path="/competitions/:id" element={<RequireCap cap="view_roster"><CompetitionDetailPage /></RequireCap>} />
        <Route path="/editions/:id" element={<RequireCap cap="view_roster"><EditionDetailPage /></RequireCap>} />
        <Route path="/skills" element={<RequireCap cap="view_roster"><SkillsPage /></RequireCap>} />
        <Route path="/flags" element={<RequireCap cap="run_readiness"><FlagsPage /></RequireCap>} />
        <Route path="/insights" element={<RequireCap cap="review_insights"><InsightsPage /></RequireCap>} />
        <Route path="/messages" element={<RequireCap cap="draft_messages"><MessagesPage /></RequireCap>} />
        <Route path="/messages/new" element={<RequireCap cap="draft_messages"><ComposePage /></RequireCap>} />
        <Route path="/messages/:id" element={<RequireCap cap="draft_messages"><MessageDetailPage /></RequireCap>} />
        <Route path="/templates" element={<RequireCap cap="approve_templates"><TemplatesPage /></RequireCap>} />
        <Route path="/emergency" element={<RequireCap cap="emergency_broadcast"><EmergencyPage /></RequireCap>} />
        <Route path="/reports" element={<RequireCap cap="generate_reports"><ReportsPage /></RequireCap>} />
        <Route path="/inventory" element={<RequireCap cap="view_inventory"><InventoryPage /></RequireCap>} />
        <Route path="/budget" element={<RequireCap cap="view_budget"><BudgetPage /></RequireCap>} />
        <Route path="/imports" element={<RequireCap cap="manage_imports"><ImportsPage /></RequireCap>} />
        <Route path="/audit" element={<RequireCap cap="view_audit_own"><AuditPage /></RequireCap>} />
        <Route path="/users" element={<RequireCap cap="manage_users"><UsersPage /></RequireCap>} />
        <Route path="/settings" element={<RequireCap cap="manage_settings"><SettingsPage /></RequireCap>} />
      </Route>
      <Route
        element={
          <RequireAuth>
            <PortalLayout />
          </RequireAuth>
        }
      >
        <Route path="/portal" element={<ParentHome />} />
        <Route path="/portal/children/:id" element={<ChildPage />} />
        <Route path="/portal/messages" element={<PortalMessages />} />
        <Route path="/portal/preferences" element={<PortalPreferences />} />
        <Route path="/me" element={<StudentHome />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

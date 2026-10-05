export type UUID = string

export interface Me {
  user_id: UUID
  display_name: string
  email: string
  locale: 'en' | 'ar'
  roles: { role: Role; scope_type: string; scope_id: UUID | null }[]
  capabilities: string[]
  mfa: boolean
  tenant: { id: UUID; slug: string; name: string; name_ar: string | null; branding: { primary: string; accent: string; logo_url: string | null }; timezone: string }
  children: { id: UUID; name: string; year_group: number }[]
  student_portal: { student_id: UUID; enabled: boolean } | null
}

export type Role = 'teacher' | 'programme_admin' | 'leader' | 'parent' | 'student'
export type ClaimType = 'measured' | 'inferred'

export interface Figure {
  label: string
  kind: 'percent' | 'count' | 'money' | 'number'
  value: number | null
  numerator: number | null
  denominator: number | null
  denominator_label: string | null
  display: string
  withheld: boolean
  withheld_reason: string | null
  claim_type: ClaimType
  unit: string | null
}

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface Student {
  id: UUID
  external_mis_id: string
  given_name: string
  family_name: string
  preferred_name: string | null
  full_name_ar: string | null
  display_name: string
  year_group: number
  house: string | null
  gender: string | null
  enrolment_status: string
}

export interface Skill {
  id: UUID
  code: string
  name: string
  domain: string
  strand: string
  description: string | null
  parent_label_en: string
  parent_label_ar: string
  typical_year_group: number | null
  framework_refs: string[]
  is_active: boolean
}

export interface Squad {
  id: UUID
  name: string
  season_id: UUID | null
  discipline: string | null
  lead_coach_user_id: UUID | null
  description: string | null
  is_active: boolean
}

export interface Membership {
  id: UUID
  squad_id: UUID
  student_id: UUID
  role: string | null
  is_reserve: boolean
  status: 'active' | 'withdrawn'
  joined_on: string | null
  withdrawn_on: string | null
  withdrawal_reason: string | null
  name: string
  year_group: number
}

export interface SquadDetail extends Squad {
  members: Membership[]
  coaches: { user_id: UUID; name: string; is_lead: boolean }[]
  target_editions: { edition_id: UUID; name: string; competition_name: string; event_starts: string; event_ends: string; tier: string }[]
}

export interface Edition {
  id: UUID
  competition_id: UUID
  competition_name: string | null
  season_id: UUID | null
  name: string
  stage: string | null
  tier: string
  registration_opens: string | null
  registration_closes: string | null
  event_starts: string
  event_ends: string
  venue: string | null
  entry_fee: string | null
  currency: string
  expected_field_size: number | null
  eligible_year_min: number | null
  eligible_year_max: number | null
  max_team_size: number | null
  external_registration_url: string | null
  rubric: { key: string; label: string; max_score: number; skill_code?: string; level_if_met?: number; threshold?: number }[]
  notes: string | null
}

export interface Competition {
  id: UUID
  name: string
  name_ar: string | null
  organiser: string | null
  discipline: string
  tier: string
  entry_format: string
  typical_field_size: number | null
  description: string | null
  website_url: string | null
  status: 'proposed' | 'active' | 'archived'
}

export interface ReadinessLine {
  skill_id: UUID
  code: string
  name: string
  parent_label_en: string
  parent_label_ar: string
  domain: string
  required_level: number
  required_level_name: string
  weight: number
  is_core: boolean
  inherited: boolean
  requirement_id: UUID
  earned_level: number
  earned_level_name: string
  ratio: number
  contribution: number
  is_gap: boolean
  levels_short: number
  evidence: null | {
    award_id: UUID
    level: number
    source: string
    confidence: string
    awarded_on: string
    verified_by_id: UUID | null
    evidence_note: string | null
  }
}

export interface Readiness {
  student_id: UUID
  edition_id: UUID
  edition_name: string
  score: number | null
  percent: number | null
  claim_type: ClaimType
  formula: string
  total_weight: number
  threshold_percent: number
  lines: ReadinessLine[]
  gaps: ReadinessLine[]
  gap_count: number
}

export interface Award {
  id: UUID
  student_id: UUID
  student_name?: string | null
  skill_id: UUID
  skill_code: string | null
  skill_name: string | null
  level: number
  awarded_on: string
  source: 'rubric' | 'teacher' | 'artefact' | 'self'
  confidence: string | null
  status: 'proposed' | 'verified' | 'rejected' | 'revoked'
  evidence_note: string | null
  result_id: UUID | null
  session_id: UUID | null
  rubric_criterion: string | null
  verified_by_id: UUID | null
  verified_at: string | null
  revoked_at: string | null
  revoke_reason: string | null
  claim_type: ClaimType
}

export interface Check {
  key: string
  passed: boolean
  detail_en: string
  detail_ar: string
}

export interface RecommendationItem {
  edition_id: UUID
  edition_name: string
  competition_name: string
  tier: string
  event_starts: string
  readiness_percent: number | null
  gap_count: number
  gaps: ReadinessLine[]
  checks: Check[]
  reason_en: string
  reason_ar: string
  claim_type: ClaimType
}

export interface Recommendations {
  student_id: UUID
  threshold_percent: number
  recommended: RecommendationItem[]
  almost_ready: RecommendationItem[]
  not_recommended: RecommendationItem[]
}

export interface Message {
  id: UUID
  message_type: string
  template_id: UUID | null
  status: string
  title: string
  student_ids: UUID[]
  edition_id: UUID | null
  squad_id: UUID | null
  variables: Record<string, string>
  created_by_id: UUID | null
  previewed_at: string | null
  released_by_id: UUID | null
  released_at: string | null
  scheduled_for: string | null
  is_emergency: boolean
  emergency_reason: string | null
  created_at: string
  is_negative: boolean
}

export interface Template {
  id: UUID
  key: string
  message_type: string
  version: number
  status: 'draft' | 'approved' | 'retired'
  subject_en: string
  body_en: string
  subject_ar: string
  body_ar: string
  variables: string[]
  whatsapp_template_name: string | null
  whatsapp_status: string
  approved_at: string | null
}

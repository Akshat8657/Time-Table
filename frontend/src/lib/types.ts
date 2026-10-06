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

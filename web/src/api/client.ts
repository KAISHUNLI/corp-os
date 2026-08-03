export interface UserInfo {
  id: number
  username: string
  display_name: string
  department_code?: string | null
  role_code: string
  access_token?: string
  token_type?: string
}

export interface Citation {
  document_id: number
  title: string
  category: string
  snippet: string
  score: number
}

export interface ChatResult {
  session_id: number
  answer: string
  citations: Citation[]
}

export interface ChatMessage {
  id: number
  role: string
  content: string
  citations: Citation[]
  created_at?: string | null
}

export interface AuthProvider {
  id: string
  name: string
  type: string
  enabled: boolean
  login_url?: string | null
  hint?: string | null
  mock_enabled?: boolean | null
}

export interface ChatUploadResult {
  session_id: number
  document_id: number
  title: string
  kind: string
  tip: string
  needs_approval?: boolean
  request_id?: number | null
  status?: string
  sensitivity?: string
}

const TOKEN_KEY = 'corp_os_token'
const USER_KEY = 'corp_os_user'

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function clearAuthStorage() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function persistAuth(info: UserInfo) {
  if (info.access_token) {
    localStorage.setItem(TOKEN_KEY, info.access_token)
  }
  localStorage.setItem(USER_KEY, info.username)
}

function authHeaders(): HeadersInit {
  const headers: Record<string, string> = {}
  const token = getStoredToken()
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = {
    ...authHeaders(),
    ...(init?.headers || {}),
  }
  const res = await fetch(url, { ...init, headers })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = (await res.json()) as { detail?: string }
      if (data.detail) detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch {
      /* ignore */
    }
    if (res.status === 401) {
      clearAuthStorage()
    }
    throw new Error(detail || `请求失败 (${res.status})`)
  }
  return res.json() as Promise<T>
}

export const api = {
  authProviders() {
    return request<{ providers: AuthProvider[]; default_provider: string; demo_password_hint?: string | null }>(
      '/api/v1/auth/providers',
    )
  },
  login(username: string, password: string) {
    return request<UserInfo>('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
  },
  dingtalkMock(dingtalk_userid: string) {
    return request<UserInfo>('/api/v1/auth/dingtalk/mock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dingtalk_userid }),
    })
  },
  me() {
    return request<UserInfo>('/api/v1/auth/me')
  },
  chat(message: string, sessionId?: number | null) {
    return request<ChatResult>('/api/v1/chat/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId || null }),
    })
  },
  messages(sessionId: number) {
    return request<ChatMessage[]>(`/api/v1/chat/sessions/${sessionId}/messages`)
  },
  async upload(file: File, opts?: { note?: string; textOverride?: string; kind?: string; sessionId?: number | null }) {
    const form = new FormData()
    form.append('file', file)
    if (opts?.note) form.append('note', opts.note)
    if (opts?.textOverride?.trim()) form.append('text_override', opts.textOverride)
    if (opts?.kind) form.append('kind', opts.kind)
    if (opts?.sessionId) form.append('session_id', String(opts.sessionId))
    return request<ChatUploadResult>('/api/v1/chat/upload', {
      method: 'POST',
      body: form,
    })
  },
}

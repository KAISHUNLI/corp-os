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

export interface ChatSession {
  id: number
  title: string
  updated_at?: string | null
  created_at?: string | null
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
  let res: Response
  try {
    res = await fetch(url, { ...init, headers })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw err
    }
    if (err instanceof Error && err.name === 'AbortError') {
      throw err
    }
    throw err
  }
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

export function isAbortError(err: unknown): boolean {
  return (
    (err instanceof DOMException && err.name === 'AbortError') ||
    (err instanceof Error && err.name === 'AbortError')
  )
}

export const api = {
  login(username: string, password: string) {
    return request<UserInfo>('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
  },
  me() {
    return request<UserInfo>('/api/v1/auth/me')
  },
  chat(message: string, sessionId?: number | null, opts?: { signal?: AbortSignal }) {
    return request<ChatResult>('/api/v1/chat/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId || null }),
      signal: opts?.signal,
    })
  },  sessions() {
    return request<ChatSession[]>('/api/v1/chat/sessions')
  },
  messages(sessionId: number) {
    return request<ChatMessage[]>(`/api/v1/chat/sessions/${sessionId}/messages`)
  },
  deleteSession(sessionId: number) {
    return request<{ ok: boolean; session_id: number }>(`/api/v1/chat/sessions/${sessionId}`, {
      method: 'DELETE',
    })
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
  async downloadGenerated(fileId: string) {
    const res = await fetch(`/api/v1/chat/generated/${fileId}`, {
      headers: authHeaders(),
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(text || `下载失败 (${res.status})`)
    }
    const blob = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match = /filename\*?=(?:UTF-8''|")?([^\";]+)/i.exec(disposition)
    const filename = match ? decodeURIComponent(match[1].replace(/"/g, '')) : `${fileId}.bin`
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  },
  async downloadLibrary(documentId: number) {
    const res = await fetch(`/api/v1/chat/library/${documentId}`, {
      headers: authHeaders(),
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(text || `下载失败 (${res.status})`)
    }
    const blob = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match = /filename\*?=(?:UTF-8''|")?([^\";]+)/i.exec(disposition)
    const filename = match
      ? decodeURIComponent(match[1].replace(/"/g, ''))
      : `document-${documentId}`
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  },
  async fetchGeneratedBuffer(fileId: string): Promise<{
    buffer: ArrayBuffer
    filename: string
    contentType: string
  }> {
    const res = await fetch(`/api/v1/chat/generated/${fileId}`, {
      headers: authHeaders(),
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(text || `加载失败 (${res.status})`)
    }
    const buffer = await res.arrayBuffer()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match = /filename\*?=(?:UTF-8''|")?([^\";]+)/i.exec(disposition)
    const filename = match ? decodeURIComponent(match[1].replace(/"/g, '')) : `${fileId}.bin`
    const contentType = res.headers.get('Content-Type') || 'application/octet-stream'
    return { buffer, filename, contentType }
  },
  async fetchLibraryBuffer(documentId: number): Promise<{
    buffer: ArrayBuffer
    filename: string
    contentType: string
  }> {
    const res = await fetch(`/api/v1/chat/library/${documentId}`, {
      headers: authHeaders(),
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(text || `加载失败 (${res.status})`)
    }
    const buffer = await res.arrayBuffer()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match = /filename\*?=(?:UTF-8''|")?([^\";]+)/i.exec(disposition)
    const filename = match
      ? decodeURIComponent(match[1].replace(/"/g, ''))
      : `document-${documentId}`
    const contentType = res.headers.get('Content-Type') || 'application/octet-stream'
    return { buffer, filename, contentType }
  },
  previewGenerated(fileId: string) {
    return request<{ file_id: string; filename: string; kind: string; html: string }>(
      `/api/v1/chat/generated/${fileId}/preview`,
    )
  },
}

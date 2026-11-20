export interface Project { id: number; name: string; domain: string }
export interface Label { code: string; name: string; definition: string }
export interface Domain { id: string; name: string; description: string; labels: Label[]; examples: string[] }
export interface Doc { id: number; filename: string; n_chunks: number; created_at: string }
export interface Conversation { id: string; title: string; created_at: string }
export interface Source { chunk_id: number; filename: string; location: string; text: string; score: number }

export type Part =
  | { type: 'text'; text: string }
  | { type: 'tool_call'; id: string; name: string; args: Record<string, unknown> }
  | { type: 'tool_result'; id: string; name: string; sources: Source[]; content: string | null }
  | { type: 'error'; message: string }

export interface RunSummary {
  runs: number; errors: number; avg_latency_ms: number | null; avg_tool_calls: number | null
  avg_tokens_in: number | null; avg_tokens_out: number | null; verdicts: Record<string, number>
}

export type ReadinessStatus = 'met' | 'at_risk' | 'no_evidence'
export interface ReadinessItem { criterion: string; source: number | null; status: ReadinessStatus; finding: string; citations: number[] }
export interface Readiness { items: ReadinessItem[]; counts: Record<ReadinessStatus, number>; sources: Record<string, Source> }

export interface Message { role: 'user' | 'assistant'; parts: Part[] }

import type { Lang } from './i18n'

// The UI language travels as Accept-Language: the backend localizes its messages and domain packs by it.
let lang: Lang = 'zh'
export function setApiLang(next: Lang) { lang = next }
const withLang = (init?: RequestInit): RequestInit => ({ ...init, headers: { ...init?.headers, 'Accept-Language': lang } })

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, withLang(init))
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail ?? `${response.status} ${response.statusText}`)
  }
  return response.json()
}

const json = (body: unknown): RequestInit => ({
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})

export const api = {
  domains: () => request<Domain[]>('/domains'),
  projects: () => request<Project[]>('/projects'),
  createProject: (name: string, domain: string) => request<Project>('/projects', json({ name, domain })),
  documents: (projectId: number) => request<Doc[]>(`/projects/${projectId}/documents`),
  upload: (projectId: number, files: FileList) => {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    return request<{ filename: string; error?: string }[]>(`/projects/${projectId}/documents`, { method: 'POST', body: form })
  },
  deleteDocument: (id: number) => request(`/documents/${id}`, { method: 'DELETE' }),
  conversations: (projectId: number) => request<Conversation[]>(`/projects/${projectId}/conversations`),
  createConversation: (projectId: number) => request<Conversation>(`/projects/${projectId}/conversations`, { method: 'POST' }),
  messages: (conversationId: string) => request<Message[]>(`/conversations/${conversationId}/messages`),
  readiness: (projectId: number) => request<Readiness>(`/projects/${projectId}/readiness`, { method: 'POST' }),
  runs: (projectId: number) => request<{ summary: RunSummary }>(`/projects/${projectId}/runs?limit=1`),
}

/** POST a chat message and feed each server-sent event to `onPart` as it arrives. */
export async function streamChat(conversationId: string, message: string, onPart: (part: Part) => void) {
  const response = await fetch(`/api/conversations/${conversationId}/chat`, withLang(json({ message })))
  if (!response.ok || !response.body) throw new Error(`${response.status} ${response.statusText}`)
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value.replace(/\r\n/g, '\n')
    let boundary
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      let event = 'message', data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (!data || event === 'done') continue
      onPart({ type: event, ...JSON.parse(data) } as Part)
    }
  }
}

import { useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, setApiLang, streamChat, type Conversation, type Doc, type Domain, type Message, type Part, type Project, type Readiness, type RunSummary, type Source } from './api'
import { dictionaries, I18n, initialLang, saveLang, useT, type Lang } from './i18n'

type ToolCall = Extract<Part, { type: 'tool_call' }>
type ToolResult = Extract<Part, { type: 'tool_result' }>

const LEGACY_NEW_TITLE = '新会话'  // placeholder title stored by versions before i18n

export default function App() {
  const [lang, setLang] = useState<Lang>(() => { const l = initialLang(); setApiLang(l); return l })
  const t = dictionaries[lang]
  const [domains, setDomains] = useState<Domain[]>([])
  const [creating, setCreating] = useState(false)
  const [projects, setProjects] = useState<Project[]>([])
  const [projectId, setProjectId] = useState<number | null>(null)
  const [docs, setDocs] = useState<Doc[]>([])
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [source, setSource] = useState<Source | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [stats, setStats] = useState<RunSummary | null>(null)
  const [view, setView] = useState<'chat' | 'readiness'>('chat')
  const [readiness, setReadiness] = useState<Record<number, Readiness | 'running' | string>>({})

  useEffect(() => {
    setApiLang(lang)
    saveLang(lang)
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en'
    document.title = t.brand
    api.domains().then(setDomains)
  }, [lang, t])

  useEffect(() => {
    api.projects().then(list => { setProjects(list); setProjectId(list[0]?.id ?? null) })
  }, [])

  useEffect(() => {
    setDocs([]); setConversations([]); setConversationId(null); setMessages([]); setSource(null); setStats(null)
    setView('chat')
    if (projectId == null) return
    api.runs(projectId).then(r => setStats(r.summary))
    api.documents(projectId).then(setDocs)
    api.conversations(projectId).then(list => { setConversations(list); setConversationId(list[0]?.id ?? null) })
  }, [projectId])

  useEffect(() => {
    setSource(null)
    // While streaming into a just-created conversation, local state is the source of truth.
    if (conversationId && !busy) api.messages(conversationId).then(setMessages)
    else if (!conversationId) setMessages([])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId])

  const sources = useMemo(() => {
    const map = new Map<number, Source>()
    for (const message of messages)
      for (const part of message.parts)
        if (part.type === 'tool_result') part.sources.forEach(s => map.set(s.chunk_id, s))
    return map
  }, [messages])

  async function checkReadiness() {
    if (projectId == null) return
    const id = projectId
    setReadiness(r => ({ ...r, [id]: 'running' }))
    try {
      const result = await api.readiness(id)
      setReadiness(r => ({ ...r, [id]: result }))
    } catch (error) {
      setReadiness(r => ({ ...r, [id]: String(error) }))
    }
    api.runs(id).then(r => setStats(r.summary))
  }

  async function createProject(name: string, domain: string) {
    const project = await api.createProject(name, domain)
    setProjects(list => [project, ...list])
    setProjectId(project.id)
    setCreating(false)
  }

  async function upload(files: FileList | null) {
    if (!files?.length || projectId == null) return
    setNotice(t.parsing(files.length))
    try {
      const failed = (await api.upload(projectId, files)).filter(r => r.error)
      setNotice(failed.map(r => `${r.filename}: ${r.error}`).join('; '))
    } catch (error) {
      setNotice(String(error))
    }
    setDocs(await api.documents(projectId))
  }

  async function removeDoc(doc: Doc) {
    if (!confirm(t.confirmDelete(doc.filename))) return
    await api.deleteDocument(doc.id)
    setDocs(list => list.filter(d => d.id !== doc.id))
  }

  async function send(text: string) {
    if (projectId == null || busy) return
    let id = conversationId
    if (!id) {
      const conversation = await api.createConversation(projectId)
      id = conversation.id
      setConversations(list => [conversation, ...list])
    }
    setBusy(true)
    setConversationId(id)
    setMessages(list => [...list, { role: 'user', parts: [{ type: 'text', text }] }, { role: 'assistant', parts: [] }])
    const append = (part: Part) => setMessages(list => {
      const last = list[list.length - 1]
      const parts = [...last.parts]
      const tail = parts[parts.length - 1]
      if (part.type === 'text' && tail?.type === 'text') parts[parts.length - 1] = { ...tail, text: tail.text + part.text }
      else parts.push(part)
      return [...list.slice(0, -1), { ...last, parts }]
    })
    try {
      await streamChat(id, text, append)
    } catch (error) {
      append({ type: 'error', message: String(error) })
    } finally {
      setBusy(false)
      api.conversations(projectId).then(setConversations)
      api.runs(projectId).then(r => setStats(r.summary))
    }
  }

  const project = projects.find(p => p.id === projectId)
  const domain = domains.find(d => d.id === project?.domain)
  const labels = useMemo(() => Object.fromEntries((domain?.labels ?? []).map(l => [l.code, l.name])), [domain])

  return (
    <I18n.Provider value={t}>
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">{t.brand}
          <button className="lang" onClick={() => setLang(lang === 'zh' ? 'en' : 'zh')}>{t.switchTo}</button>
        </div>
        <section>
          <header>{t.projects} <button onClick={() => setCreating(c => !c)} title={t.newProject}>＋</button></header>
          {creating && <NewProject domains={domains} onCreate={createProject} onCancel={() => setCreating(false)} />}
          <select value={projectId ?? ''} onChange={e => setProjectId(Number(e.target.value))} disabled={!projects.length}>
            {!projects.length && <option>{t.noProjects}</option>}
            {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          {domain && <div className="muted small" title={domain.description}>{t.domain}: {domain.name}</div>}
        </section>
        {project && <>
          <section>
            <header>{t.conversations} <button onClick={() => { setConversationId(null); setView('chat') }} disabled={busy} title={t.newConversation}>＋</button></header>
            <ul className="list">
              {conversations.map(c => (
                <li key={c.id} className={view === 'chat' && c.id === conversationId ? 'active' : ''}
                  onClick={() => { if (!busy) { setConversationId(c.id); setView('chat') } }}>{c.title && c.title !== LEGACY_NEW_TITLE ? c.title : t.newConversation}</li>
              ))}
            </ul>
          </section>
          <button className={`tool ${view === 'readiness' ? 'active' : ''}`} onClick={() => setView('readiness')}>
            ✓ {t.readiness}
          </button>
          <section className="grow">
            <header>
              {t.documents} <span className="muted">{docs.length}</span>
              <label className="button">{t.upload}<input type="file" multiple hidden accept=".pdf,.docx,.xlsx,.md,.txt"
                onChange={e => { upload(e.target.files); e.target.value = '' }} /></label>
            </header>
            {notice && <p className="notice">{notice}</p>}
            <ul className="list docs">
              {docs.map(d => (
                <li key={d.id} title={t.chunks(d.n_chunks)}>
                  <span>{d.filename}</span>
                  <button className="icon" onClick={() => removeDoc(d)} title={t.remove}>×</button>
                </li>
              ))}
            </ul>
          </section>
          {!!stats?.runs && (
            <div className="muted small" title="GET /api/projects/{id}/runs">
              {t.stats(stats.runs, ((stats.avg_latency_ms ?? 0) / 1000).toFixed(1),
                Math.round((stats.avg_tokens_in ?? 0) + (stats.avg_tokens_out ?? 0)).toLocaleString())}
            </div>
          )}
        </>}
      </aside>

      {view === 'readiness' && project ? (
        <main className="chat">
          <ReadinessView state={readiness[project.id]} onRun={checkReadiness} onBack={() => setView('chat')}
            onCite={id => { const r = readiness[project.id]; setSource(typeof r === 'object' ? r.sources[id] ?? null : null) }} />
        </main>
      ) : (
      <main className="chat">
        <div className="messages">
          {!messages.length && <Empty hasProject={!!project} hasDocs={docs.length > 0} examples={domain?.examples ?? []} onAsk={send} />}
          {messages.map((m, i) => (
            <div key={i} className={`message ${m.role}`}>
              {m.role === 'user'
                ? <div className="bubble">{m.parts.map(p => p.type === 'text' ? p.text : '').join('')}</div>
                : <AssistantParts parts={m.parts} pending={busy && i === messages.length - 1} labels={labels}
                    onCite={id => setSource(sources.get(id) ?? null)} />}
            </div>
          ))}
        </div>
        <Composer disabled={busy || !project} onSend={send} />
      </main>
      )}

      {source && (
        <aside className="source">
          <header><b>{source.filename}</b><button className="icon" onClick={() => setSource(null)}>×</button></header>
          <div className="muted">{source.location} · c{source.chunk_id}</div>
          <pre>{source.text}</pre>
        </aside>
      )}
    </div>
    </I18n.Provider>
  )
}

function AssistantParts({ parts, pending, labels, onCite }: {
  parts: Part[]; pending: boolean; labels: Record<string, string>; onCite: (id: number) => void
}) {
  const t = useT()
  const results = new Map(parts.filter((p): p is ToolResult => p.type === 'tool_result').map(p => [p.id, p]))
  return (
    <div className="assistant-body">
      {parts.map((part, i) => {
        if (part.type === 'text') return <Answer key={i} text={part.text} labels={labels} onCite={onCite} />
        if (part.type === 'error') return <div key={i} className="error">{part.message}</div>
        if (part.type === 'tool_call') return <Step key={i} call={part} result={results.get(part.id)} />
        return null
      })}
      {pending && <div className="muted">{t.thinking}</div>}
    </div>
  )
}

function Step({ call, result }: { call: ToolCall; result?: ToolResult }) {
  const t = useT()
  const query = typeof call.args.query === 'string' ? call.args.query : ''
  return (
    <details className="step">
      <summary>
        {result ? '✓' : '…'} {t.tools[call.name] ?? call.name}{query && <>: <code>{query}</code></>}
        {result && <span className="muted"> · {result.sources.length ? t.results(result.sources.length) : t.done}</span>}
      </summary>
      {result?.sources.map(s => <div key={s.chunk_id} className="hit"><b>c{s.chunk_id}</b> {s.filename} · {s.location}</div>)}
      {result?.content && <pre>{result.content}</pre>}
    </details>
  )
}

function Answer({ text, labels, onCite }: { text: string; labels: Record<string, string>; onCite: (id: number) => void }) {
  // [c12] and [[verdict:out_of_scope]] become markdown links that the renderer below turns into a citation chip
  // and a verdict badge (named in the UI language). A half-streamed "[[verd" at the end is hidden until complete.
  const linked = text
    .replace(/\[\[\s*verdict\s*:\s*([a-z_]+)\s*\]\]/gi, (_, code: string) =>
      `[${labels[code.toLowerCase()] ?? code}](#verdict-${code.toLowerCase()})`)
    .replace(/\[\[[^\]]*$/, '')
    .replace(/\[c(\d+)\]/g, '[c$1](#cite-$1)')
  return (
    <div className="answer">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        a: ({ href, children }) => href?.startsWith('#cite-')
          ? <button className="cite" onClick={() => onCite(Number(href.slice(6)))}>{children}</button>
          : href?.startsWith('#verdict-')
            ? <span className={`verdict verdict-${href.slice(9)}`}>{children}</span>
            : <a href={href} target="_blank" rel="noreferrer">{children}</a>,
      }}>{linked}</ReactMarkdown>
    </div>
  )
}

function ReadinessView({ state, onRun, onBack, onCite }: {
  state: Readiness | 'running' | string | undefined; onRun: () => void; onBack: () => void; onCite: (id: number) => void
}) {
  const t = useT()
  const running = state === 'running'
  return (
    <div className="messages readiness">
      <button className="link" onClick={onBack}>{t.backToChat}</button>
      <h2>{t.readiness}</h2>
      <p className="muted">{t.readinessIntro}</p>
      <button className="primary" onClick={onRun} disabled={running}>{state && !running ? t.readinessRerun : t.readinessRun}</button>
      {running && <p className="muted">{t.readinessRunning}</p>}
      {typeof state === 'string' && !running && <p className="error">{state}</p>}
      {typeof state === 'object' && <>
        <p className="counts">
          {(['at_risk', 'no_evidence', 'met'] as const).map(s =>
            <span key={s} className={`status status-${s}`}>{t.statuses[s]} {state.counts[s]}</span>)}
        </p>
        <table>
          <thead><tr><th>{t.criterion}</th><th>{t.status}</th><th>{t.finding}</th></tr></thead>
          <tbody>
            {state.items.map((item, i) => (
              <tr key={i}>
                <td>{item.criterion}{item.source != null &&
                  <button className="cite" onClick={() => onCite(item.source!)}>c{item.source}</button>}</td>
                <td><span className={`status status-${item.status}`}>{t.statuses[item.status]}</span></td>
                <td>{item.finding} {item.citations.map(c =>
                  <button key={c} className="cite" onClick={() => onCite(c)}>c{c}</button>)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </>}
    </div>
  )
}

function Composer({ disabled, onSend }: { disabled: boolean; onSend: (text: string) => void }) {
  const t = useT()
  const [text, setText] = useState('')
  const submit = () => {
    if (!text.trim() || disabled) return
    onSend(text.trim())
    setText('')
  }
  return (
    <div className="composer">
      <textarea value={text} rows={2}
        placeholder={t.placeholder}
        onChange={e => setText(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit() }
        }} />
      <button onClick={submit} disabled={disabled || !text.trim()}>{t.send}</button>
    </div>
  )
}

function NewProject({ domains, onCreate, onCancel }: {
  domains: Domain[]; onCreate: (name: string, domain: string) => void; onCancel: () => void
}) {
  const t = useT()
  const [name, setName] = useState('')
  const [domain, setDomain] = useState('')
  const chosen = domains.find(d => d.id === domain) ?? domains[0]
  const submit = () => { if (name.trim() && chosen) onCreate(name.trim(), chosen.id) }
  return (
    <form className="new-project" onSubmit={e => { e.preventDefault(); submit() }}>
      <input autoFocus placeholder={t.projectName} value={name} onChange={e => setName(e.target.value)}
        onKeyDown={e => e.key === 'Escape' && onCancel()} />
      <select value={chosen?.id ?? ''} onChange={e => setDomain(e.target.value)}>
        {domains.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
      </select>
      {chosen && <p className="muted small">{chosen.description}<br />{t.verdicts}: {chosen.labels.map(l => l.name).join(' / ')}</p>}
      <div className="row">
        <button type="button" onClick={onCancel}>{t.cancel}</button>
        <button type="submit" className="primary" disabled={!name.trim()}>{t.create}</button>
      </div>
    </form>
  )
}

function Empty({ hasProject, hasDocs, examples, onAsk }: {
  hasProject: boolean; hasDocs: boolean; examples: string[]; onAsk: (text: string) => void
}) {
  const t = useT()
  return (
    <div className="empty">
      <h2>{t.brand}</h2>
      {hasDocs
        ? examples.map(q => <button key={q} className="example" onClick={() => onAsk(q)}>{q}</button>)
        : <p className="muted">{hasProject ? t.emptyNoDocs : t.emptyNoProject}</p>}
    </div>
  )
}

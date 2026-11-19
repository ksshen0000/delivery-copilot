import { useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, streamChat, type Conversation, type Doc, type Domain, type Message, type Part, type Project, type Source } from './api'

type ToolCall = Extract<Part, { type: 'tool_call' }>
type ToolResult = Extract<Part, { type: 'tool_result' }>

const TOOL_LABELS: Record<string, string> = { search_documents: '检索文档', list_documents: '查看文档列表' }

export default function App() {
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

  useEffect(() => {
    api.domains().then(setDomains)
    api.projects().then(list => { setProjects(list); setProjectId(list[0]?.id ?? null) })
  }, [])

  useEffect(() => {
    setDocs([]); setConversations([]); setConversationId(null); setMessages([]); setSource(null)
    if (projectId == null) return
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

  async function createProject(name: string, domain: string) {
    const project = await api.createProject(name, domain)
    setProjects(list => [project, ...list])
    setProjectId(project.id)
    setCreating(false)
  }

  async function upload(files: FileList | null) {
    if (!files?.length || projectId == null) return
    setNotice(`正在解析 ${files.length} 个文件…`)
    try {
      const failed = (await api.upload(projectId, files)).filter(r => r.error)
      setNotice(failed.map(r => `${r.filename}：${r.error}`).join('；'))
    } catch (error) {
      setNotice(String(error))
    }
    setDocs(await api.documents(projectId))
  }

  async function removeDoc(doc: Doc) {
    if (!confirm(`删除 ${doc.filename}？`)) return
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
    }
  }

  const project = projects.find(p => p.id === projectId)
  const domain = domains.find(d => d.id === project?.domain)

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">交付实施助手</div>
        <section>
          <header>项目 <button onClick={() => setCreating(c => !c)} title="新建项目">＋</button></header>
          {creating && <NewProject domains={domains} onCreate={createProject} onCancel={() => setCreating(false)} />}
          <select value={projectId ?? ''} onChange={e => setProjectId(Number(e.target.value))} disabled={!projects.length}>
            {!projects.length && <option>先新建项目</option>}
            {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          {domain && <div className="muted small" title={domain.description}>领域：{domain.name}</div>}
        </section>
        {project && <>
          <section>
            <header>会话 <button onClick={() => setConversationId(null)} disabled={busy} title="新会话">＋</button></header>
            <ul className="list">
              {conversations.map(c => (
                <li key={c.id} className={c.id === conversationId ? 'active' : ''}
                  onClick={() => !busy && setConversationId(c.id)}>{c.title}</li>
              ))}
            </ul>
          </section>
          <section className="grow">
            <header>
              文档 <span className="muted">{docs.length}</span>
              <label className="button">上传<input type="file" multiple hidden accept=".pdf,.docx,.xlsx,.md,.txt"
                onChange={e => { upload(e.target.files); e.target.value = '' }} /></label>
            </header>
            {notice && <p className="notice">{notice}</p>}
            <ul className="list docs">
              {docs.map(d => (
                <li key={d.id} title={`${d.n_chunks} 个片段`}>
                  <span>{d.filename}</span>
                  <button className="icon" onClick={() => removeDoc(d)} title="删除">×</button>
                </li>
              ))}
            </ul>
          </section>
        </>}
      </aside>

      <main className="chat">
        <div className="messages">
          {!messages.length && <Empty hasProject={!!project} hasDocs={docs.length > 0} examples={domain?.examples ?? []} onAsk={send} />}
          {messages.map((m, i) => (
            <div key={i} className={`message ${m.role}`}>
              {m.role === 'user'
                ? <div className="bubble">{m.parts.map(p => p.type === 'text' ? p.text : '').join('')}</div>
                : <AssistantParts parts={m.parts} pending={busy && i === messages.length - 1}
                    onCite={id => setSource(sources.get(id) ?? null)} />}
            </div>
          ))}
        </div>
        <Composer disabled={busy || !project} onSend={send} />
      </main>

      {source && (
        <aside className="source">
          <header><b>{source.filename}</b><button className="icon" onClick={() => setSource(null)}>×</button></header>
          <div className="muted">{source.location} · c{source.chunk_id}</div>
          <pre>{source.text}</pre>
        </aside>
      )}
    </div>
  )
}

function AssistantParts({ parts, pending, onCite }: { parts: Part[]; pending: boolean; onCite: (id: number) => void }) {
  const results = new Map(parts.filter((p): p is ToolResult => p.type === 'tool_result').map(p => [p.id, p]))
  return (
    <div className="assistant-body">
      {parts.map((part, i) => {
        if (part.type === 'text') return <Answer key={i} text={part.text} onCite={onCite} />
        if (part.type === 'error') return <div key={i} className="error">{part.message}</div>
        if (part.type === 'tool_call') return <Step key={i} call={part} result={results.get(part.id)} />
        return null
      })}
      {pending && <div className="muted">思考中…</div>}
    </div>
  )
}

function Step({ call, result }: { call: ToolCall; result?: ToolResult }) {
  const query = typeof call.args.query === 'string' ? call.args.query : ''
  return (
    <details className="step">
      <summary>
        {result ? '✓' : '…'} {TOOL_LABELS[call.name] ?? call.name}{query && <>：<code>{query}</code></>}
        {result && <span className="muted"> · {result.sources.length ? `${result.sources.length} 条结果` : '完成'}</span>}
      </summary>
      {result?.sources.map(s => <div key={s.chunk_id} className="hit"><b>c{s.chunk_id}</b> {s.filename} · {s.location}</div>)}
      {result?.content && <pre>{result.content}</pre>}
    </details>
  )
}

function Answer({ text, onCite }: { text: string; onCite: (id: number) => void }) {
  // [c12] becomes a markdown link that the renderer below turns into a citation chip.
  const linked = text.replace(/\[c(\d+)\]/g, '[c$1](#cite-$1)')
  return (
    <div className="answer">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        a: ({ href, children }) => href?.startsWith('#cite-')
          ? <button className="cite" onClick={() => onCite(Number(href.slice(6)))}>{children}</button>
          : <a href={href} target="_blank" rel="noreferrer">{children}</a>,
      }}>{linked}</ReactMarkdown>
    </div>
  )
}

function Composer({ disabled, onSend }: { disabled: boolean; onSend: (text: string) => void }) {
  const [text, setText] = useState('')
  const submit = () => {
    if (!text.trim() || disabled) return
    onSend(text.trim())
    setText('')
  }
  return (
    <div className="composer">
      <textarea value={text} rows={2}
        placeholder="问项目里的问题，或让我判断某个需求是否在合同范围内（Enter 发送，Shift+Enter 换行）"
        onChange={e => setText(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit() }
        }} />
      <button onClick={submit} disabled={disabled || !text.trim()}>发送</button>
    </div>
  )
}

function NewProject({ domains, onCreate, onCancel }: {
  domains: Domain[]; onCreate: (name: string, domain: string) => void; onCancel: () => void
}) {
  const [name, setName] = useState('')
  const [domain, setDomain] = useState('')
  const chosen = domains.find(d => d.id === domain) ?? domains[0]
  const submit = () => { if (name.trim() && chosen) onCreate(name.trim(), chosen.id) }
  return (
    <form className="new-project" onSubmit={e => { e.preventDefault(); submit() }}>
      <input autoFocus placeholder="项目名称" value={name} onChange={e => setName(e.target.value)}
        onKeyDown={e => e.key === 'Escape' && onCancel()} />
      <select value={chosen?.id ?? ''} onChange={e => setDomain(e.target.value)}>
        {domains.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
      </select>
      {chosen && <p className="muted small">{chosen.description}<br />结论：{Object.keys(chosen.labels).join(' / ')}</p>}
      <div className="row">
        <button type="button" onClick={onCancel}>取消</button>
        <button type="submit" className="primary" disabled={!name.trim()}>创建</button>
      </div>
    </form>
  )
}

function Empty({ hasProject, hasDocs, examples, onAsk }: {
  hasProject: boolean; hasDocs: boolean; examples: string[]; onAsk: (text: string) => void
}) {
  return (
    <div className="empty">
      <h2>交付实施助手</h2>
      {hasDocs
        ? examples.map(q => <button key={q} className="example" onClick={() => onAsk(q)}>{q}</button>)
        : <p className="muted">{hasProject ? '在左侧' : '先在左侧新建项目，再'}上传合同、技术协议、会议纪要、日报等文档（PDF / Word / Excel / Markdown）。</p>}
    </div>
  )
}

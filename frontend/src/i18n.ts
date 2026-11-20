import { createContext, useContext } from 'react'

export type Lang = 'zh' | 'en'

const zh = {
  brand: '交付实施助手',
  projects: '项目',
  newProject: '新建项目',
  noProjects: '先新建项目',
  domain: '领域',
  projectName: '项目名称',
  verdicts: '结论',
  cancel: '取消',
  create: '创建',
  conversations: '会话',
  newConversation: '新会话',
  documents: '文档',
  upload: '上传',
  remove: '删除',
  chunks: (n: number) => `${n} 个片段`,
  parsing: (n: number) => `正在解析 ${n} 个文件…`,
  confirmDelete: (name: string) => `删除 ${name}？`,
  thinking: '思考中…',
  results: (n: number) => `${n} 条结果`,
  done: '完成',
  send: '发送',
  placeholder: '问项目里的问题，或让我判断某个需求是否在合同范围内（Enter 发送，Shift+Enter 换行）',
  emptyNoProject: '先在左侧新建项目，再上传合同、技术协议、会议纪要、日报等文档（PDF / Word / Excel / Markdown）。',
  emptyNoDocs: '在左侧上传合同、技术协议、会议纪要、日报等文档（PDF / Word / Excel / Markdown）。',
  tools: { search_documents: '检索文档', list_documents: '查看文档列表' } as Record<string, string>,
  switchTo: 'English',
  readiness: '验收就绪度检查',
  readinessIntro: '从合同和技术协议中列出验收标准，逐条到日报、问题清单里找证据，判断是否已满足。',
  readinessRun: '开始检查',
  readinessRerun: '重新检查',
  readinessRunning: '正在逐条核查验收标准…（约 15 秒）',
  backToChat: '← 返回对话',
  criterion: '验收标准',
  status: '状态',
  finding: '发现',
  statuses: { met: '已满足', at_risk: '有风险', no_evidence: '无证据' } as Record<string, string>,
  stats: (runs: number, seconds: string, tokens: string) => `${runs} 次问答 · 平均 ${seconds} 秒 · ${tokens} tokens/次`,
}

const en: typeof zh = {
  brand: 'Delivery Copilot',
  projects: 'Projects',
  newProject: 'New project',
  noProjects: 'Create a project first',
  domain: 'Domain',
  projectName: 'Project name',
  verdicts: 'Verdicts',
  cancel: 'Cancel',
  create: 'Create',
  conversations: 'Chats',
  newConversation: 'New chat',
  documents: 'Documents',
  upload: 'Upload',
  remove: 'Delete',
  chunks: n => `${n} chunk${n === 1 ? '' : 's'}`,
  parsing: n => `Parsing ${n} file${n === 1 ? '' : 's'}…`,
  confirmDelete: name => `Delete ${name}?`,
  thinking: 'Thinking…',
  results: n => `${n} result${n === 1 ? '' : 's'}`,
  done: 'done',
  send: 'Send',
  placeholder: 'Ask about the project, or whether a request is within the contract scope (Enter to send, Shift+Enter for a new line)',
  emptyNoProject: 'Create a project on the left, then upload the contract, technical agreement, meeting minutes and reports (PDF / Word / Excel / Markdown).',
  emptyNoDocs: 'Upload the contract, technical agreement, meeting minutes and reports on the left (PDF / Word / Excel / Markdown).',
  tools: { search_documents: 'Search documents', list_documents: 'List documents' },
  switchTo: '中文',
  readiness: 'Acceptance readiness',
  readinessIntro: 'Lists the acceptance criteria from the contract and technical agreement, then checks each one against the site reports and issue log.',
  readinessRun: 'Run check',
  readinessRerun: 'Run again',
  readinessRunning: 'Checking each acceptance criterion… (about 15 s)',
  backToChat: '← Back to chat',
  criterion: 'Criterion',
  status: 'Status',
  finding: 'Finding',
  statuses: { met: 'Met', at_risk: 'At risk', no_evidence: 'No evidence' },
  stats: (runs, seconds, tokens) => `${runs} run${runs === 1 ? '' : 's'} · avg ${seconds} s · ${tokens} tokens/run`,
}

export const dictionaries = { zh, en }
export type Dictionary = typeof zh

export const I18n = createContext<Dictionary>(zh)
export const useT = () => useContext(I18n)

const KEY = 'delivery-copilot.lang'

/** Saved choice, else the browser language. Storage can be unavailable (private mode), so guard it. */
export function initialLang(): Lang {
  try {
    const saved = localStorage.getItem(KEY)
    if (saved === 'zh' || saved === 'en') return saved
  } catch { /* fall through */ }
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

export function saveLang(lang: Lang) {
  try { localStorage.setItem(KEY, lang) } catch { /* not persisted */ }
}

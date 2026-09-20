import { useEffect, useRef, useState } from 'react'
import {
  ArrowUp,
  Bot,
  Check,
  ChevronDown,
  Database,
  FileSearch,
  Link2,
  LogOut,
  Menu,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-react'

const suggestions = [
  { icon: '◌', label: 'Nghỉ phép', text: 'Tôi cần xin nghỉ phép trước bao lâu?' },
  { icon: '↗', label: 'Công tác phí', text: 'Quy trình công tác phí thế nào?' },
  { icon: '⌁', label: 'Liên hệ HR', text: 'Liên hệ phòng Nhân sự ở đâu?' },
]

const TOKEN_KEY = 'va_token'

async function readApiResponse(response) {
  const raw = await response.text()
  try {
    return raw ? JSON.parse(raw) : {}
  } catch {
    return { detail: raw || `Backend trả về HTTP ${response.status}` }
  }
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY))
  const [user, setUser] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)
  const [authMode, setAuthMode] = useState('login')
  const [authForm, setAuthForm] = useState({ email: '', password: '', display_name: '' })
  const [authLoading, setAuthLoading] = useState(false)
  const [authError, setAuthError] = useState('')

  const [threads, setThreads] = useState([])
  const [activeThreadId, setActiveThreadId] = useState(null)

  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState([])
  const [department, setDepartment] = useState('')
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(false)
  const [debugQuery, setDebugQuery] = useState('')
  const [debugRows, setDebugRows] = useState([])
  const [debugging, setDebugging] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const scrollAnchorRef = useRef(null)

  useEffect(() => {
    loadHealth()
  }, [])

  useEffect(() => {
    scrollAnchorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, loading])

  useEffect(() => {
    if (!token) {
      setAuthChecked(true)
      return
    }
    ;(async () => {
      try {
        const response = await fetch('/api/auth/me', { headers: authHeaders(token) })
        if (!response.ok) throw new Error()
        setUser(await response.json())
        loadThreads(token)
      } catch {
        clearSession()
      } finally {
        setAuthChecked(true)
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  function authHeaders(overrideToken = token) {
    return overrideToken ? { Authorization: `Bearer ${overrideToken}` } : {}
  }

  async function loadHealth() {
    try {
      const response = await fetch('/api/health')
      setHealth(await response.json())
    } catch {
      setHealth({ status: 'offline' })
    }
  }

  async function loadThreads(activeToken = token) {
    try {
      const response = await fetch('/api/chat/threads', { headers: authHeaders(activeToken) })
      if (response.ok) setThreads(await response.json())
    } catch {
      // best-effort
    }
  }

  async function openThread(id) {
    setActiveThreadId(id)
    setMobileNav(false)
    try {
      const response = await fetch(`/api/chat/threads/${id}/messages`, { headers: authHeaders() })
      const rows = response.ok ? await response.json() : []
      setMessages(rows.map((row) => ({ role: row.role, text: row.content })))
    } catch {
      setMessages([])
    }
  }

  function startNewThread() {
    setActiveThreadId(null)
    setMessages([])
    setMobileNav(false)
  }

  async function deleteThread(id, event) {
    event.stopPropagation()
    setThreads((current) => current.filter((thread) => thread.id !== id))
    if (activeThreadId === id) startNewThread()
    try {
      await fetch(`/api/chat/threads/${id}`, { method: 'DELETE', headers: authHeaders() })
    } catch {
      // best-effort
    }
  }

  function clearSession() {
    localStorage.removeItem(TOKEN_KEY)
    setToken(null)
    setUser(null)
    setThreads([])
    setActiveThreadId(null)
    setMessages([])
  }

  async function handleAuthSubmit(event) {
    event.preventDefault()
    setAuthLoading(true)
    setAuthError('')
    try {
      const path = authMode === 'login' ? '/api/auth/login' : '/api/auth/signup'
      const body = authMode === 'login'
        ? { email: authForm.email, password: authForm.password }
        : authForm
      const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await readApiResponse(response)
      if (!response.ok) throw new Error(data.detail || 'Không thể xác thực')
      localStorage.setItem(TOKEN_KEY, data.access_token)
      setToken(data.access_token)
    } catch (error) {
      setAuthError(error.message)
    } finally {
      setAuthLoading(false)
    }
  }

  async function ask(value = question) {
    const text = value.trim()
    if (!text || loading) return
    setQuestion('')
    const history = messages
      .slice(-6)
      .map((message) => ({ role: message.role, content: message.text }))
    setMessages((current) => [...current, { role: 'user', text }])
    setLoading(true)
    try {
      const response = await fetch('/api/chat-staff', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          question: text,
          user_department: department || null,
          history,
          thread_id: activeThreadId,
        }),
      })
      if (response.status === 401) {
        clearSession()
        return
      }
      const data = await readApiResponse(response)
      if (!response.ok) {
        throw new Error(data.detail || `Backend trả về HTTP ${response.status}`)
      }
      setMessages((current) => [...current, { role: 'assistant', text: data.answer, citations: data.citations || [] }])
      if (data.thread_id) {
        setActiveThreadId(data.thread_id)
        loadThreads()
      }
    } catch (error) {
      setMessages((current) => [...current, { role: 'assistant', text: `Không thể xử lý câu hỏi. ${error.message || 'Kiểm tra backend FastAPI ở port 8000.'}` }])
    } finally {
      setLoading(false)
    }
  }

  async function debugSearch(event) {
    event?.preventDefault()
    if (!debugQuery.trim()) return
    setDebugging(true)
    try {
      const response = await fetch(`/api/debug/search?q=${encodeURIComponent(debugQuery)}`)
      setDebugRows(await response.json())
    } catch {
      setDebugRows([])
    } finally {
      setDebugging(false)
    }
  }

  const statusOnline = health?.status === 'ok'

  if (!authChecked) {
    return <div className="grid min-h-screen place-items-center bg-[#f7f5ef] text-sm text-[#7a8889]">Đang tải...</div>
  }

  if (!user) {
    return (
      <AuthScreen
        mode={authMode}
        setMode={setAuthMode}
        form={authForm}
        setForm={setAuthForm}
        onSubmit={handleAuthSubmit}
        loading={authLoading}
        error={authError}
      />
    )
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#f7f5ef] text-ink">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />

      <aside className={`${mobileNav ? 'mobile-open' : ''} sidebar fixed inset-y-0 left-0 z-30 flex w-[272px] -translate-x-full flex-col border-r border-[#dce3de] bg-[#123d52] px-5 py-6 text-white transition-transform lg:static lg:h-screen lg:translate-x-0`}>
        <div className="flex items-center justify-between">
          <Brand light />
          <button className="icon-button light lg:hidden" onClick={() => setMobileNav(false)} aria-label="Đóng menu"><X size={18} /></button>
        </div>
        <button onClick={startNewThread} className="mt-7 flex w-full items-center justify-center gap-2 rounded-lg border border-white/15 bg-white/5 py-2.5 text-sm font-semibold text-white transition hover:bg-white/10">
          <Plus size={16} /> Cuộc trò chuyện mới
        </button>
        <div className="mt-6 min-h-0 flex-1 overflow-y-auto">
          <div className="nav-caption">Lịch sử</div>
          <div className="mt-2 space-y-1">
            {threads.length === 0 && <p className="px-3 py-2 text-xs leading-5 text-[#8fb3af]">Chưa có cuộc trò chuyện nào.</p>}
            {threads.map((thread) => (
              <div
                key={thread.id}
                onClick={() => openThread(thread.id)}
                className={`thread-item flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2.5 text-sm transition ${thread.id === activeThreadId ? 'bg-white/10 text-white' : 'text-[#b3d0cc] hover:bg-white/5'}`}
              >
                <span className="truncate">{thread.title || 'Cuộc trò chuyện mới'}</span>
                <button onClick={(event) => deleteThread(thread.id, event)} className="thread-delete shrink-0 text-[#8fb3af] hover:text-white" aria-label="Xoá cuộc trò chuyện"><X size={13} /></button>
              </div>
            ))}
          </div>
        </div>
        <div className="mt-4 flex items-center gap-2.5 border-t border-white/10 pt-4">
          <div className="avatar user-avatar !h-8 !w-8 shrink-0">{(user.display_name || user.email || '?').slice(0, 1).toUpperCase()}</div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-bold text-white">{user.display_name || 'Nhân viên'}</p>
            <p className="truncate text-[10px] text-[#9ec3c1]">{user.email}</p>
          </div>
          <button onClick={clearSession} className="icon-button light !h-8 !w-8 shrink-0" aria-label="Đăng xuất"><LogOut size={14} /></button>
        </div>
      </aside>
      {mobileNav && <button className="fixed inset-0 z-20 bg-[#0b2632]/40 lg:hidden" onClick={() => setMobileNav(false)} aria-label="Đóng menu" />}

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="mobile-header lg:hidden">
          <Brand />
          <button className="icon-button" onClick={() => setMobileNav(true)} aria-label="Mở menu"><Menu size={19} /></button>
        </header>

        <div className="flex min-h-0 flex-1 gap-5 p-4 sm:p-6">
          <section className="paper-panel flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl">
            <div className="flex items-center justify-between border-b border-[#e2e7e1] px-5 py-4 sm:px-7">
              <div>
                <p className="text-sm font-bold text-ink">Staff conversation</p>
                <p className="mt-1 text-[11px] text-[#889598]">Trợ lý nội bộ · Trường Việt Anh</p>
              </div>
              <div className="flex items-center gap-2">
                <div className="hidden items-center gap-2 rounded-full bg-[#e8f4ed] px-3 py-1.5 text-[10px] font-bold uppercase tracking-[.12em] text-[#24725f] sm:flex"><Sparkles size={13} /> RAG ready</div>
                <div className="flex items-center gap-2 rounded-full border border-[#dce3de] bg-white/70 px-3 py-1.5 text-[11px] text-[#607175]"><span className={`status-dot ${statusOnline ? 'online' : ''}`} />{statusOnline ? `${health.approved_notes || 0}/${health.knowledge_notes || 0} notes` : 'Offline'}</div>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-7 sm:px-8">
              <div className="mx-auto max-w-[820px]">
                {messages.length === 0 ? <EmptyState onAsk={ask} /> : (
                  <div className="space-y-7">
                    {messages.map((message, index) => <Message key={`${message.role}-${index}`} message={message} />)}
                    {loading && <div className="flex gap-3"><div className="avatar"><Bot size={16} /></div><div className="bubble assistant-bubble"><span className="typing"><i /><i /><i /></span> Đang tra cứu trong Vault...</div></div>}
                    <div ref={scrollAnchorRef} />
                  </div>
                )}
              </div>
            </div>
            <form className="border-t border-[#e2e7e1] bg-[#fcfbf7] p-4 sm:p-5" onSubmit={(event) => { event.preventDefault(); ask() }}>
              <div className="mx-auto max-w-[820px]">
                <div className="relative flex items-center">
                  <input value={question} onChange={(event) => setQuestion(event.target.value)} className="h-14 w-full rounded-xl border border-[#d8dfda] bg-white pl-4 pr-16 text-sm text-ink outline-none transition placeholder:text-[#9aa5a5] focus:border-[#3c8b79] focus:ring-4 focus:ring-[#3c8b79]/10" placeholder="Hỏi về quy định nội bộ..." />
                  <button disabled={loading || !question.trim()} className="absolute right-2 grid h-10 w-10 place-items-center rounded-lg bg-[#e9795c] text-white transition hover:bg-[#d9654a] disabled:cursor-not-allowed disabled:opacity-35" aria-label="Gửi câu hỏi"><ArrowUp size={18} strokeWidth={2.5} /></button>
                </div>
                <div className="mt-3 flex items-center justify-between px-1 text-[10px] text-[#9aa5a5]"><span>Thêm/duyệt dữ liệu qua trang <a href="/admin" className="underline">/admin</a></span><span>Vietnamese · Internal</span></div>
              </div>
            </form>
          </section>

          <aside className="hidden w-[300px] shrink-0 flex-col gap-4 overflow-y-auto xl:flex">
            <section className="paper-panel rounded-2xl p-5">
              <div className="flex items-start justify-between"><div><p className="panel-kicker">Your context</p><h2 className="panel-title mt-1">Phạm vi tìm kiếm</h2></div><ShieldCheck className="text-[#3d8d79]" size={20} /></div>
              <p className="mt-3 text-xs leading-5 text-[#7c898a]">Thu hẹp phạm vi tra cứu theo phòng ban (không bắt buộc).</p>
              <Field label="Phòng ban"><Select value={department} onChange={(event) => setDepartment(event.target.value)}><option value="">Tất cả phòng ban</option><option>HR</option><option>Finance</option><option>Academic</option><option>Admin</option></Select></Field>
              <div className="mt-4 flex items-center gap-2 border-t border-[#e7ebe6] pt-4 text-[11px] text-[#718081]"><Check size={14} className="text-[#3d8d79]" /> Approved sources only</div>
            </section>
            <section className="paper-panel rounded-2xl p-5">
              <div className="flex items-start justify-between"><div><p className="panel-kicker">Vault control</p><h2 className="panel-title mt-1">Knowledge sync</h2></div><Database className="text-[#3d8d79]" size={20} /></div>
              <p className="mt-3 text-xs leading-5 text-[#7c898a]">Dữ liệu lưu trên Supabase, đồng bộ ngay khi admin duyệt — không cần reindex thủ công.</p>
            </section>
            <section className="paper-panel rounded-2xl p-5">
              <div className="flex items-start justify-between"><div><p className="panel-kicker">Developer view</p><h2 className="panel-title mt-1">Debug retrieval</h2></div><Search className="text-[#3d8d79]" size={19} /></div>
              <form onSubmit={debugSearch} className="mt-4 flex gap-2"><input value={debugQuery} onChange={(event) => setDebugQuery(event.target.value)} className="min-w-0 flex-1 rounded-lg border border-[#d8dfda] bg-white px-3 py-2 text-xs outline-none focus:border-[#3c8b79]" placeholder="nghỉ phép" /><button className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-[#e8f4ed] text-[#26725f]" aria-label="Tìm chunks"><FileSearch size={15} /></button></form>
              {debugging && <p className="mt-3 text-[11px] text-[#899697]">Đang tìm chunks...</p>}
              {debugRows.length > 0 && <div className="mt-3 space-y-2">{debugRows.slice(0, 3).map((row) => <div key={row.id} className="rounded-lg border border-[#e5eae4] bg-[#fcfbf7] p-2.5"><div className="flex items-center justify-between gap-2 text-[10px] font-bold text-[#26725f]"><span className="truncate">{row.metadata.source_file || row.metadata.source}</span><span>{Number(row.score).toFixed(2)}</span></div><p className="mt-1 truncate text-[10px] text-[#7d8a8a]">{row.metadata.heading}</p></div>)}</div>}
            </section>
          </aside>
        </div>
      </div>
    </div>
  )
}

function AuthScreen({ mode, setMode, form, setForm, onSubmit, loading, error }) {
  return (
    <div className="relative flex min-h-screen items-center justify-center bg-[#f7f5ef] px-4">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <div className="paper-panel relative w-full max-w-[380px] rounded-2xl p-8">
        <div className="flex justify-center"><div className="brand-mark">VA</div></div>
        <h1 className="display-subtitle mt-5 text-center !text-[26px]">{mode === 'login' ? 'Đăng nhập' : 'Tạo tài khoản'}</h1>
        <p className="mt-2 text-center text-xs text-[#7a8889]">Staff Assistant · Major Education</p>
        <form onSubmit={onSubmit} className="mt-7 space-y-3">
          {mode === 'signup' && (
            <input required value={form.display_name} onChange={(event) => setForm((current) => ({ ...current, display_name: event.target.value }))} placeholder="Họ tên" className="auth-input" />
          )}
          <input required type="email" value={form.email} onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))} placeholder="Email công ty" className="auth-input" />
          <input required type="password" minLength={6} value={form.password} onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))} placeholder="Mật khẩu (tối thiểu 6 ký tự)" className="auth-input" />
          {error && <p className="text-xs leading-5 text-[#c0503a]">{error}</p>}
          <button disabled={loading} className="auth-submit">{loading ? 'Đang xử lý...' : mode === 'login' ? 'Đăng nhập' : 'Đăng ký'}</button>
        </form>
        <button onClick={() => setMode(mode === 'login' ? 'signup' : 'login')} className="mt-5 w-full text-center text-xs text-[#3c8b79] hover:underline">
          {mode === 'login' ? 'Chưa có tài khoản? Đăng ký' : 'Đã có tài khoản? Đăng nhập'}
        </button>
      </div>
    </div>
  )
}

function Brand({ light = false }) { return <div className="flex items-center gap-3"><div className={`brand-mark ${light ? 'brand-light' : ''}`}>VA</div><div className={`font-display text-lg leading-none ${light ? 'text-white' : 'text-ink'}`}>Staff Assistant<span className={`mt-1 block font-sans text-[9px] font-bold uppercase tracking-[.16em] ${light ? 'text-[#9ec3c1]' : 'text-[#789096]'}`}>Major Education</span></div></div> }
function Field({ label, children }) { return <label className="mt-4 block"><span className="mb-1.5 block text-[10px] font-bold uppercase tracking-[.1em] text-[#859292]">{label}</span>{children}</label> }
function Select(props) { return <div className="relative"><select {...props} className="h-10 w-full appearance-none rounded-lg border border-[#d8dfda] bg-white px-3 pr-8 text-xs text-ink outline-none focus:border-[#3c8b79]" /><ChevronDown size={14} className="pointer-events-none absolute right-3 top-3 text-[#82908e]" /></div> }
function EmptyState({ onAsk }) { return <div className="flex h-full min-h-[450px] flex-col justify-center"><div className="mb-6 flex h-14 w-14 items-center justify-center rounded-2xl bg-[#e5f2e9] text-[#287661]"><Bot size={27} strokeWidth={1.6} /></div><h2 className="display-subtitle">What can I help you find?</h2><p className="mt-3 max-w-[430px] text-sm leading-6 text-[#7a8889]">Tra cứu nhanh các chính sách đã được phê duyệt trong Vault. Mỗi câu trả lời đều có nguồn để bạn kiểm chứng.</p><div className="mt-8 grid gap-2.5 sm:grid-cols-3">{suggestions.map((suggestion) => <button key={suggestion.label} onClick={() => onAsk(suggestion.text)} className="group rounded-xl border border-[#dfe6df] bg-[#fcfbf7] p-3 text-left transition hover:-translate-y-0.5 hover:border-[#87b9a4] hover:bg-[#f0f8f1]"><span className="text-xl text-[#e9795c]">{suggestion.icon}</span><span className="mt-2 block text-xs font-bold text-ink">{suggestion.label}</span><span className="mt-1 block text-[10px] leading-4 text-[#849091]">{suggestion.text}</span></button>)}</div></div> }
function renderInline(text, keyPrefix) {
  const parts = text.split(/\*\*(.+?)\*\*/g)
  return parts.map((part, index) =>
    index % 2 === 1 ? <strong key={`${keyPrefix}-b${index}`}>{part}</strong> : <span key={`${keyPrefix}-t${index}`}>{part}</span>
  )
}

function renderMessageText(text) {
  const blocks = text.split(/\n{2,}/)
  return blocks.map((block, blockIndex) => {
    const lines = block.split('\n').filter((line) => line.trim() !== '')
    const isBulletBlock = lines.length > 0 && lines.every((line) => /^\s*[*-]\s+/.test(line))
    if (isBulletBlock) {
      return (
        <ul key={`b${blockIndex}`} className="my-1.5 list-disc space-y-1 pl-4">
          {lines.map((line, lineIndex) => (
            <li key={`b${blockIndex}-l${lineIndex}`}>{renderInline(line.replace(/^\s*[*-]\s+/, ''), `b${blockIndex}-l${lineIndex}`)}</li>
          ))}
        </ul>
      )
    }
    return (
      <p key={`b${blockIndex}`} className={blockIndex > 0 ? 'mt-2' : ''}>
        {block.split('\n').map((line, lineIndex, arr) => (
          <span key={`b${blockIndex}-l${lineIndex}`}>
            {renderInline(line, `b${blockIndex}-l${lineIndex}`)}
            {lineIndex < arr.length - 1 && <br />}
          </span>
        ))}
      </p>
    )
  })
}

function Message({ message }) { return <div className={`flex gap-3 ${message.role === 'user' ? 'justify-end' : ''}`}>{message.role !== 'user' && <div className="avatar"><Bot size={16} /></div>}<div className={`max-w-[86%] rounded-2xl px-4 py-3 text-sm leading-6 ${message.role === 'user' ? 'user-bubble' : 'assistant-bubble'}`}>{message.role === 'user' ? message.text : renderMessageText(message.text)}{message.citations?.length > 0 && <div className="mt-3 border-t border-[#3d8d79]/20 pt-2 text-[10px] leading-5 text-[#29745f]"><div className="mb-1 flex items-center gap-1 font-bold uppercase tracking-[.08em]"><Link2 size={11} /> Sources</div>{message.citations.map((citation, index) => <div key={`${citation.source}-${index}`}>[{citation.source} · {citation.heading} · v{citation.version}]</div>)}</div>}</div>{message.role === 'user' && <div className="avatar user-avatar">You</div>}</div> }

export default App

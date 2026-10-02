import { useEffect, useRef, useState } from 'react'
import {
  ArrowUp,
  ArrowRightLeft,
  Bot,
  BriefcaseBusiness,
  CalendarDays,
  Check,
  Link2,
  LogOut,
  Menu,
  Plus,
  ShieldCheck,
  Users,
  X,
} from 'lucide-react'
import { createClient } from '@supabase/supabase-js'

// Logo chính thức theo Brand Guideline (header website truongvietanh.com), phục vụ từ /brand
// của backend. Quy định: rộng tối thiểu 120px, chỉ đặt trên nền trắng hoặc #F0F4F8.
const LOGO_SRC = '/brand/logo-vietanh.webp'

const suggestions = [
  { icon: CalendarDays, label: 'Nghỉ phép', text: 'Tôi cần xin nghỉ phép trước bao lâu?' },
  { icon: BriefcaseBusiness, label: 'Công tác phí', text: 'Đi công tác thì có được trả phí không?' },
  { icon: Users, label: 'Liên hệ Nhân sự', text: 'Liên hệ phòng Nhân sự ở đâu?' },
]

const TOKEN_KEY = 'va_token'
const REFRESH_KEY = 'va_refresh_token'

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
  const [supabaseAuth, setSupabaseAuth] = useState(null)
  const [googleConfigured, setGoogleConfigured] = useState(false)

  const [threads, setThreads] = useState([])
  const [activeThreadId, setActiveThreadId] = useState(null)

  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState([])
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const scrollAnchorRef = useRef(null)
  const tokenRef = useRef(token)
  const refreshingRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const params = new URLSearchParams(window.location.search)
      try {
        const response = await fetch('/api/auth/config')
        const config = response.ok ? await response.json() : { enabled: false }
        if (cancelled) return
        setGoogleConfigured(Boolean(config.enabled))
        if (config.enabled) {
          const client = createClient(config.supabase_url, config.supabase_anon_key, {
            auth: { flowType: 'pkce', detectSessionInUrl: false, autoRefreshToken: false },
          })
          setSupabaseAuth(client)
          const code = params.get('code')
          if (code) {
            const { data, error } = await client.auth.exchangeCodeForSession(code)
            window.history.replaceState({}, document.title, window.location.pathname)
            if (error) throw error
            persistSession(data.session.access_token, data.session.refresh_token)
          }
        }
        const callbackError = params.get('error_description') || params.get('error')
        if (callbackError) {
          window.history.replaceState({}, document.title, window.location.pathname)
          setAuthError(callbackError)
        }
      } catch (error) {
        if (!cancelled) setAuthError(error.message || 'Không thể khởi tạo đăng nhập Google.')
      } finally {
        if (!cancelled && !localStorage.getItem(TOKEN_KEY)) setAuthChecked(true)
      }
    })()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    loadHealth()
  }, [])

  useEffect(() => {
    scrollAnchorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, loading])

  useEffect(() => {
    if (!token) {
      if (!new URLSearchParams(window.location.search).has('code')) setAuthChecked(true)
      return
    }
    ;(async () => {
      try {
        const response = await authFetch('/api/auth/me')
        if (!response.ok) throw new Error()
        setUser(await response.json())
        loadThreads()
      } catch {
        clearSession()
      } finally {
        setAuthChecked(true)
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  function persistSession(accessToken, refreshToken) {
    localStorage.setItem(TOKEN_KEY, accessToken)
    localStorage.setItem(REFRESH_KEY, refreshToken)
    tokenRef.current = accessToken
    setToken(accessToken)
  }

  /** Tự làm mới phiên đăng nhập khi access token hết hạn (~1h), để nhân viên không
   * bị văng ra ngoài giữa chừng — chỉ đăng xuất khi refresh token cũng hết hạn/không hợp lệ. */
  async function refreshSession() {
    if (refreshingRef.current) return refreshingRef.current
    const storedRefreshToken = localStorage.getItem(REFRESH_KEY)
    if (!storedRefreshToken) return false
    refreshingRef.current = (async () => {
      try {
        const response = await fetch('/api/auth/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: storedRefreshToken }),
        })
        if (!response.ok) return false
        const data = await response.json()
        persistSession(data.access_token, data.refresh_token)
        return true
      } catch {
        return false
      }
    })()
    const ok = await refreshingRef.current
    refreshingRef.current = null
    return ok
  }

  /** fetch có Authorization header, tự refresh + retry 1 lần nếu access token đã hết hạn (401). */
  async function authFetch(url, options = {}) {
    const withAuth = (activeToken) => ({
      ...options,
      headers: { ...(options.headers || {}), ...(activeToken ? { Authorization: `Bearer ${activeToken}` } : {}) },
    })
    const response = await fetch(url, withAuth(tokenRef.current))
    if (response.status !== 401) return response
    const refreshed = await refreshSession()
    if (!refreshed) {
      clearSession()
      return response
    }
    return fetch(url, withAuth(tokenRef.current))
  }

  async function loadHealth() {
    try {
      const response = await fetch('/api/health')
      setHealth(await response.json())
    } catch {
      setHealth({ status: 'offline' })
    }
  }

  async function loadThreads() {
    try {
      const response = await authFetch('/api/chat/threads')
      if (response.ok) setThreads(await response.json())
    } catch {
      // best-effort
    }
  }

  async function openThread(id) {
    setActiveThreadId(id)
    setMobileNav(false)
    try {
      const response = await authFetch(`/api/chat/threads/${id}/messages`)
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
      await authFetch(`/api/chat/threads/${id}`, { method: 'DELETE' })
    } catch {
      // best-effort
    }
  }

  function clearSession() {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(REFRESH_KEY)
    tokenRef.current = null
    setToken(null)
    setUser(null)
    setThreads([])
    setActiveThreadId(null)
    setMessages([])
  }

  async function handleLogout() {
    try {
      await supabaseAuth?.auth.signOut({ scope: 'local' })
    } catch {
      // Always clear the app session, even if Supabase sign-out cannot reach the network.
    }
    clearSession()
  }

  async function handleSwitchGoogleAccount() {
    if (!supabaseAuth || !googleConfigured) {
      setAuthError('Đăng nhập Google chưa được cấu hình. Liên hệ quản trị viên.')
      await handleLogout()
      return
    }

    setAuthLoading(true)
    setAuthError('')
    try {
      await supabaseAuth.auth.signOut({ scope: 'local' })
    } catch {
      // Continue switching; the provider account chooser is independent of local session cleanup.
    }
    clearSession()

    const { error } = await supabaseAuth.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: window.location.origin,
        queryParams: { prompt: 'select_account' },
      },
    })
    if (error) {
      setAuthError(error.message)
      setAuthLoading(false)
    }
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
      persistSession(data.access_token, data.refresh_token)
    } catch (error) {
      setAuthError(error.message)
    } finally {
      setAuthLoading(false)
    }
  }

  async function handleGoogleSignIn() {
    if (!supabaseAuth) {
      setAuthError('Đăng nhập Google chưa được cấu hình. Liên hệ quản trị viên.')
      return
    }
    setAuthLoading(true)
    setAuthError('')
    const { error } = await supabaseAuth.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: window.location.origin,
        queryParams: { prompt: 'select_account' },
      },
    })
    if (error) {
      setAuthError(error.message)
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
      const response = await authFetch('/api/chat-staff', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: text,
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

  const statusOnline = health?.status === 'ok'
  const approvedCount = health?.approved_notes || 0

  if (!authChecked) {
    return <div className="grid min-h-screen place-items-center bg-surface text-base text-muted">Đang tải...</div>
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
        onGoogle={handleGoogleSignIn}
        googleConfigured={googleConfigured}
      />
    )
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-surface text-ink">
      <aside className={`${mobileNav ? 'mobile-open' : ''} sidebar fixed inset-y-0 left-0 z-30 flex w-[300px] -translate-x-full flex-col border-r border-line bg-white px-5 py-6 transition-transform lg:static lg:h-screen lg:translate-x-0`}>
        <div className="flex items-start justify-between gap-3">
          <Brand />
          <button className="icon-button lg:hidden" onClick={() => setMobileNav(false)} aria-label="Đóng menu"><X size={20} /></button>
        </div>
        <button onClick={startNewThread} className="btn-cta mt-6 h-12 w-full text-base">
          <Plus size={20} strokeWidth={2.5} /> Cuộc trò chuyện mới
        </button>
        <div className="mt-7 min-h-0 flex-1 overflow-y-auto">
          <div className="section-caption px-1">Lịch sử</div>
          <div className="mt-3 space-y-1">
            {threads.length === 0 && <p className="px-1 py-2 text-base leading-6 text-muted">Chưa có cuộc trò chuyện nào.</p>}
            {threads.map((thread) => {
              const active = thread.id === activeThreadId
              return (
                <div
                  key={thread.id}
                  onClick={() => openThread(thread.id)}
                  className={`thread-item ${active ? 'active bg-navy text-white' : 'text-ink hover:bg-surface'} flex cursor-pointer items-center justify-between gap-2 rounded-xl px-3 py-2.5 text-base transition`}
                >
                  <span className="truncate">{thread.title || 'Cuộc trò chuyện mới'}</span>
                  <button onClick={(event) => deleteThread(thread.id, event)} className={`thread-delete grid h-8 w-8 shrink-0 place-items-center rounded-lg ${active ? 'text-white hover:bg-white/15' : 'text-muted hover:bg-white hover:text-navy'}`} aria-label="Xoá cuộc trò chuyện"><X size={16} /></button>
                </div>
              )
            })}
          </div>
        </div>
        <div className="mt-4 flex items-center gap-3 border-t border-line pt-4">
          <div className="avatar user-avatar">{(user.display_name || user.email || '?').slice(0, 1).toUpperCase()}</div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-base font-bold text-navy">{user.display_name || 'Nhân viên'}</p>
            <p className="truncate text-base text-muted">{user.email}</p>
          </div>
        </div>
        <div className="mt-3 space-y-2">
          <button onClick={handleSwitchGoogleAccount} className="btn-outline h-11 w-full whitespace-nowrap px-3 text-base"><ArrowRightLeft size={18} /> Đổi tài khoản Google</button>
          <button onClick={handleLogout} className="btn-outline h-11 w-full whitespace-nowrap px-3 text-base"><LogOut size={18} /> Đăng xuất</button>
        </div>
      </aside>
      {mobileNav && <button className="fixed inset-0 z-20 bg-navy/40 lg:hidden" onClick={() => setMobileNav(false)} aria-label="Đóng menu" />}

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex items-center justify-between border-b border-line bg-white px-4 py-3 lg:hidden">
          <img src={LOGO_SRC} alt="Trường Việt Anh" className="h-auto w-[150px]" />
          <button className="icon-button" onClick={() => setMobileNav(true)} aria-label="Mở menu"><Menu size={22} /></button>
        </header>

        <div className="flex min-h-0 flex-1 gap-5 p-3 sm:p-6">
          <section className="card flex min-w-0 flex-1 flex-col overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4 sm:px-7">
              <div>
                <h1 className="text-xl font-extrabold text-navy">Trò chuyện nội bộ</h1>
                <p className="mt-0.5 text-base text-muted">Tra cứu quy định đã được duyệt · Trường Việt Anh</p>
              </div>
              <div className="flex items-center gap-2 rounded-full border border-line bg-surface px-4 py-1.5 text-base text-ink">
                <span className={`status-dot ${statusOnline ? 'online' : ''}`} />
                {statusOnline ? `${approvedCount} tài liệu đã duyệt` : 'Mất kết nối'}
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-7 sm:px-8">
              <div className="mx-auto max-w-[820px]">
                {messages.length === 0 ? <EmptyState onAsk={ask} /> : (
                  <div className="space-y-7">
                    {messages.map((message, index) => <Message key={`${message.role}-${index}`} message={message} />)}
                    {loading && (
                      <div className="flex gap-3">
                        <div className="avatar"><Bot size={20} /></div>
                        <div className="assistant-bubble px-4 py-3 text-base text-muted"><span className="typing"><i /><i /><i /></span>Đang tra cứu tài liệu nội bộ...</div>
                      </div>
                    )}
                    <div ref={scrollAnchorRef} />
                  </div>
                )}
              </div>
            </div>
            <form className="border-t border-line bg-white p-4 sm:p-5" onSubmit={(event) => { event.preventDefault(); ask() }}>
              <div className="mx-auto max-w-[820px]">
                <div className="relative flex items-center">
                  <input value={question} onChange={(event) => setQuestion(event.target.value)} className="field h-14 pr-16" placeholder="Hỏi về quy định nội bộ..." aria-label="Câu hỏi" />
                  <button disabled={loading || !question.trim()} className="btn-cta absolute right-2 h-11 w-11 px-0" aria-label="Gửi câu hỏi"><ArrowUp size={22} strokeWidth={2.5} /></button>
                </div>
                <div className="mt-3 flex flex-wrap items-center justify-between gap-2 px-1 text-base text-muted">
                  <span>Câu trả lời chỉ dựa trên tài liệu nội bộ đã được duyệt.</span>
                  <a href="/admin" className="font-semibold text-navy underline-offset-4 hover:underline">Quản lý dữ liệu</a>
                </div>
              </div>
            </form>
          </section>

          <aside className="hidden w-[320px] shrink-0 flex-col gap-4 overflow-y-auto xl:flex">
            <section className="card p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="section-caption">Kho tri thức</p>
                  <h2 className="mt-1 text-lg font-extrabold text-ink">Tài liệu đã duyệt</h2>
                </div>
                <ShieldCheck className="shrink-0 text-navy" size={24} />
              </div>
              <p className="mt-3 text-base leading-7 text-muted">Trợ lý chỉ trả lời từ nội dung đã được duyệt, áp dụng cho cán bộ, giáo viên, nhân viên.</p>
              <div className="mt-4 flex items-center gap-2 border-t border-line pt-4 text-base text-ink">
                {statusOnline ? <Check size={18} className="shrink-0 text-success" /> : <span className="status-dot" />}
                {statusOnline ? `${approvedCount} tài liệu đang sẵn sàng` : 'Chưa kết nối được kho tri thức'}
              </div>
              {user.employee && (
                <div className="mt-4 border-t border-line pt-4 text-base">
                  <p className="font-bold text-navy">{user.employee.department}</p>
                  <p className="mt-1 text-muted">{user.employee.job_title}</p>
                </div>
              )}
            </section>
          </aside>
        </div>
      </div>
    </div>
  )
}

function AuthScreen({ mode, setMode, form, setForm, onSubmit, loading, error, onGoogle, googleConfigured }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-surface px-4 py-10">
      <div className="card w-full max-w-[440px] p-7 sm:p-9">
        <div className="flex justify-center"><img src={LOGO_SRC} alt="Trường Việt Anh" className="h-auto w-[240px]" /></div>
        <h1 className="mt-6 text-center text-3xl font-extrabold text-navy">{mode === 'login' ? 'Đăng nhập' : 'Tạo tài khoản'}</h1>
        <p className="mt-2 text-center text-base leading-6 text-muted">Trợ lý nội bộ dành cho cán bộ, giáo viên, nhân viên Trường Việt Anh</p>
        <button type="button" onClick={onGoogle} disabled={loading || !googleConfigured} className="btn-outline mt-7 h-12 w-full text-base">
          <GoogleMark />
          {googleConfigured ? 'Đăng nhập với Google' : 'Google chưa được cấu hình'}
        </button>
        <div className="my-5 flex items-center gap-3 text-base text-muted"><span className="h-px flex-1 bg-line" />hoặc dùng email<span className="h-px flex-1 bg-line" /></div>
        <form onSubmit={onSubmit} className="space-y-3">
          {mode === 'signup' && (
            <input required value={form.display_name} onChange={(event) => setForm((current) => ({ ...current, display_name: event.target.value }))} placeholder="Họ tên" aria-label="Họ tên" className="field" />
          )}
          <input required type="email" value={form.email} onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))} placeholder="Email công ty (@truongvietanh.com)" aria-label="Email" className="field" />
          <input required type="password" minLength={6} value={form.password} onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))} placeholder="Mật khẩu (tối thiểu 6 ký tự)" aria-label="Mật khẩu" className="field" />
          {error && <p className="text-base leading-6 text-danger" role="alert">{error}</p>}
          <button disabled={loading} className="btn-cta h-12 w-full text-base">{loading ? 'Đang xử lý...' : mode === 'login' ? 'Đăng nhập' : 'Đăng ký'}</button>
        </form>
        <button onClick={() => setMode(mode === 'login' ? 'signup' : 'login')} className="mt-5 w-full text-center text-base font-semibold text-navy underline-offset-4 hover:underline">
          {mode === 'login' ? 'Chưa có tài khoản? Đăng ký' : 'Đã có tài khoản? Đăng nhập'}
        </button>
      </div>
    </div>
  )
}

function GoogleMark() {
  return (
    <svg width="20" height="20" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  )
}

function Brand() {
  return (
    <div className="min-w-0">
      <img src={LOGO_SRC} alt="Trường Việt Anh" className="h-auto w-[200px]" />
      <p className="mt-2 text-base font-bold text-navy">Trợ lý nội bộ</p>
    </div>
  )
}

function EmptyState({ onAsk }) {
  return (
    <div className="flex h-full min-h-[420px] flex-col justify-center">
      <div className="mb-6 grid h-14 w-14 place-items-center rounded-2xl bg-navy text-brand-yellow"><Bot size={28} strokeWidth={1.8} /></div>
      <h2 className="text-3xl font-extrabold leading-tight text-navy sm:text-4xl">Bạn cần tra cứu gì?</h2>
      <p className="mt-3 max-w-[520px] text-lg leading-8 text-muted">Hỏi nhanh về quy định, chính sách đã được duyệt. Mỗi câu trả lời đều kèm nguồn để bạn kiểm chứng.</p>
      <div className="mt-8 grid gap-3 sm:grid-cols-3">
        {suggestions.map((suggestion) => {
          const Icon = suggestion.icon
          return (
            <button key={suggestion.label} onClick={() => onAsk(suggestion.text)} className="group rounded-2xl border border-line bg-white p-4 text-left transition hover:-translate-y-0.5 hover:border-navy/40 hover:shadow-card focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-navy/20">
              <Icon size={24} className="text-navy" />
              <span className="mt-3 block text-base font-bold text-navy">{suggestion.label}</span>
              <span className="mt-1 block text-base leading-6 text-muted">{suggestion.text}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function renderInline(text, keyPrefix) {
  const parts = text.split(/\*\*(.+?)\*\*/g)
  return parts.map((part, index) =>
    index % 2 === 1 ? <strong key={`${keyPrefix}-b${index}`} className="font-bold text-navy">{part}</strong> : <span key={`${keyPrefix}-t${index}`}>{part}</span>
  )
}

function renderMessageText(text) {
  const blocks = text.split(/\n{2,}/)
  return blocks.map((block, blockIndex) => {
    const lines = block.split('\n').filter((line) => line.trim() !== '')
    const isBulletBlock = lines.length > 0 && lines.every((line) => /^\s*[*-]\s+/.test(line))
    if (isBulletBlock) {
      return (
        <ul key={`b${blockIndex}`} className={`${blockIndex > 0 ? 'mt-2' : ''} list-disc space-y-1.5 pl-5 marker:text-navy`}>
          {lines.map((line, lineIndex) => (
            <li key={`b${blockIndex}-l${lineIndex}`} className={/^\s{2,}/.test(line) ? 'ml-5' : ''}>{renderInline(line.replace(/^\s*[*-]\s+/, ''), `b${blockIndex}-l${lineIndex}`)}</li>
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

function Message({ message }) {
  const isUser = message.role === 'user'
  return (
    <div className={`flex gap-3 ${isUser ? 'justify-end' : ''}`}>
      {!isUser && <div className="avatar"><Bot size={20} /></div>}
      <div className={`max-w-[86%] px-4 py-3 text-base leading-7 ${isUser ? 'user-bubble' : 'assistant-bubble'}`}>
        {isUser ? message.text : renderMessageText(message.text)}
        {message.citations?.length > 0 && (
          <div className="mt-3 border-t border-line pt-3">
            <div className="flex items-center gap-1.5 text-base font-bold text-navy"><Link2 size={16} /> Nguồn</div>
            <ul className="mt-1 space-y-1">
              {message.citations.map((citation, index) => (
                <li key={`${citation.source}-${index}`} className="text-base leading-6 text-muted">
                  <span className="font-semibold text-ink">{citation.source}</span> · {citation.heading}{citation.version && citation.version !== 'unknown' ? ` · v${citation.version}` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}

export default App

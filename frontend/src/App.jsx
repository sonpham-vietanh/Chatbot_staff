import { useEffect, useRef, useState } from 'react'
import { ArrowUp, Menu, Plus, X } from 'lucide-react'
import { createClient } from '@supabase/supabase-js'

// Logo chính thức (header website truongvietanh.com), phục vụ từ /brand của backend.
// Chỉ đặt trên nền trắng/kem; trên nền navy dùng wordmark chữ "TRƯỜNG VIỆT ANH".
const LOGO_SRC = '/brand/logo-vietanh.webp'

const suggestions = [
  { label: 'Nghỉ phép', text: 'Tôi cần xin nghỉ phép trước bao lâu?' },
  { label: 'Công tác phí', text: 'Đi công tác thì có được trả phí không?' },
  { label: 'Liên hệ', text: 'Liên hệ phòng Nhân sự ở đâu?' },
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
  const statusPending = health === null
  const approvedCount = health?.approved_notes || 0
  const displayName = user?.display_name || user?.employee?.display_name || ''
  const firstName = displayName.trim().split(/\s+/).pop() || 'bạn'
  const activeThread = threads.find((thread) => thread.id === activeThreadId)

  if (!authChecked) {
    return <div className="grid h-full place-items-center bg-cream text-base text-muted">Đang tải...</div>
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
    <div className="relative flex h-full w-full overflow-hidden">
      <aside className={`dark-surface z-30 flex w-[288px] shrink-0 flex-col px-5 pb-5 pt-7 max-[899px]:absolute max-[899px]:inset-y-0 max-[899px]:left-0 ${mobileNav ? '' : 'max-[899px]:hidden'}`}>
        <div className="flex items-start justify-between gap-3 px-2">
          <div className="flex flex-col gap-1.5">
            <span className="wordmark">TRƯỜNG VIỆT ANH</span>
            <span className="font-semibold text-gold">Trợ lý nội bộ</span>
          </div>
          <button onClick={() => setMobileNav(false)} className="grid h-10 w-10 place-items-center border border-white/20 text-white min-[900px]:hidden" aria-label="Đóng menu"><X size={18} /></button>
        </div>

        <button onClick={startNewThread} className="btn btn-new mt-7 w-full">
          <Plus size={18} strokeWidth={2.4} /> Cuộc trò chuyện mới
        </button>

        <div className="mt-8 px-2 font-bold tracking-[.08em] text-gold">LỊCH SỬ</div>
        <div className="mt-2.5 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
          {threads.length === 0 && <p className="p-2 text-[#A9AAC8]">Chưa có cuộc trò chuyện nào.</p>}
          {threads.map((thread) => (
            <div key={thread.id} onClick={() => openThread(thread.id)} className={`thread-row ${thread.id === activeThreadId ? 'active' : ''}`}>
              <span className="dot" />
              <span className="title">{thread.title || 'Cuộc trò chuyện mới'}</span>
              <button onClick={(event) => deleteThread(thread.id, event)} className="del" aria-label="Xoá cuộc trò chuyện"><X size={14} strokeWidth={2.2} /></button>
            </div>
          ))}
        </div>

        <div className="mt-4 flex items-center gap-3 border-t border-white/[.12] px-2 pt-4">
          <div className="grid h-10 w-10 shrink-0 place-items-center bg-[linear-gradient(180deg,#F9DD0E_0%,#E0B90C_100%)] text-[17px] font-extrabold text-navy">
            {(displayName || user.email || '?').trim().split(/\s+/).pop().slice(0, 1).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate font-bold">{displayName || 'Nhân viên'}</div>
            <div className="truncate text-[#A9AAC8]">{user.employee?.department || user.email}</div>
          </div>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <button onClick={handleSwitchGoogleAccount} className="btn-dark">Đổi tài khoản</button>
          <button onClick={handleLogout} className="btn-dark">Đăng xuất</button>
        </div>
      </aside>
      {mobileNav && <div className="absolute inset-0 z-20 bg-[rgba(13,14,43,.45)] min-[900px]:hidden" onClick={() => setMobileNav(false)} />}

      <div className="flex min-w-0 flex-1 flex-col bg-cream">
        <header className="flex items-center justify-between gap-4 border-b border-line bg-white px-4 py-4 sm:px-7">
          <div className="flex min-w-0 items-center gap-3.5">
            <button onClick={() => setMobileNav(true)} className="grid h-11 w-11 shrink-0 place-items-center border border-line bg-white text-navy min-[900px]:hidden" aria-label="Mở menu"><Menu size={20} /></button>
            <div className="min-w-0">
              <h1 className="truncate text-lg font-extrabold text-navy">{activeThread?.title || 'Cuộc trò chuyện mới'}</h1>
              <p className="truncate text-muted">Chỉ trả lời từ tài liệu đã được duyệt</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-5">
            <div className="flex items-center gap-2 text-ink">
              <span className={`status-dot ${statusOnline ? 'online' : statusPending ? 'pending' : ''}`} />
              <span className="hidden sm:inline">{statusOnline ? `${approvedCount} tài liệu đã duyệt` : statusPending ? 'Đang kết nối...' : 'Mất kết nối'}</span>
              <span className="sm:hidden">{statusOnline ? `${approvedCount} tài liệu` : statusPending ? '...' : 'Mất kết nối'}</span>
            </div>
            <a href="/admin" className="hidden font-bold sm:inline">Quản lý dữ liệu</a>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-10 sm:px-6">
          <div className="mx-auto max-w-[760px]">
            {messages.length === 0 && !loading ? <EmptyState firstName={firstName} onAsk={ask} /> : (
              <div className="flex flex-col gap-8">
                {messages.map((message, index) => <Message key={`${message.role}-${index}`} message={message} />)}
                {loading && (
                  <div className="flex items-center gap-4">
                    <div className="va-mark">VA</div>
                    <div className="flex items-center gap-3 text-muted"><span className="typing"><i /><i /><i /></span>Đang tra cứu tài liệu nội bộ…</div>
                  </div>
                )}
                <div ref={scrollAnchorRef} />
              </div>
            )}
          </div>
        </div>

        <form className="bg-cream px-4 pb-5 pt-4 sm:px-6" onSubmit={(event) => { event.preventDefault(); ask() }}>
          <div className="mx-auto max-w-[760px]">
            <div className="flex items-center gap-2 rounded-lg border border-line bg-white py-1.5 pl-5 pr-1.5 shadow-[0_4px_16px_rgba(20,21,58,.05)]">
              <input value={question} onChange={(event) => setQuestion(event.target.value)} className="h-12 min-w-0 flex-1 border-0 bg-transparent text-[17px] text-ink outline-none" placeholder="Hỏi về quy định nội bộ…" aria-label="Câu hỏi" />
              <button disabled={loading || !question.trim()} className="send-btn" aria-label="Gửi câu hỏi"><ArrowUp size={20} strokeWidth={2.6} /></button>
            </div>
            <p className="mt-2.5 text-center text-muted">Câu trả lời chỉ dựa trên tài liệu nội bộ đã được duyệt.</p>
          </div>
        </form>
      </div>
    </div>
  )
}

function AuthScreen({ mode, setMode, form, setForm, onSubmit, loading, error, onGoogle, googleConfigured }) {
  const update = (key) => (event) => setForm((current) => ({ ...current, [key]: event.target.value }))
  return (
    <div className="grid h-full overflow-auto min-[900px]:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <aside className="dark-surface diagonal hidden flex-col justify-between px-16 py-14 min-[900px]:flex">
        <div className="wordmark text-lg">TRƯỜNG VIỆT ANH</div>
        <div className="max-w-[460px]">
          <div className="rule mb-6" />
          <h1 className="text-[48px] font-extrabold leading-[1.1] tracking-[-.01em] [text-wrap:balance]">Trợ lý nội bộ cho cán bộ, giáo viên, nhân viên</h1>
          <p className="mt-5 text-lg leading-[1.65] text-navy-100">Tra cứu quy chế, chính sách và quy trình đã được duyệt. Mỗi câu trả lời đều kèm nguồn để bạn kiểm chứng.</p>
          <div className="mt-10 flex flex-col border-t border-white/[.12]">
            {[['Nhân sự', 'Nghỉ phép · bảo hiểm'], ['Tài chính', 'Công tác phí · thanh toán'], ['Chuyên môn', 'SOP giảng dạy']].map(([label, text]) => (
              <div key={label} className="flex justify-between gap-4 border-b border-white/[.12] py-3.5"><span className="font-bold">{label}</span><span className="text-[#C9CAE0]">{text}</span></div>
            ))}
          </div>
        </div>
        <div className="font-semibold text-[#FDE68A]">Vui vẻ và Thực dụng</div>
      </aside>

      <main className="flex items-center justify-center bg-cream px-6 py-12">
        <div className="flex w-full max-w-[420px] flex-col">
          <img src={LOGO_SRC} alt="Trường Việt Anh" className="h-auto w-[180px] self-start" />
          <h2 className="mt-10 text-[32px] font-extrabold leading-tight text-navy">{mode === 'login' ? 'Đăng nhập' : 'Tạo tài khoản'}</h2>
          <p className="mt-2 leading-relaxed text-muted">Dùng tài khoản @truongvietanh.com</p>

          <button type="button" onClick={onGoogle} disabled={loading || !googleConfigured} className="btn btn-google mt-8 w-full">
            <GoogleMark />
            {googleConfigured ? 'Đăng nhập với Google' : 'Google chưa được cấu hình'}
          </button>

          <div className="my-7 flex items-center gap-3 text-muted"><span className="h-px flex-1 bg-line" />hoặc dùng email<span className="h-px flex-1 bg-line" /></div>

          <form onSubmit={onSubmit} className="flex flex-col gap-4">
            {mode === 'signup' && (
              <label className="field">Họ tên<input required value={form.display_name} onChange={update('display_name')} placeholder="Nguyễn Văn A" /></label>
            )}
            <label className="field">Email<input required type="email" value={form.email} onChange={update('email')} placeholder="ten@truongvietanh.com" /></label>
            <label className="field">Mật khẩu<input required type="password" minLength={6} value={form.password} onChange={update('password')} placeholder="Tối thiểu 6 ký tự" /></label>
            {error && <p className="text-danger" role="alert">{error}</p>}
            <button disabled={loading} className="btn btn-cta mt-2 w-full">{loading ? 'Đang xử lý...' : `${mode === 'login' ? 'Đăng nhập' : 'Đăng ký'} →`}</button>
          </form>

          <button onClick={() => setMode(mode === 'login' ? 'signup' : 'login')} className="btn-link mt-6 self-center">
            {mode === 'login' ? 'Chưa có tài khoản? Đăng ký' : 'Đã có tài khoản? Đăng nhập'}
          </button>
        </div>
      </main>
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

function EmptyState({ firstName, onAsk }) {
  return (
    <div className="pt-[6vh]">
      <div className="rule" />
      <h1 className="mt-5 text-[34px] font-extrabold leading-[1.12] tracking-[-.01em] text-navy sm:text-[44px]">Chào {firstName}, bạn cần tra cứu gì?</h1>
      <p className="mt-3.5 max-w-[540px] text-lg leading-[1.65] text-muted">Hỏi về quy định, chính sách đã được duyệt. Mỗi câu trả lời đều kèm nguồn để bạn kiểm chứng.</p>
      <div className="mt-9 flex flex-col overflow-hidden rounded-lg border border-line bg-white">
        {suggestions.map((suggestion) => (
          <button key={suggestion.label} onClick={() => onAsk(suggestion.text)} className="suggestion">
            <span className="label font-bold text-gold-text">{suggestion.label}</span>
            <span className="text-[17px] font-semibold text-navy">{suggestion.text}</span>
            <span className="text-lg text-navy" aria-hidden="true">→</span>
          </button>
        ))}
      </div>
    </div>
  )
}

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
        <ul key={`b${blockIndex}`} className="mb-2.5 flex list-disc flex-col gap-1.5 pl-[22px]">
          {lines.map((line, lineIndex) => (
            <li key={`b${blockIndex}-l${lineIndex}`} className={`pl-1 ${/^\s{2,}/.test(line) ? 'ml-5' : ''}`}>{renderInline(line.replace(/^\s*[*-]\s+/, ''), `b${blockIndex}-l${lineIndex}`)}</li>
          ))}
        </ul>
      )
    }
    return (
      <p key={`b${blockIndex}`} className="mb-2.5 [text-wrap:pretty]">
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
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-[10px_10px_3px_10px] bg-navy px-[18px] py-3.5 text-[17px] leading-[1.6] text-white">{message.text}</div>
      </div>
    )
  }
  return (
    <div className="flex gap-4">
      <div className="va-mark">VA</div>
      <div className="bot-text min-w-0 flex-1 pt-1 text-[17px] leading-[1.7] text-ink">
        {renderMessageText(message.text)}
        {message.citations?.length > 0 && (
          <div className="mt-4 flex flex-col gap-2">
            <span className="font-bold text-gold-text">Nguồn</span>
            <div className="flex flex-wrap gap-2">
              {message.citations.map((citation, index) => (
                <span key={`${citation.source}-${index}`} className="source-chip">
                  <strong>{citation.source}</strong>
                  {citation.heading}{citation.version && citation.version !== 'unknown' ? ` · v${citation.version}` : ''}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default App

import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { ArrowUp, Flag, Menu, Plus, X } from 'lucide-react'
import { createClient } from '@supabase/supabase-js'
import { MarkdownView } from './markdown.jsx'
import { errorMessage, networkMessage } from './api.js'
// Hai trang này chỉ tải khi người dùng vào đó, để màn hình chat/đăng nhập tải nhanh hơn
const Manage = lazy(() => import('./Manage.jsx'))
const Profile = lazy(() => import('./Profile.jsx'))
const PageLoading = <div className="grid h-full place-items-center bg-cream text-base text-muted">Đang tải...</div>

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
const DRAFT_KEY = 'va_draft' // câu đang gõ khi phiên hết hạn, khôi phục sau khi đăng nhập lại
const MAX_QUESTION = 2000 // backend giới hạn 2000 ký tự
const ACTIVE_THREAD_KEY = 'va_active_thread' // thread đang xem, lưu theo user để F5 không mất

function readActiveThread(uid) {
  try { return localStorage.getItem(`${ACTIVE_THREAD_KEY}:${uid}`) } catch { return null }
}
function writeActiveThread(uid, id) {
  try {
    if (id) localStorage.setItem(`${ACTIVE_THREAD_KEY}:${uid}`, id)
    else localStorage.removeItem(`${ACTIVE_THREAD_KEY}:${uid}`)
  } catch { /* trình duyệt chặn storage: bỏ qua */ }
}

function saveDraft(text) {
  try { sessionStorage.setItem(DRAFT_KEY, text) } catch { /* trình duyệt chặn storage: bỏ qua */ }
}
function takeDraft() {
  try {
    const text = sessionStorage.getItem(DRAFT_KEY)
    if (text) sessionStorage.removeItem(DRAFT_KEY)
    return text || ''
  } catch {
    return ''
  }
}

/** Đăng nhập 1 lần từ Major OS: backend /sso/os trả phiên qua URL fragment (#sso_access=...).
 * Phải đọc ĐỒNG BỘ ngay lúc khởi tạo state (trước lần render đầu) để màn đăng nhập không nháy lên;
 * chạy lại lần 2 (React StrictMode) thì fragment đã bị xoá nhưng phiên đã nằm trong localStorage. */
function consumeSsoFragment() {
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const access = fragment.get('sso_access')
  const refresh = fragment.get('sso_refresh')
  if (!access || !refresh) return
  localStorage.setItem(TOKEN_KEY, access)
  localStorage.setItem(REFRESH_KEY, refresh)
  window.history.replaceState({}, document.title, window.location.pathname + window.location.search)
}

async function readApiResponse(response) {
  const raw = await response.text()
  try {
    return raw ? JSON.parse(raw) : {}
  } catch {
    // Trang lỗi HTML của proxy/Cloudflare (502, 504, 524...) không được đổ nguyên văn vào khung chat
    if (/^\s*<(!doctype|html)/i.test(raw)) {
      return { detail: `Máy chủ tạm thời không phản hồi (HTTP ${response.status}). Thử lại sau ít phút.` }
    }
    return { detail: raw || `Máy chủ trả về lỗi (HTTP ${response.status}).` }
  }
}

function App() {
  const [token, setToken] = useState(() => { consumeSsoFragment(); return localStorage.getItem(TOKEN_KEY) })
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
  const [meError, setMeError] = useState('') // lỗi tạm thời khi kiểm tra phiên (503/mất mạng) — KHÔNG xoá token
  const [meRetry, setMeRetry] = useState(0)
  const [viewKey, setViewKey] = useState(0) // đổi mỗi lần mở thread/tạo mới, để bỏ qua phản hồi của thread cũ
  const [pendingView, setPendingView] = useState(null)
  const [threadLoading, setThreadLoading] = useState(false)
  const [threadError, setThreadError] = useState('')
  const [notice, setNotice] = useState('')
  const [manageInfo, setManageInfo] = useState(null) // quyền quản lý (từ /api/manage/me), tải nền sau khi vào app
  const restoredRef = useRef(false) // đã thử khôi phục thread đang xem sau khi tải trang chưa
  const viewRef = useRef(0)
  const noticeTimer = useRef(null)
  const inputRef = useRef(null)
  const scrollAnchorRef = useRef(null)
  const tokenRef = useRef(token)
  const refreshingRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const params = new URLSearchParams(window.location.search)
      const ssoError = params.get('sso_error')
      if (ssoError) {
        window.history.replaceState({}, document.title, window.location.pathname)
        setAuthError(`Không đăng nhập được từ Major OS: ${ssoError}`)
      }
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
      setMeError('')
      try {
        const response = await authFetch('/api/auth/me')
        if (response.status === 401) {
          clearSession() // token thật sự hết hạn/không hợp lệ
        } else if (!response.ok) {
          setMeError('Không kết nối được, thử lại')
        } else {
          const me = await response.json()
          setUser(me)
          loadThreads(me.id)
          const draft = takeDraft()
          if (draft) setQuestion(draft)
        }
      } catch {
        setMeError('Không kết nối được, thử lại') // mất mạng: giữ token để thử lại
      } finally {
        setAuthChecked(true)
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, meRetry])

  // Nhớ thread đang xem (chỉ sau khi đã thử khôi phục, để lần render đầu không ghi đè bằng null)
  useEffect(() => {
    if (user?.id && restoredRef.current) writeActiveThread(user.id, activeThreadId)
  }, [activeThreadId, user?.id])

  // Quyền quản lý tải nền, không chặn lần mở đầu; lỗi thì chỉ ẩn link
  useEffect(() => {
    if (!user?.id) { setManageInfo(null); return undefined }
    let cancelled = false
    authFetch('/api/manage/me')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => { if (!cancelled) setManageInfo(data) })
      .catch(() => {})
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id])

  // Ô nhập tự giãn theo nội dung (tối đa ~6 dòng, giới hạn bằng max-height trong CSS)
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [question, user])

  function showNotice(text) {
    clearTimeout(noticeTimer.current)
    setNotice(text)
    noticeTimer.current = setTimeout(() => setNotice(''), 6000)
  }

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

  async function loadThreads(uid) {
    try {
      const response = await authFetch('/api/chat/threads')
      if (response.ok) {
        const rows = await response.json()
        setThreads(rows)
        // Lần tải đầu sau F5: mở lại thread đang xem; thread đã bị xoá thì về cuộc mới, không báo lỗi
        if (uid && !restoredRef.current) {
          restoredRef.current = true
          const saved = readActiveThread(uid)
          if (saved && rows.some((thread) => thread.id === saved)) openThread(saved)
          else if (saved) writeActiveThread(uid, null)
        }
      }
    } catch {
      // best-effort
    }
  }

  function nextView() {
    viewRef.current += 1
    setViewKey(viewRef.current)
    return viewRef.current
  }

  async function openThread(id) {
    const view = nextView()
    setActiveThreadId(id)
    setMobileNav(false)
    setMessages([])
    setThreadError('')
    setThreadLoading(true)
    try {
      const response = await authFetch(`/api/chat/threads/${id}/messages`)
      if (!response.ok) throw new Error(errorMessage(await readApiResponse(response), response.status, 'Không tải được cuộc trò chuyện này.'))
      const rows = await response.json()
      if (viewRef.current !== view) return // người dùng đã chuyển thread khác
      setMessages(rows.map((row) => ({ role: row.role, text: row.content, citations: row.citations || [] })))
    } catch (error) {
      if (viewRef.current === view) setThreadError(networkMessage(error))
    } finally {
      if (viewRef.current === view) setThreadLoading(false)
    }
  }

  function startNewThread() {
    nextView()
    setActiveThreadId(null)
    setMessages([])
    setThreadError('')
    setThreadLoading(false)
    setMobileNav(false)
  }

  async function deleteThread(id, event) {
    event.stopPropagation()
    const title = threads.find((thread) => thread.id === id)?.title || 'Cuộc trò chuyện mới'
    if (!window.confirm(`Xoá cuộc trò chuyện "${title}"? Không thể khôi phục.`)) return
    try {
      const response = await authFetch(`/api/chat/threads/${id}`, { method: 'DELETE' })
      if (!response.ok) throw new Error(errorMessage(await readApiResponse(response), response.status, 'Không xoá được cuộc trò chuyện.'))
    } catch (error) {
      showNotice(`Chưa xoá được cuộc trò chuyện. ${networkMessage(error)}`) // chưa gỡ khỏi danh sách nên không cần hoàn tác
      return
    }
    setThreads((current) => current.filter((thread) => thread.id !== id))
    if (activeThreadId === id) startNewThread()
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
    restoredRef.current = false
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
      if (!response.ok) throw new Error(errorMessage(data, response.status, 'Không thể xác thực'))
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

  /** Nút "Báo sai": gửi nguyên văn câu hỏi + câu trả lời + nguồn để admin kiểm tra. */
  async function reportAnswer(index, { reason, note }) {
    const answer = messages[index]
    const previousQuestion = messages.slice(0, index).reverse().find((message) => message.role === 'user')
    const response = await authFetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: previousQuestion?.text || '(không rõ câu hỏi)',
        answer: answer.text,
        citations: answer.citations || [],
        reason,
        note: note || null,
        thread_id: activeThreadId,
      }),
    })
    if (!response.ok) {
      const data = await readApiResponse(response)
      throw new Error(errorMessage(data, response.status, 'Chưa gửi được báo cáo, vui lòng thử lại.'))
    }
    setMessages((current) => current.map((message, i) => (i === index ? { ...message, reported: true } : message)))
  }

  async function ask(value = question, { retry = false } = {}) {
    const text = String(value).trim()
    if (!text || loading || threadLoading) return
    if (!retry) setQuestion('')
    // Bỏ câu báo lỗi của trợ lý khỏi history gửi LLM
    let base = messages.filter((message) => !message.error)
    // Gửi lại: câu hỏi đã nằm sẵn trong khung chat, không đưa vào history và không thêm lần nữa
    if (retry && base.at(-1)?.role === 'user') base = base.slice(0, -1)
    const history = base
      .slice(-6)
      .map((message) => ({ role: message.role, content: message.text }))
    const viewAtSend = viewRef.current
    const stillHere = () => viewRef.current === viewAtSend // vẫn đang xem đúng thread lúc gửi?
    setMessages((current) => (retry
      ? current.filter((message) => !message.error) // bỏ tin lỗi cũ, giữ nguyên câu hỏi của người dùng
      : [...current, { role: 'user', text }]))
    setLoading(true)
    setPendingView(viewAtSend)
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
        saveDraft(text) // giữ lại câu đang hỏi để khôi phục sau khi đăng nhập lại
        setAuthError('Phiên đã hết hạn, vui lòng đăng nhập lại')
        clearSession()
        return
      }
      const data = await readApiResponse(response)
      if (!response.ok) {
        throw new Error(errorMessage(data, response.status, `Máy chủ trả về lỗi (HTTP ${response.status}).`))
      }
      if (data.thread_id) loadThreads()
      // Người dùng đã sang thread khác trong lúc chờ: không chèn câu trả lời / không đổi thread
      if (!stillHere()) return
      const followUps = Array.isArray(data.suggestions) ? data.suggestions.filter((item) => typeof item === 'string' && item.trim()).slice(0, 3) : []
      setMessages((current) => [...current, { role: 'assistant', text: data.answer, citations: data.citations || [], suggestions: followUps }])
      if (data.thread_id) setActiveThreadId(data.thread_id)
    } catch (error) {
      if (!stillHere()) return
      setMessages((current) => [...current, { role: 'assistant', error: true, text: `Không thể xử lý câu hỏi. ${networkMessage(error)}`, retryText: text }])
    } finally {
      setLoading(false)
      setPendingView(null)
    }
  }

  // Đưa câu hỏi lỗi về ô nhập để sửa: gỡ cả tin lỗi lẫn câu hỏi cũ để không bị lặp khi gửi lại
  function editFailed(text) {
    setMessages((current) => {
      const withoutError = current.filter((message) => !message.error)
      return withoutError.at(-1)?.role === 'user' ? withoutError.slice(0, -1) : withoutError
    })
    setQuestion(text)
    inputRef.current?.focus()
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

  if (!user && token && meError) {
    return (
      <div className="grid h-full place-items-center bg-cream px-6">
        <div className="max-w-[460px] text-center">
          <div className="rule mx-auto" />
          <h1 className="mt-5 text-2xl font-extrabold text-navy">{meError}</h1>
          <p className="mt-3 leading-relaxed text-muted">Phiên đăng nhập của bạn vẫn được giữ. Kiểm tra kết nối mạng rồi bấm Thử lại.</p>
          <button type="button" onClick={() => { setAuthChecked(false); setMeRetry((n) => n + 1) }} className="btn btn-cta mt-8 px-8">Thử lại</button>
        </div>
      </div>
    )
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

  if (window.location.pathname.replace(/\/$/, '') === '/quan-ly') {
    return <Suspense fallback={PageLoading}><Manage authFetch={authFetch} user={user} /></Suspense>
  }
  if (window.location.pathname.replace(/\/$/, '') === '/ho-so') {
    return <Suspense fallback={PageLoading}><Profile authFetch={authFetch} user={user} /></Suspense>
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
            <div key={thread.id} className={`thread-row ${thread.id === activeThreadId ? 'active' : ''}`}>
              <button type="button" onClick={() => openThread(thread.id)} className="open" aria-current={thread.id === activeThreadId ? 'true' : undefined}>
                <span className="dot" />
                <span className="title">{thread.title || 'Cuộc trò chuyện mới'}</span>
              </button>
              <button type="button" onClick={(event) => deleteThread(thread.id, event)} className="del" aria-label={`Xoá cuộc trò chuyện ${thread.title || ''}`.trim()}><X size={14} strokeWidth={2.2} /></button>
            </div>
          ))}
        </div>

        <a href="/ho-so" className="btn-dark mt-4 grid place-items-center font-semibold no-underline hover:no-underline">Hồ sơ &amp; WIG</a>
        <div className="mt-4 flex items-center gap-3 border-t border-white/[.12] px-2 pt-4">
          <div className="grid h-10 w-10 shrink-0 place-items-center bg-[linear-gradient(180deg,#F9DD0E_0%,#E0B90C_100%)] text-[17px] font-extrabold text-navy">
            {(displayName || user.email || '?').trim().split(/\s+/).pop().slice(0, 1).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate font-bold">{displayName || 'Nhân viên'}</div>
            <div className="truncate text-[#A9AAC8]">{user.employee?.department || user.email}</div>
          </div>
        </div>
        <div className="mt-2 flex items-center gap-1 px-1">
          <button onClick={handleSwitchGoogleAccount} className="side-link">Đổi tài khoản</button>
          <span className="text-[#6E6F96]" aria-hidden="true">·</span>
          <button onClick={handleLogout} className="side-link">Đăng xuất</button>
        </div>
      </aside>
      {mobileNav && <div className="absolute inset-0 z-20 bg-[rgba(13,14,43,.45)] min-[900px]:hidden" onClick={() => setMobileNav(false)} />}

      <div className="flex min-w-0 flex-1 flex-col bg-cream">
        <header className="flex items-center justify-between gap-4 border-b border-line bg-white px-4 py-3.5 sm:px-7">
          <div className="flex min-w-0 items-center gap-3.5">
            <button onClick={() => setMobileNav(true)} className="grid h-11 w-11 shrink-0 place-items-center border border-line bg-white text-navy min-[900px]:hidden" aria-label="Mở menu"><Menu size={20} /></button>
            <h1 className="min-w-0 truncate text-lg font-extrabold text-navy">{activeThread?.title || 'Cuộc trò chuyện mới'}</h1>
          </div>
          <div className="flex shrink-0 items-center gap-5">
            <div className="flex items-center gap-2 text-ink">
              <span className={`status-dot ${statusOnline ? 'online' : statusPending ? 'pending' : ''}`} />
              <span className="hidden sm:inline">{statusOnline ? `${approvedCount} tài liệu đã duyệt` : statusPending ? 'Đang kết nối...' : 'Mất kết nối'}</span>
              <span className="sm:hidden">{statusOnline ? `${approvedCount} tài liệu` : statusPending ? '...' : 'Mất kết nối'}</span>
            </div>
            {manageInfo?.can_manage && <a href="/quan-ly" className="hidden font-semibold text-muted sm:inline">Quản lý tri thức</a>}
            {manageInfo?.is_admin && <a href="/admin" className="hidden font-semibold text-muted sm:inline">Quản trị</a>}
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-10 sm:px-6">
          <div className="mx-auto max-w-[760px]" role="log" aria-live="polite" aria-label="Cuộc trò chuyện">
            {threadLoading ? (
              <div className="flex items-center gap-3 text-muted"><span className="typing"><i /><i /><i /></span>Đang tải cuộc trò chuyện…</div>
            ) : threadError ? (
              <div className="flex flex-col items-start gap-3" role="alert">
                <p className="text-danger">{threadError}</p>
                <button type="button" className="btn btn-report-send px-5" onClick={() => openThread(activeThreadId)}>Thử lại</button>
              </div>
            ) : messages.length === 0 && !loading ? <EmptyState firstName={firstName} onAsk={ask} /> : (
              <div className="flex flex-col gap-8">
                {messages.map((message, index) => {
                  const isLast = index === messages.length - 1
                  return (
                    <Message
                      key={`${message.role}-${index}`}
                      message={message}
                      onReport={(payload) => reportAnswer(index, payload)}
                      onRetry={isLast && message.retryText && !loading ? () => ask(message.retryText, { retry: true }) : null}
                      onEdit={isLast && message.retryText && !loading ? () => editFailed(message.retryText) : null}
                      onSuggest={isLast && !loading ? ask : null}
                    />
                  )
                })}
                {loading && pendingView === viewKey && (
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
            {notice && <p className="mb-2 bg-[#FDE8E6] px-4 py-2.5 text-[#9B1C14]" role="alert">{notice}</p>}
            <div className="flex items-end gap-2 rounded-lg border border-line bg-white py-1.5 pl-5 pr-1.5 shadow-[0_4px_16px_rgba(20,21,58,.05)]">
              <textarea
                ref={inputRef}
                rows={1}
                value={question}
                maxLength={MAX_QUESTION}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  // Enter gửi, Shift+Enter xuống dòng (bỏ qua khi đang gõ tiếng Việt bằng IME)
                  if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); ask() }
                }}
                className="chat-input"
                placeholder="Hỏi về quy định nội bộ…"
                aria-label="Câu hỏi"
              />
              <button disabled={loading || threadLoading || !question.trim()} className="send-btn" aria-label="Gửi câu hỏi"><ArrowUp size={20} strokeWidth={2.6} /></button>
            </div>
            {question.length >= MAX_QUESTION - 300 && <p className={`mt-1.5 text-right ${question.length >= MAX_QUESTION ? 'font-bold text-danger' : 'text-muted'}`} aria-live="polite">{question.length}/{MAX_QUESTION} ký tự</p>}
            <p className="mt-2.5 text-center leading-[1.5] text-muted">Câu trả lời dựa trên tài liệu nội bộ đã được duyệt; phần pháp luật chung chỉ để tham khảo. Ứng dụng ghi nhận thời gian và tính năng sử dụng để cải thiện sản phẩm.</p>
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
          <p className="mt-6 text-center text-muted">Ứng dụng ghi nhận thời gian và tính năng sử dụng để cải thiện sản phẩm.</p>
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

const REPORT_REASONS = [
  { value: 'wrong_info', label: 'Sai thông tin' },
  { value: 'missing_info', label: 'Thiếu ý' },
  { value: 'off_topic', label: 'Không đúng câu hỏi' },
  { value: 'other', label: 'Khác' },
]

function ReportForm({ onSubmit, onCancel }) {
  const [reason, setReason] = useState('wrong_info')
  const [note, setNote] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  async function submit(event) {
    event.preventDefault()
    setSending(true)
    setError('')
    try {
      await onSubmit({ reason, note: note.trim() })
    } catch (err) {
      setError(err.message)
      setSending(false)
    }
  }
  return (
    <form onSubmit={submit} className="mt-3 flex flex-col gap-3 rounded-lg border border-line bg-white p-4">
      <span className="font-bold text-navy">Câu trả lời này sai ở đâu?</span>
      <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Lý do báo sai">
        {REPORT_REASONS.map((item) => (
          <button key={item.value} type="button" role="radio" aria-checked={reason === item.value} onClick={() => setReason(item.value)} className={`report-reason ${reason === item.value ? 'active' : ''}`}>{item.label}</button>
        ))}
      </div>
      <textarea value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} rows={3} placeholder="Ghi chú thêm (tuỳ chọn), ví dụ: thông tin đúng là…" aria-label="Ghi chú" className="report-note" />
      {error && <p className="text-danger" role="alert">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <button disabled={sending} className="btn btn-report-send">{sending ? 'Đang gửi...' : 'Gửi báo cáo'}</button>
        <button type="button" onClick={onCancel} className="btn-link">Huỷ</button>
      </div>
    </form>
  )
}

function ReportControl({ message, onReport }) {
  const [open, setOpen] = useState(false)
  if (message.reported) return <p className="mt-3 font-semibold text-success">Đã gửi báo cáo, cảm ơn bạn. Admin sẽ kiểm tra lại câu trả lời này.</p>
  if (open) return <ReportForm onSubmit={onReport} onCancel={() => setOpen(false)} />
  return (
    <button type="button" onClick={() => setOpen(true)} className="report-trigger mt-3">
      <Flag size={16} /> Báo sai
    </button>
  )
}

function sectionOf({ source, heading }) {
  const parts = (heading || '').split('>').map((part) => part.trim()).filter(Boolean)
  if (parts[0] === source) parts.shift()
  return parts.join(' › ')
}

// Mỗi tài liệu chỉ hiện một dòng: tên tài liệu, các mục đã dùng, phiên bản (không lặp lại tên tài liệu trong tên mục)
function groupCitations(citations) {
  const groups = new Map()
  citations.forEach((citation) => {
    const key = `${citation.source}|${citation.version || ''}`
    if (!groups.has(key)) groups.set(key, { source: citation.source, version: citation.version, sections: [] })
    const section = sectionOf(citation)
    const group = groups.get(key)
    if (section && !group.sections.includes(section)) group.sections.push(section)
  })
  return [...groups.values()]
}

function Message({ message, onReport, onRetry, onEdit, onSuggest }) {
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
        <MarkdownView source={message.text} />
        {message.citations?.length > 0 && (
          <div className="mt-5 border-t border-line pt-3">
            <span className="font-bold text-gold-text">Nguồn</span>
            <ul className="mt-2 flex flex-col gap-2">
              {groupCitations(message.citations).map((group) => (
                <li key={`${group.source}-${group.version}`} className="source-item">
                  <strong>{group.source}</strong>
                  {group.sections.length > 0 && ` · ${group.sections.join(' · ')}`}
                  {group.version && group.version !== 'unknown' ? ` · v${group.version}` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}
        {message.error && (onRetry || onEdit) && (
          <div className="mt-3 flex flex-wrap gap-2">
            {onRetry && <button type="button" className="btn btn-report-send px-5" onClick={onRetry}>Thử lại</button>}
            {onEdit && <button type="button" className="btn btn-outline-sm px-5" onClick={onEdit}>Sửa câu hỏi</button>}
          </div>
        )}
        {!message.error && onReport && <ReportControl message={message} onReport={onReport} />}
        {onSuggest && message.suggestions?.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2" aria-label="Câu hỏi gợi ý">
            {message.suggestions.map((item) => (
              <button key={item} type="button" className="followup" onClick={() => onSuggest(item)}>{item}</button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default App

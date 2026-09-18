import { useEffect, useState } from 'react'
import {
  ArrowUp,
  BookOpen,
  Bot,
  Check,
  ChevronDown,
  CircleHelp,
  Database,
  FileSearch,
  LayoutDashboard,
  Link2,
  Menu,
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

function App() {
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState([])
  const [department, setDepartment] = useState('')
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(false)
  const [debugQuery, setDebugQuery] = useState('')
  const [debugRows, setDebugRows] = useState([])
  const [debugging, setDebugging] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)

  useEffect(() => {
    loadHealth()
  }, [])

  async function loadHealth() {
    try {
      const response = await fetch('/api/health')
      setHealth(await response.json())
    } catch {
      setHealth({ status: 'offline' })
    }
  }

  async function readApiResponse(response) {
    const raw = await response.text()
    try {
      return raw ? JSON.parse(raw) : {}
    } catch {
      return { detail: raw || `Backend trả về HTTP ${response.status}` }
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
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: text,
          user_department: department || null,
          history,
        }),
      })
      const data = await readApiResponse(response)
      if (!response.ok) {
        throw new Error(data.detail || `Backend trả về HTTP ${response.status}`)
      }
      setMessages((current) => [...current, { role: 'assistant', text: data.answer, citations: data.citations || [] }])
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

  return (
    <div className="min-h-screen bg-[#f7f5ef] text-ink">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <header className="mobile-header lg:hidden">
        <Brand />
        <button className="icon-button" onClick={() => setMobileNav(true)} aria-label="Mở menu"><Menu size={19} /></button>
      </header>
      <div className="relative mx-auto flex min-h-screen max-w-[1540px]">
        <aside className={`${mobileNav ? 'mobile-open' : ''} sidebar fixed inset-y-0 left-0 z-30 w-[272px] -translate-x-full border-r border-[#dce3de] bg-[#123d52] px-7 py-7 text-white transition-transform lg:static lg:translate-x-0`}>
          <div className="flex items-start justify-between"><Brand light /><button className="icon-button light lg:hidden" onClick={() => setMobileNav(false)} aria-label="Đóng menu"><X size={18} /></button></div>
          <div className="mt-16"><div className="nav-caption">Workspace</div><nav className="mt-3 space-y-1"><NavItem icon={<LayoutDashboard size={17} />} label="Staff desk" active /><NavItem icon={<BookOpen size={17} />} label="Knowledge Vault" /><NavItem icon={<FileSearch size={17} />} label="Draft review" /></nav></div>
          <div className="mt-14"><div className="nav-caption">System</div><div className="mt-3 space-y-1"><NavItem icon={<ShieldCheck size={17} />} label="Access policy" /><NavItem icon={<CircleHelp size={17} />} label="Help center" /></div></div>
          <div className="sidebar-footer"><div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[.14em] text-[#9ec3c1]"><span className={`status-dot ${statusOnline ? 'online' : ''}`} /> {statusOnline ? 'System online' : 'Connecting'}</div><p className="mt-3 text-xs leading-5 text-[#b3d0cc]">Grounded answers from approved Obsidian knowledge.</p></div>
        </aside>
        {mobileNav && <button className="fixed inset-0 z-20 bg-[#0b2632]/40 lg:hidden" onClick={() => setMobileNav(false)} aria-label="Đóng menu" />}
        <main className="relative min-w-0 flex-1 px-5 py-6 sm:px-8 lg:px-12 lg:py-9 xl:px-16">
          <div className="mx-auto max-w-[1150px]">
            <div className="flex flex-col justify-between gap-5 border-b border-[#dce3de] pb-8 md:flex-row md:items-start">
              <div><div className="eyebrow"><span className="eyebrow-line" /> Internal knowledge desk</div><h1 className="display-title mt-4 max-w-[700px]">Ask with context.<br /><em>Act with clarity.</em></h1><p className="mt-4 max-w-[590px] text-sm leading-6 text-[#6e7c80]">Trợ lý nội bộ cho quy định nhân sự, nghỉ phép, tài chính và quy trình chuyên môn của Trường Việt Anh.</p></div>
              <div className="flex items-center gap-2 rounded-full border border-[#dce3de] bg-white/70 px-3 py-2 text-xs text-[#607175]"><span className={`status-dot ${statusOnline ? 'online' : ''}`} />{statusOnline ? `${health.approved_notes || 0}/${health.knowledge_notes || 0} approved notes` : 'Backend offline'}</div>
            </div>

            <div className="mt-7 grid gap-6 xl:grid-cols-[minmax(0,1fr)_330px]">
              <section className="paper-panel flex min-h-[650px] flex-col overflow-hidden rounded-2xl">
                <div className="flex items-center justify-between border-b border-[#e2e7e1] px-5 py-4 sm:px-7"><div><p className="text-sm font-bold text-ink">Staff conversation</p><p className="mt-1 text-[11px] text-[#889598]">Grounded assistant</p></div><div className="flex items-center gap-2 rounded-full bg-[#e8f4ed] px-3 py-1.5 text-[10px] font-bold uppercase tracking-[.12em] text-[#24725f]"><Sparkles size={13} /> RAG ready</div></div>
                <div className="flex-1 px-5 py-7 sm:px-8">
                  {messages.length === 0 ? <EmptyState onAsk={ask} /> : <div className="space-y-7">{messages.map((message, index) => <Message key={`${message.role}-${index}`} message={message} />)}{loading && <div className="flex gap-3"><div className="avatar"><Bot size={16} /></div><div className="bubble assistant-bubble"><span className="typing"><i /><i /><i /></span> Đang tra cứu trong Vault...</div></div>}</div>}
                </div>
                <form className="border-t border-[#e2e7e1] bg-[#fcfbf7] p-4 sm:p-5" onSubmit={(event) => { event.preventDefault(); ask() }}><div className="relative flex items-center"><input value={question} onChange={(event) => setQuestion(event.target.value)} className="h-14 w-full rounded-xl border border-[#d8dfda] bg-white pl-4 pr-16 text-sm text-ink outline-none transition placeholder:text-[#9aa5a5] focus:border-[#3c8b79] focus:ring-4 focus:ring-[#3c8b79]/10" placeholder="Hỏi về quy định nội bộ..." /><button disabled={loading || !question.trim()} className="absolute right-2 grid h-10 w-10 place-items-center rounded-lg bg-[#e9795c] text-white transition hover:bg-[#d9654a] disabled:cursor-not-allowed disabled:opacity-35" aria-label="Gửi câu hỏi"><ArrowUp size={18} strokeWidth={2.5} /></button></div><div className="mt-3 flex items-center justify-between px-1 text-[10px] text-[#9aa5a5]"><span>Thêm/duyệt dữ liệu qua trang <a href="/admin" className="underline">/admin</a></span><span>Vietnamese · Internal</span></div></form>
              </section>

              <aside className="space-y-5">
                <section className="paper-panel rounded-2xl p-5"><div className="flex items-start justify-between"><div><p className="panel-kicker">Your context</p><h2 className="panel-title mt-1">Phạm vi tìm kiếm</h2></div><ShieldCheck className="text-[#3d8d79]" size={20} /></div><p className="mt-3 text-xs leading-5 text-[#7c898a]">Thu hẹp phạm vi tra cứu theo phòng ban (không bắt buộc).</p><Field label="Phòng ban"><Select value={department} onChange={(event) => setDepartment(event.target.value)}><option value="">Tất cả phòng ban</option><option>HR</option><option>Finance</option><option>Academic</option><option>Admin</option></Select></Field><div className="mt-4 flex items-center gap-2 border-t border-[#e7ebe6] pt-4 text-[11px] text-[#718081]"><Check size={14} className="text-[#3d8d79]" /> Approved sources only</div></section>
                <section className="paper-panel rounded-2xl p-5"><div className="flex items-start justify-between"><div><p className="panel-kicker">Vault control</p><h2 className="panel-title mt-1">Knowledge sync</h2></div><Database className="text-[#3d8d79]" size={20} /></div><p className="mt-3 text-xs leading-5 text-[#7c898a]">Dữ liệu lưu trên Supabase, đồng bộ tự động ngay khi HR/admin duyệt trong <a href="/admin" className="underline">/admin</a> — không cần reindex thủ công.</p></section>
                <section className="paper-panel rounded-2xl p-5"><div className="flex items-start justify-between"><div><p className="panel-kicker">Developer view</p><h2 className="panel-title mt-1">Debug retrieval</h2></div><Search className="text-[#3d8d79]" size={19} /></div><form onSubmit={debugSearch} className="mt-4 flex gap-2"><input value={debugQuery} onChange={(event) => setDebugQuery(event.target.value)} className="min-w-0 flex-1 rounded-lg border border-[#d8dfda] bg-white px-3 py-2 text-xs outline-none focus:border-[#3c8b79]" placeholder="nghỉ phép" /><button className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-[#e8f4ed] text-[#26725f]" aria-label="Tìm chunks"><FileSearch size={15} /></button></form>{debugging && <p className="mt-3 text-[11px] text-[#899697]">Đang tìm chunks...</p>}{debugRows.length > 0 && <div className="mt-3 space-y-2">{debugRows.slice(0, 3).map((row) => <div key={row.id} className="rounded-lg border border-[#e5eae4] bg-[#fcfbf7] p-2.5"><div className="flex items-center justify-between gap-2 text-[10px] font-bold text-[#26725f]"><span className="truncate">{row.metadata.source_file || row.metadata.source}</span><span>{Number(row.score).toFixed(2)}</span></div><p className="mt-1 truncate text-[10px] text-[#7d8a8a]">{row.metadata.heading}</p></div>)}</div>}</section>
              </aside>
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}

function Brand({ light = false }) { return <div className="flex items-center gap-3"><div className={`brand-mark ${light ? 'brand-light' : ''}`}>VA</div><div className={`font-display text-lg leading-none ${light ? 'text-white' : 'text-ink'}`}>Staff Assistant<span className={`mt-1 block font-sans text-[9px] font-bold uppercase tracking-[.16em] ${light ? 'text-[#9ec3c1]' : 'text-[#789096]'}`}>Major Education</span></div></div> }
function NavItem({ icon, label, active }) { return <div className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm ${active ? 'bg-white/10 text-white' : 'text-[#b3d0cc]'}`}>{icon}<span>{label}</span>{active && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-[#f4c95d]" />}</div> }
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

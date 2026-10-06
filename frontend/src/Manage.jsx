import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ChevronDown, ChevronRight, History, Lock, Plus, RotateCcw, Search, Trash2 } from 'lucide-react'
import { MarkdownView } from './markdown.jsx'

const DEPT_LABELS = { HR: 'Nhân sự', Finance: 'Tài chính', Academic: 'Học thuật', Admin: 'Vận hành', Unassigned: 'Dùng chung' }
const DEPT_ORDER = ['HR', 'Finance', 'Academic', 'Admin', 'Unassigned']
const STATUS_LABELS = { approved: 'Đã duyệt — chatbot dùng ngay', draft: 'Nháp — chatbot chưa dùng', rejected: 'Từ chối' }
const ACCESS_LABELS = { staff: 'Nhân viên', manager: 'Quản lý', admin: 'Quản trị' }
const KIND_LABELS = { create: 'Tạo mới', update: 'Sửa', delete: 'Xoá', restore: 'Khôi phục', sync: 'Đồng bộ' }
const EMPTY_DRAFT = { title: '', department: 'HR', status: 'approved', access_level: 'staff', content: '' }

async function call(authFetch, path, options = {}) {
  const response = await authFetch(`/api/manage${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  })
  const raw = await response.text()
  let data = {}
  try { data = raw ? JSON.parse(raw) : {} } catch { data = { detail: raw } }
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join('; ') : data.detail
    throw new Error(detail || `Lỗi ${response.status}`)
  }
  return data
}

const truncate = (text, max) => (text.length > max ? `${text.slice(0, max - 1)}…` : text)
const formatTime = (iso) => (iso ? new Date(iso).toLocaleString('vi-VN') : '')

export default function Manage({ authFetch: authFetchProp, user }) {
  // App tạo lại authFetch mỗi lần render; giữ bản mới nhất trong ref để các effect không chạy lại liên tục.
  const fetchRef = useRef(authFetchProp)
  fetchRef.current = authFetchProp
  const authFetch = useCallback((...args) => fetchRef.current(...args), [])
  const [me, setMe] = useState(null)
  const [notes, setNotes] = useState([])
  const [query, setQuery] = useState('')
  const [collapsed, setCollapsed] = useState({})
  const [selectedId, setSelectedId] = useState(null)
  const [note, setNote] = useState(null)
  const [draft, setDraft] = useState(EMPTY_DRAFT)
  const [tab, setTab] = useState('preview')
  const [versions, setVersions] = useState([])
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState(null)
  const [loadError, setLoadError] = useState('')
  const toastTimer = useRef(null)
  const dirty = useMemo(() => {
    if (selectedId === 'new') return draft.title.trim() !== '' || draft.content.trim() !== ''
    return Boolean(note) && ['title', 'department', 'status', 'access_level', 'content'].some((k) => draft[k] !== note[k])
  }, [selectedId, note, draft])

  const showToast = useCallback((text, error = false) => {
    clearTimeout(toastTimer.current)
    setToast({ text, error })
    toastTimer.current = setTimeout(() => setToast(null), 5000)
  }, [])

  const loadNotes = useCallback(async (search = '') => {
    try {
      setNotes(await call(authFetch, `/notes${search ? `?q=${encodeURIComponent(search)}` : ''}`))
      setLoadError('')
    } catch (error) {
      setLoadError(error.message)
    }
  }, [authFetch])

  useEffect(() => {
    call(authFetch, '/me').then(setMe).catch((error) => { setMe({ can_manage: false }); setLoadError(error.message) })
  }, [authFetch])

  useEffect(() => {
    if (!me?.can_manage) return undefined
    const timer = setTimeout(() => loadNotes(query.trim()), query ? 300 : 0)
    return () => clearTimeout(timer)
  }, [me, query, loadNotes])

  useEffect(() => {
    const warn = (event) => { if (dirty) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const confirmLeave = () => !dirty || window.confirm('Bạn có thay đổi chưa lưu. Bỏ thay đổi và tiếp tục?')

  const openNote = useCallback(async (id) => {
    if (id !== selectedId && !confirmLeave()) return
    setSelectedId(id)
    setVersions([])
    try {
      const data = await call(authFetch, `/notes/${encodeURIComponent(id)}`)
      setNote(data)
      setDraft({ title: data.title, department: data.department, status: data.status, access_level: data.access_level || 'staff', content: data.content })
      setTab(data.editable ? 'edit' : 'preview')
      call(authFetch, `/notes/${encodeURIComponent(id)}/versions`).then(setVersions).catch(() => setVersions([]))
    } catch (error) {
      showToast(error.message, true)
      setSelectedId(null)
      setNote(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authFetch, selectedId, dirty, showToast])

  const startNew = () => {
    if (!confirmLeave()) return
    const first = (me?.departments || []).find((d) => d !== 'Unassigned') || me?.departments?.[0] || 'HR'
    setSelectedId('new')
    setNote(null)
    setVersions([])
    setDraft({ ...EMPTY_DRAFT, department: first })
    setTab('edit')
  }

  const save = async () => {
    setBusy(true)
    try {
      if (selectedId === 'new') {
        const { id } = await call(authFetch, '/notes', { method: 'POST', body: JSON.stringify(draft) })
        showToast('Đã tạo — chatbot dùng được ngay.')
        await loadNotes(query.trim())
        setSelectedId(id)
        const data = await call(authFetch, `/notes/${encodeURIComponent(id)}`)
        setNote(data)
        setDraft({ title: data.title, department: data.department, status: data.status, access_level: data.access_level || 'staff', content: data.content })
        call(authFetch, `/notes/${encodeURIComponent(id)}/versions`).then(setVersions).catch(() => {})
      } else {
        await call(authFetch, `/notes/${encodeURIComponent(selectedId)}`, { method: 'PUT', body: JSON.stringify({ ...draft, base_updated_at: note?.updated_at }) })
        showToast(draft.status === 'approved' ? 'Đã lưu — chatbot dùng bản mới ngay.' : 'Đã lưu.')
        await loadNotes(query.trim())
        const data = await call(authFetch, `/notes/${encodeURIComponent(selectedId)}`)
        setNote(data)
        setDraft({ title: data.title, department: data.department, status: data.status, access_level: data.access_level || 'staff', content: data.content })
        call(authFetch, `/notes/${encodeURIComponent(selectedId)}/versions`).then(setVersions).catch(() => {})
      }
    } catch (error) {
      showToast(error.message, true)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!window.confirm(`Xoá "${note.title}"? Chatbot sẽ không dùng tài liệu này nữa (vẫn khôi phục được từ lịch sử).`)) return
    setBusy(true)
    try {
      await call(authFetch, `/notes/${encodeURIComponent(selectedId)}`, { method: 'DELETE' })
      showToast('Đã xoá.')
      setSelectedId(null)
      setNote(null)
      setDraft(EMPTY_DRAFT)
      await loadNotes(query.trim())
    } catch (error) {
      showToast(error.message, true)
    } finally {
      setBusy(false)
    }
  }

  const restore = async (version) => {
    if (!window.confirm(`Khôi phục về bản ${version.version_no} (${KIND_LABELS[version.change_kind]} lúc ${formatTime(version.created_at)})?`)) return
    setBusy(true)
    try {
      await call(authFetch, `/notes/${encodeURIComponent(selectedId)}/restore/${version.id}`, { method: 'POST' })
      showToast('Đã khôi phục.')
      await loadNotes(query.trim())
      const data = await call(authFetch, `/notes/${encodeURIComponent(selectedId)}`)
      setNote(data)
      setDraft({ title: data.title, department: data.department, status: data.status, access_level: data.access_level || 'staff', content: data.content })
      call(authFetch, `/notes/${encodeURIComponent(selectedId)}/versions`).then(setVersions).catch(() => {})
    } catch (error) {
      showToast(error.message, true)
    } finally {
      setBusy(false)
    }
  }

  const linkMap = useMemo(() => {
    const map = {}
    for (const link of note?.links?.outgoing || []) map[link.target] = link.id
    return map
  }, [note])

  const grouped = useMemo(() => {
    const groups = {}
    for (const item of notes) (groups[item.department] ||= []).push(item)
    return DEPT_ORDER.filter((d) => groups[d]).map((d) => [d, groups[d]])
  }, [notes])

  if (!me) return <div className="grid h-full place-items-center bg-cream text-muted">Đang tải...</div>
  if (!me.can_manage) {
    return (
      <div className="grid h-full place-items-center bg-cream px-6">
        <div className="max-w-[520px] text-center">
          <div className="rule mx-auto" />
          <h1 className="mt-5 text-3xl font-extrabold text-navy">Chưa có quyền quản lý tri thức</h1>
          <p className="mt-3 text-lg leading-relaxed text-muted">{loadError || 'Tài khoản của bạn chưa được cấp quyền. Liên hệ admin để được thêm làm leader của phòng ban.'}</p>
          <a href="/" className="btn btn-cta mt-8 inline-flex px-8">← Về trợ lý nội bộ</a>
        </div>
      </div>
    )
  }

  const editable = selectedId === 'new' || Boolean(note?.editable)
  const departmentChoices = (me.departments || []).filter((d) => d !== 'Unassigned' || me.is_admin)

  return (
    <div className="mg-shell">
      <header className="mg-top">
        <a href="/" className="mg-back"><ArrowLeft size={18} /> Trợ lý nội bộ</a>
        <div className="min-w-0">
          <div className="font-bold text-gold-text">Quản lý tri thức</div>
          <div className="truncate text-muted">{user.email} · {me.is_admin ? 'Admin — mọi phòng ban' : `Leader: ${(me.departments || []).map((d) => DEPT_LABELS[d]).join(', ')}`}</div>
        </div>
        <button type="button" className="btn btn-cta mg-new" onClick={startNew}><Plus size={18} /> Note mới</button>
      </header>

      <div className="mg-body">
        <aside className="mg-tree" aria-label="Danh sách tài liệu">
          <div className="mg-search">
            <Search size={18} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm tiêu đề, nội dung…" aria-label="Tìm tài liệu" />
          </div>
          {loadError && <p className="p-4 text-danger" role="alert">{loadError}</p>}
          <div className="mg-scroll">
            {grouped.map(([department, items]) => (
              <section key={department}>
                <button type="button" className="mg-group" onClick={() => setCollapsed((c) => ({ ...c, [department]: !c[department] }))} aria-expanded={!collapsed[department]}>
                  {collapsed[department] ? <ChevronRight size={18} /> : <ChevronDown size={18} />}
                  <span>{DEPT_LABELS[department]}</span>
                  <span className="mg-count">{items.length}</span>
                </button>
                {!collapsed[department] && items.map((item) => (
                  <button type="button" key={item.id} className={`mg-item ${item.id === selectedId ? 'active' : ''}`} onClick={() => openNote(item.id)} title={item.title}>
                    <span className="mg-item-title">{item.title}</span>
                    {item.status !== 'approved' && <span className="mg-tag">{item.status === 'draft' ? 'Nháp' : 'Từ chối'}</span>}
                    {!item.editable && <Lock size={16} aria-label="Chỉ xem" />}
                  </button>
                ))}
              </section>
            ))}
            {!grouped.length && !loadError && <p className="p-4 text-muted">{query ? 'Không tìm thấy tài liệu phù hợp.' : 'Chưa có tài liệu nào.'}</p>}
          </div>
        </aside>

        <main className="mg-main">
          {!selectedId ? (
            <div className="mg-empty">
              <div className="rule" />
              <h1>Chọn một tài liệu để xem hoặc sửa</h1>
              <p>Bên trái là toàn bộ kho tri thức theo phòng ban. Tài liệu có biểu tượng khoá thuộc phòng khác — bạn chỉ xem được. Sửa xong bấm Lưu là chatbot dùng bản mới ngay.</p>
              <button type="button" className="btn btn-cta" onClick={startNew}><Plus size={18} /> Tạo note mới</button>
            </div>
          ) : (
            <>
              {!editable && <div className="mg-notice"><Lock size={18} /> Tài liệu thuộc phòng {DEPT_LABELS[note?.department]} — bạn chỉ có quyền xem. Cần sửa thì nhờ leader phòng đó hoặc admin.</div>}
              {note?.source_file?.startsWith('vault:') && editable && (
                <div className="mg-notice info">Tài liệu này đồng bộ từ wiki Obsidian ({note.source_file.slice(6)}). Nếu file Obsidian được sửa sau, bản sửa trên web sẽ bị thay bằng bản Obsidian — bản cũ vẫn khôi phục được ở mục Lịch sử.</div>
              )}
              <div className="mg-fields">
                <label className="mg-field wide">Tiêu đề
                  <input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} disabled={!editable} maxLength={200} />
                </label>
                <label className="mg-field">Phòng ban
                  <select value={draft.department} onChange={(event) => setDraft({ ...draft, department: event.target.value })} disabled={!editable}>
                    {(editable ? departmentChoices : [draft.department]).map((d) => <option key={d} value={d}>{DEPT_LABELS[d]}</option>)}
                  </select>
                </label>
                <label className="mg-field">Trạng thái
                  <select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value })} disabled={!editable}>
                    {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
                <label className="mg-field">Dành cho
                  <select value={draft.access_level} onChange={(event) => setDraft({ ...draft, access_level: event.target.value })} disabled={!editable}>
                    {Object.entries(ACCESS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
              </div>

              <div className="mg-tabs" role="tablist">
                {[['edit', 'Soạn'], ['preview', 'Xem trước'], ['graph', 'Đồ thị liên kết']].map(([key, label]) => (
                  <button type="button" role="tab" aria-selected={tab === key} key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}</button>
                ))}
                {dirty && <span className="mg-dirty">Chưa lưu</span>}
              </div>

              <div className="mg-pane">
                {tab === 'edit' && (
                  <textarea className="mg-editor" value={draft.content} onChange={(event) => setDraft({ ...draft, content: event.target.value })} readOnly={!editable}
                    placeholder="Viết nội dung bằng markdown. Dùng [[tên-trang]] để liên kết tới tài liệu khác." aria-label="Nội dung" spellCheck={false} />
                )}
                {tab === 'preview' && (
                  <article className="mg-preview">
                    <h1 className="mg-preview-title">{draft.title || 'Chưa có tiêu đề'}</h1>
                    <MarkdownView source={draft.content} linkMap={linkMap} onOpenLink={openNote} />
                  </article>
                )}
                {tab === 'graph' && <LinkGraph note={note} onOpen={openNote} />}
              </div>

              {editable && (
                <div className="mg-actions">
                  <button type="button" className="btn btn-cta" onClick={save} disabled={busy || !dirty || !draft.title.trim() || !draft.content.trim()}>{busy ? 'Đang lưu…' : 'Lưu'}</button>
                  {selectedId !== 'new' && <button type="button" className="btn mg-danger" onClick={remove} disabled={busy}><Trash2 size={18} /> Xoá</button>}
                </div>
              )}
            </>
          )}
        </main>

        {note && (
          <aside className="mg-side" aria-label="Liên kết và lịch sử">
            <section>
              <h2>Liên kết đi</h2>
              {note.links.outgoing.length === 0 && note.links.unresolved.length === 0 && <p className="mg-muted">Chưa liên kết tới trang nào.</p>}
              {note.links.outgoing.map((link) => <button type="button" key={link.id} className="mg-link" onClick={() => openNote(link.id)}>{link.title}</button>)}
              {note.links.unresolved.map((target) => <span key={target} className="mg-link missing" title="Trang này chưa có trong kho tri thức">{target}</span>)}
            </section>
            <section>
              <h2>Được liên kết từ</h2>
              {note.links.incoming.length === 0 && <p className="mg-muted">Chưa có trang nào trỏ tới.</p>}
              {note.links.incoming.map((link) => <button type="button" key={link.id} className="mg-link" onClick={() => openNote(link.id)}>{link.title}</button>)}
            </section>
            <section>
              <h2><History size={18} /> Lịch sử</h2>
              {versions.length === 0 && <p className="mg-muted">Chưa có lịch sử (từ lần sửa tiếp theo sẽ được ghi lại).</p>}
              {versions.map((version) => (
                <div key={version.id} className="mg-version">
                  <div><strong>{KIND_LABELS[version.change_kind] || version.change_kind}</strong> · bản {version.version_no}</div>
                  <div className="mg-muted">{version.changed_by || 'Hệ thống'}</div>
                  <div className="mg-muted">{formatTime(version.created_at)}</div>
                  {note.editable && <button type="button" className="mg-restore" onClick={() => restore(version)} disabled={busy}><RotateCcw size={16} /> Khôi phục bản này</button>}
                </div>
              ))}
            </section>
          </aside>
        )}
      </div>

      {toast && <div className={`mg-toast ${toast.error ? 'error' : ''}`} role="status">{toast.text}</div>}
    </div>
  )
}

/** Đồ thị cục bộ kiểu Obsidian: trang đang mở ở giữa, trang trỏ tới nó bên trái, trang nó trỏ tới bên phải. */
function LinkGraph({ note, onOpen }) {
  if (!note) return <p className="mg-muted p-6">Lưu note trước để xem liên kết.</p>
  const incoming = note.links.incoming
  const outgoing = note.links.outgoing
  const show = 9
  const rows = Math.max(Math.min(incoming.length, show), Math.min(outgoing.length, show), 1) + 1
  const height = Math.max(rows * 52 + 40, 220)
  const colX = { left: 20, center: 305, right: 590 }
  const nodeW = 250
  const yFor = (index, count) => (height - count * 52) / 2 + index * 52 + 6
  const side = (items, x, isIncoming) => items.slice(0, show).map((item, index) => {
    const y = yFor(index, Math.min(items.length, show))
    return (
      <g key={item.id} className="g-node" onClick={() => onOpen(item.id)} role="button" tabIndex={0} onKeyDown={(event) => event.key === 'Enter' && onOpen(item.id)}>
        <line x1={isIncoming ? x + nodeW : colX.center} y1={isIncoming ? y + 20 : height / 2} x2={isIncoming ? colX.center : x} y2={isIncoming ? height / 2 : y + 20} className="g-edge" />
        <rect x={x} y={y} width={nodeW} height={40} rx="3" />
        <text x={x + 12} y={y + 26}>{truncate(item.title, 24)}</text>
      </g>
    )
  })
  return (
    <div className="mg-graph">
      <svg viewBox={`0 0 840 ${height}`} role="img" aria-label="Đồ thị liên kết của tài liệu đang mở">
        {side(incoming, colX.left, true)}
        {side(outgoing, colX.right, false)}
        <g className="g-node center">
          <rect x={colX.center} y={height / 2 - 24} width={nodeW - 60} height={48} rx="3" />
          <text x={colX.center + 12} y={height / 2 + 6}>{truncate(note.title, 18)}</text>
        </g>
      </svg>
      <p className="mg-muted">
        Trái: trang trỏ tới tài liệu này ({incoming.length}). Phải: trang tài liệu này trỏ tới ({outgoing.length}).
        {(incoming.length > show || outgoing.length > show) && ' Chỉ hiện tối đa 9 trang mỗi bên.'} Bấm vào một trang để mở.
      </p>
    </div>
  )
}

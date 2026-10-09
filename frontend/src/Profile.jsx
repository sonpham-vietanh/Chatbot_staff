import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Check, History, Plus, RotateCcw, Trash2 } from 'lucide-react'

const STATUS_LABELS = { active: 'Đang chạy', done: 'Hoàn thành', dropped: 'Đã bỏ' }

async function call(authFetch, path, options = {}) {
  const response = await authFetch(`/api${path}`, {
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

const currentQuarter = () => {
  const now = new Date()
  return `Q${Math.floor(now.getMonth() / 3) + 1}/${now.getFullYear()}`
}
const EMPTY_FORM = () => ({ title: '', period_label: currentQuarter(), metric_name: '', unit: '', start_value: '0', target_value: '', current_value: '', due_date: '', description: '' })
const formatDate = (value) => (value ? new Date(value).toLocaleDateString('vi-VN') : '')
const formatDateTime = (value) => (value ? new Date(value).toLocaleString('vi-VN') : '')
const formatNumber = (value) => Number(value).toLocaleString('vi-VN', { maximumFractionDigits: 2 })

function ProgressBar({ value }) {
  return (
    <div className="pf-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} aria-label="Tiến độ">
      <div className="pf-bar-fill" style={{ width: `${value}%` }} />
    </div>
  )
}

function WigCard({ wig, onChanged, notify, authFetch, readOnly = false }) {
  const [mode, setMode] = useState(null) // 'progress' | 'history'
  const [value, setValue] = useState(String(wig.current_value))
  const [note, setNote] = useState('')
  const [history, setHistory] = useState([])
  const [busy, setBusy] = useState(false)
  const unit = wig.unit ? ` ${wig.unit}` : ''

  async function run(action, success) {
    setBusy(true)
    try {
      await action()
      if (success) notify(success)
      await onChanged()
    } catch (error) {
      notify(error.message, true)
    } finally {
      setBusy(false)
    }
  }

  async function openHistory() {
    if (mode === 'history') return setMode(null)
    try {
      setHistory((await call(authFetch, `/wigs/${wig.id}/updates`)).items)
      setMode('history')
    } catch (error) {
      notify(error.message, true)
    }
  }

  const saveProgress = (event) => {
    event.preventDefault()
    run(async () => {
      await call(authFetch, `/me/wigs/${wig.id}/progress`, { method: 'POST', body: JSON.stringify({ value: Number(value), note: note.trim() || null }) })
      setMode(null)
      setNote('')
    }, 'Đã cập nhật tiến độ.')
  }
  const setStatus = (status, success) => run(() => call(authFetch, `/me/wigs/${wig.id}`, { method: 'PUT', body: JSON.stringify({ status }) }), success)

  return (
    <article className={`pf-wig ${wig.status !== 'active' ? 'closed' : ''}`}>
      <header className="pf-wig-head">
        <h3>{wig.title}</h3>
        <span className="pf-tag">{wig.period_label}</span>
        {wig.status !== 'active' && <span className="pf-tag dark">{STATUS_LABELS[wig.status]}</span>}
        {wig.overdue && <span className="pf-tag warn">Quá hạn</span>}
      </header>
      {wig.description && <p className="pf-muted">{wig.description}</p>}
      <div className="pf-wig-progress">
        <ProgressBar value={wig.progress} />
        <strong>{formatNumber(wig.progress)}%</strong>
      </div>
      <p className="pf-muted">
        {wig.metric_name}: <strong>{formatNumber(wig.current_value)}</strong> / {formatNumber(wig.target_value)}{unit}
        {wig.due_date ? ` · hạn ${formatDate(wig.due_date)}` : ''}
      </p>

      {mode === 'progress' && !readOnly && (
        <form className="pf-inline" onSubmit={saveProgress}>
          <label className="pf-field">Giá trị hiện tại{wig.unit ? ` (${wig.unit})` : ''}
            <input type="number" step="any" required value={value} onChange={(event) => setValue(event.target.value)} />
          </label>
          <label className="pf-field grow">Ghi chú (tuỳ chọn)
            <input maxLength={1000} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Ví dụ: đã xong 2/3 phòng" />
          </label>
          <button className="btn pf-btn dark" disabled={busy}>Lưu</button>
          <button type="button" className="btn-link" onClick={() => setMode(null)}>Huỷ</button>
        </form>
      )}

      {mode === 'history' && (
        <ul className="pf-history">
          {history.map((item) => (
            <li key={item.id}><strong>{formatNumber(item.value)}{unit}</strong> <span className="pf-muted">{formatDateTime(item.created_at)}{item.note ? ` — ${item.note}` : ''}</span></li>
          ))}
        </ul>
      )}

      <footer className="pf-actions">
        {!readOnly && wig.status === 'active' && (
          <button className="btn pf-btn outline" onClick={() => { setValue(String(wig.current_value)); setMode(mode === 'progress' ? null : 'progress') }}>Cập nhật tiến độ</button>
        )}
        <button className="pf-link" onClick={openHistory}><History size={16} /> Lịch sử</button>
        {!readOnly && wig.status === 'active' && <button className="pf-link" disabled={busy} onClick={() => setStatus('done', 'Đã đánh dấu hoàn thành.')}><Check size={16} /> Hoàn thành</button>}
        {!readOnly && wig.status !== 'active' && <button className="pf-link" disabled={busy} onClick={() => setStatus('active', 'Đã mở lại WIG.')}><RotateCcw size={16} /> Mở lại</button>}
        {!readOnly && wig.status !== 'dropped' && (
          <button className="pf-link danger" disabled={busy} onClick={() => window.confirm('Bỏ WIG này? Bạn vẫn xem lại được trong mục "WIG đã đóng".') && setStatus('dropped', 'Đã bỏ WIG.')}><Trash2 size={16} /> Bỏ</button>
        )}
      </footer>
    </article>
  )
}

export default function Profile({ authFetch: authFetchProp }) {
  const fetchRef = useRef(authFetchProp)
  fetchRef.current = authFetchProp
  const authFetch = useCallback((...args) => fetchRef.current(...args), [])
  const [profile, setProfile] = useState(null)
  const [wigs, setWigs] = useState([])
  const [team, setTeam] = useState([])
  const [tab, setTab] = useState('mine')
  const [showClosed, setShowClosed] = useState(false)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [own, setOwn] = useState({ phone: '', bio: '' })
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [toast, setToast] = useState(null)
  const toastTimer = useRef(null)

  const notify = useCallback((text, error = false) => {
    clearTimeout(toastTimer.current)
    setToast({ text, error })
    toastTimer.current = setTimeout(() => setToast(null), 5000)
  }, [])

  const loadWigs = useCallback(async () => {
    const data = await call(authFetch, `/me/wigs?include_closed=${showClosed}`)
    setWigs(data.items)
  }, [authFetch, showClosed])

  useEffect(() => {
    call(authFetch, '/me/profile')
      .then((data) => { setProfile(data); setOwn({ phone: data.phone || '', bio: data.bio || '' }); setLoadError('') })
      .catch((error) => setLoadError(error.message))
  }, [authFetch])
  useEffect(() => { loadWigs().catch((error) => setLoadError(error.message)) }, [loadWigs])
  useEffect(() => {
    if (profile?.has_team) call(authFetch, '/team/wigs').then((data) => setTeam(data.items)).catch(() => {})
  }, [authFetch, profile?.has_team])

  const refreshAll = useCallback(async () => {
    await loadWigs()
    call(authFetch, '/me/profile').then(setProfile).catch(() => {})
  }, [authFetch, loadWigs])

  async function saveOwn(event) {
    event.preventDefault()
    setBusy(true)
    try {
      await call(authFetch, '/me/profile', { method: 'PUT', body: JSON.stringify({ phone: own.phone, bio: own.bio }) })
      notify('Đã lưu thông tin của bạn.')
    } catch (error) {
      notify(error.message, true)
    } finally {
      setBusy(false)
    }
  }

  async function createWig(event) {
    event.preventDefault()
    setBusy(true)
    try {
      const body = {
        title: form.title, period_label: form.period_label, metric_name: form.metric_name, unit: form.unit || null,
        description: form.description || null, start_value: Number(form.start_value || 0), target_value: Number(form.target_value),
        current_value: form.current_value === '' ? null : Number(form.current_value), due_date: form.due_date || null,
      }
      await call(authFetch, '/me/wigs', { method: 'POST', body: JSON.stringify(body) })
      setForm(EMPTY_FORM())
      setAdding(false)
      notify('Đã thêm WIG.')
      await loadWigs()
    } catch (error) {
      notify(error.message, true)
    } finally {
      setBusy(false)
    }
  }

  const field = (key) => ({ value: form[key], onChange: (event) => setForm((current) => ({ ...current, [key]: event.target.value })) })
  const open = wigs.filter((wig) => wig.status === 'active')
  const closed = wigs.filter((wig) => wig.status !== 'active')
  const initial = (profile?.display_name || profile?.email || '?').trim().split(/\s+/).pop().slice(0, 1).toUpperCase()

  return (
    <div className="pf-shell">
      <header className="pf-top">
        <a href="/" className="pf-back"><ArrowLeft size={18} /> Trợ lý</a>
        <h1>Hồ sơ &amp; WIG</h1>
      </header>
      {loadError && <p className="pf-error" role="alert">{loadError}</p>}
      {profile && (
        <main className="pf-body">
          <aside className="pf-side">
            <section className="pf-card">
              <div className="pf-id">
                <div className="pf-avatar">{initial}</div>
                <div>
                  <h2>{profile.display_name || profile.email}</h2>
                  <p className="pf-muted">{[profile.job_title, profile.department].filter(Boolean).join(' · ') || 'Chưa có thông tin chức danh'}</p>
                </div>
              </div>
              {!profile.in_directory && <p className="pf-notice">Bạn chưa có trong danh bạ nhân sự nên một số thông tin đang trống. Nhờ HR bổ sung để hiển thị đầy đủ.</p>}
              <dl className="pf-facts">
                <div><dt>Email</dt><dd>{profile.email}</dd></div>
                {profile.campus && <div><dt>Cơ sở</dt><dd>{profile.campus}</dd></div>}
                {profile.employment_start_date && <div><dt>Vào công ty</dt><dd>{formatDate(profile.employment_start_date)}{profile.tenure ? ` (${profile.tenure})` : ''}</dd></div>}
                {profile.manager && <div><dt>Quản lý trực tiếp</dt><dd>{profile.manager.display_name || profile.manager.email}</dd></div>}
                {profile.work_phone && <div><dt>Điện thoại công việc</dt><dd>{profile.work_phone}</dd></div>}
              </dl>
              <form className="pf-own" onSubmit={saveOwn}>
                <label className="pf-field">Số điện thoại của bạn
                  <input maxLength={30} value={own.phone} onChange={(event) => setOwn({ ...own, phone: event.target.value })} />
                </label>
                <label className="pf-field">Giới thiệu ngắn
                  <textarea maxLength={500} rows={3} value={own.bio} onChange={(event) => setOwn({ ...own, bio: event.target.value })} placeholder="Bạn phụ trách việc gì?" />
                </label>
                <button className="btn pf-btn outline" disabled={busy}>Lưu thông tin</button>
              </form>
            </section>
            <section className="pf-card">
              <h2>Điểm đóng góp</h2>
              <p className="pf-points">{profile.points?.total ?? 0}</p>
              <p className="pf-muted">Bạn nhận điểm khi báo sai một câu trả lời và khi báo cáo được xác nhận, đã sửa. Hiện chỉ để ghi nhận.</p>
              {profile.points?.recent?.length > 0 && (
                <ul className="pf-history">
                  {profile.points.recent.map((item, index) => (
                    <li key={`${item.created_at}-${index}`}><strong>+{item.points}</strong> {item.label} <span className="pf-muted">{formatDate(item.created_at)}</span></li>
                  ))}
                </ul>
              )}
            </section>
          </aside>

          <section className="pf-main">
            {profile.has_team && (
              <div className="pf-tabs" role="tablist">
                <button role="tab" aria-selected={tab === 'mine'} className={tab === 'mine' ? 'active' : ''} onClick={() => setTab('mine')}>WIG của tôi</button>
                <button role="tab" aria-selected={tab === 'team'} className={tab === 'team' ? 'active' : ''} onClick={() => setTab('team')}>Nhóm của tôi</button>
              </div>
            )}

            {tab === 'mine' && (
              <>
                <div className="pf-section-head">
                  <div>
                    <h2>WIG của tôi</h2>
                    <p className="pf-muted">Mục tiêu trọng yếu bạn tự đặt và theo dõi. Hỏi trợ lý “WIG của tôi tới đâu rồi?” để nghe báo lại.</p>
                  </div>
                  {!adding && <button className="btn btn-cta pf-add" onClick={() => setAdding(true)}><Plus size={18} /> Thêm WIG</button>}
                </div>

                {adding && (
                  <form className="pf-card pf-form" onSubmit={createWig}>
                    <label className="pf-field wide">Tên WIG<input required maxLength={200} {...field('title')} placeholder="Ví dụ: Tăng tỷ lệ báo cáo đúng hạn" /></label>
                    <label className="pf-field">Kỳ<input required maxLength={40} {...field('period_label')} /></label>
                    <label className="pf-field">Hạn hoàn thành<input type="date" {...field('due_date')} /></label>
                    <label className="pf-field wide">Chỉ số đo<input required maxLength={120} {...field('metric_name')} placeholder="Ví dụ: Tỷ lệ báo cáo đúng hạn" /></label>
                    <label className="pf-field">Đơn vị<input maxLength={30} {...field('unit')} placeholder="%, học sinh, triệu đồng…" /></label>
                    <label className="pf-field">Giá trị đầu kỳ<input type="number" step="any" required {...field('start_value')} /></label>
                    <label className="pf-field">Mục tiêu<input type="number" step="any" required {...field('target_value')} /></label>
                    <label className="pf-field">Hiện tại (nếu khác đầu kỳ)<input type="number" step="any" {...field('current_value')} /></label>
                    <label className="pf-field wide">Mô tả (tuỳ chọn)<textarea rows={2} maxLength={2000} {...field('description')} /></label>
                    <div className="pf-actions wide">
                      <button className="btn pf-btn dark" disabled={busy}>Lưu WIG</button>
                      <button type="button" className="btn-link" onClick={() => { setAdding(false); setForm(EMPTY_FORM()) }}>Huỷ</button>
                    </div>
                  </form>
                )}

                {open.length === 0 && !adding && <p className="pf-empty">Bạn chưa có WIG nào đang chạy. Bấm “Thêm WIG” để bắt đầu.</p>}
                {open.map((wig) => <WigCard key={wig.id} wig={wig} authFetch={authFetch} notify={notify} onChanged={refreshAll} />)}

                <label className="pf-check">
                  <input type="checkbox" checked={showClosed} onChange={(event) => setShowClosed(event.target.checked)} /> Hiện WIG đã đóng (hoàn thành, đã bỏ)
                </label>
                {showClosed && closed.map((wig) => <WigCard key={wig.id} wig={wig} authFetch={authFetch} notify={notify} onChanged={refreshAll} />)}
              </>
            )}

            {tab === 'team' && (
              <>
                <div className="pf-section-head"><div><h2>Nhóm của tôi</h2><p className="pf-muted">WIG đang chạy của những người báo cáo trực tiếp cho bạn (chỉ xem).</p></div></div>
                {team.length === 0 && <p className="pf-empty">Chưa có ai trong nhóm của bạn.</p>}
                {team.map((person) => (
                  <section key={person.email} className="pf-person">
                    <h3>{person.display_name} <span className="pf-muted">· {person.job_title}</span></h3>
                    {person.wigs.length === 0 && <p className="pf-muted">Chưa có WIG nào đang chạy.</p>}
                    {person.wigs.map((wig) => <WigCard key={wig.id} wig={wig} authFetch={authFetch} notify={notify} onChanged={async () => {}} readOnly />)}
                  </section>
                ))}
              </>
            )}
          </section>
        </main>
      )}
      {toast && <div className={`pf-toast ${toast.error ? 'error' : ''}`} role="status">{toast.text}</div>}
    </div>
  )
}

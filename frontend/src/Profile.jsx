import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Check, History, Plus, RotateCcw, Trash2 } from 'lucide-react'
import { errorMessage, networkMessage } from './api.js'

const STATUS_LABELS = { active: 'Đang chạy', done: 'Hoàn thành', dropped: 'Đã bỏ' }
const OWN_KEYS = ['phone', 'zalo', 'birth_date', 'gender', 'address', 'education', 'expertise', 'bio', 'emergency_name', 'emergency_relation', 'emergency_phone']
const toOwn = (data) => Object.fromEntries(OWN_KEYS.map((key) => [key, data?.[key] || '']))

async function call(authFetch, path, options = {}) {
  let response
  try {
    response = await authFetch(`/api${path}`, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    })
  } catch (error) {
    throw new Error(networkMessage(error))
  }
  const raw = await response.text()
  let data = {}
  try { data = raw ? JSON.parse(raw) : {} } catch { data = { detail: raw } }
  if (!response.ok) {
    throw new Error(errorMessage(data, response.status))
  }
  return data
}

const REQ_STATUS = { pending: 'Chờ duyệt', approved: 'Đã duyệt', rejected: 'Từ chối', cancelled: 'Đã huỷ' }
const DEFAULT_REQUESTABLE = [
  { field: 'display_name', label: 'Họ tên', max_length: 100, type: 'text' },
  { field: 'job_title', label: 'Chức danh', max_length: 100, type: 'text' },
  { field: 'department', label: 'Phòng ban', max_length: 100, type: 'text' },
  { field: 'campus', label: 'Cơ sở', max_length: 100, type: 'text' },
  { field: 'work_phone', label: 'Điện thoại công việc', max_length: 30, type: 'text' },
  { field: 'employment_start_date', label: 'Ngày vào công ty', max_length: 10, type: 'date' },
  { field: 'team_name', label: 'Tổ chuyên môn', max_length: 100, type: 'text' },
  { field: 'extra_roles', label: 'Kiêm nhiệm', max_length: 200, type: 'text' },
  { field: 'subjects', label: 'Môn dạy', max_length: 200, type: 'text' },
  { field: 'grade_levels', label: 'Khối lớp phụ trách', max_length: 100, type: 'text' },
]

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
  const start = Number(wig.start_value)
  const current = Number(wig.current_value)
  const target = Number(wig.target_value)
  const decreasing = wig.start_value != null && target < start // chỉ số "càng thấp càng tốt"
  const reached = wig.progress >= 100 || (decreasing ? current <= target : current >= target)
  const exceeded = decreasing ? current < target : current > target

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
        {exceeded && <span className="pf-tag ok">Vượt mục tiêu</span>}
      </header>
      {wig.description && <p className="pf-muted">{wig.description}</p>}
      <div className="pf-wig-progress">
        <ProgressBar value={wig.progress} />
        <strong>{formatNumber(wig.progress)}%</strong>
      </div>
      <p className="pf-muted">
        {decreasing ? (
          <>{wig.metric_name} (càng thấp càng tốt): đầu kỳ <strong>{formatNumber(start)}</strong> → hiện tại <strong>{formatNumber(current)}</strong>, mục tiêu ≤ <strong>{formatNumber(target)}</strong>{unit}</>
        ) : (
          <>{wig.metric_name}: <strong>{formatNumber(current)}</strong> / {formatNumber(target)}{unit}</>
        )}
        {wig.due_date ? ` · hạn ${formatDate(wig.due_date)}` : ''}
      </p>
      {!readOnly && wig.status === 'active' && reached && (
        <div className="pf-reached" role="status">
          <span>Đã đạt mục tiêu — đóng WIG này?</span>
          <button type="button" className="btn pf-btn dark" disabled={busy} onClick={() => setStatus('done', 'Đã đánh dấu hoàn thành.')}>Đánh dấu hoàn thành</button>
        </div>
      )}

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

function FactRow({ label, display, spec, rawValue, pending, canRequest, onSubmit }) {
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    if (reason.trim().length < 5) { // backend yêu cầu tối thiểu 5 ký tự
      setError('Vui lòng nhập lý do (tối thiểu 5 ký tự).')
      setBusy(false)
      return
    }
    try {
      await onSubmit({ field: spec.field, new_value: value.trim(), reason: reason.trim() })
      setOpen(false)
      setValue('')
      setReason('')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <dt>{label}</dt>
      <dd>{display}</dd>
      {spec && canRequest && pending && <p className="pf-req-wait">Đang chờ HR duyệt</p>}
      {spec && canRequest && !pending && !open && (
        <button type="button" className="pf-link pf-req-btn" onClick={() => { setValue(spec.type === 'date' ? (rawValue || '') : ''); setError(''); setOpen(true) }}>Yêu cầu sửa</button>
      )}
      {spec && canRequest && !pending && open && (
        <form className="pf-req-form" onSubmit={submit}>
          <label className="pf-field">Giá trị mới
            <input type={spec.type === 'date' ? 'date' : 'text'} required maxLength={spec.type === 'date' ? undefined : spec.max_length} value={value} onChange={(event) => setValue(event.target.value)} />
          </label>
          <label className="pf-field">Lý do <span className="pf-required" aria-hidden="true">*</span> <span className="pf-muted">(bắt buộc, tối thiểu 5 ký tự)</span>
            <input required minLength={5} maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Ví dụ: đổi chức danh theo quyết định mới" />
          </label>
          {error && <p className="pf-req-error" role="alert">{error}</p>}
          <div className="pf-actions">
            <button className="btn pf-btn dark" disabled={busy}>Gửi</button>
            <button type="button" className="btn-link" onClick={() => setOpen(false)}>Huỷ</button>
          </div>
        </form>
      )}
    </div>
  )
}

function RequestList({ requests, labelOf, onCancel, busy }) {
  const shown = (v) => (v == null || v === '' ? '(trống)' : /^\d{4}-\d{2}-\d{2}$/.test(v) ? formatDate(v) : v)
  return (
    <section className="pf-card">
      <h2>Yêu cầu chỉnh sửa của tôi</h2>
      <ul className="pf-history">
        {requests.map((item) => (
          <li key={item.id} className="pf-req-item">
            <div><strong>{item.label || labelOf(item.field)}</strong> <span className={`pf-tag pf-st-${item.status}`}>{REQ_STATUS[item.status] || item.status}</span></div>
            <div>{shown(item.old_value)} → <strong>{shown(item.new_value)}</strong></div>
            <div className="pf-muted">Gửi ngày {formatDate(item.created_at)}{item.reason ? ` · Lý do: ${item.reason}` : ''}</div>
            {item.status === 'rejected' && item.review_note && <div className="pf-muted">Ghi chú của HR: {item.review_note}</div>}
            {item.status === 'pending' && <button type="button" className="pf-link danger" disabled={busy} onClick={() => onCancel(item.id)}>Huỷ yêu cầu</button>}
          </li>
        ))}
      </ul>
    </section>
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
  const [own, setOwn] = useState(() => toOwn(null))
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [agree, setAgree] = useState(false) // đã tick đồng ý trước khi lưu hồ sơ cá nhân
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
      .then((data) => { setProfile(data); setOwn(toOwn(data)); setLoadError('') })
      .catch((error) => setLoadError(error.message))
  }, [authFetch])
  useEffect(() => { loadWigs().catch((error) => setLoadError(error.message)) }, [loadWigs])
  useEffect(() => {
    if (profile?.has_team) call(authFetch, '/team/wigs').then((data) => setTeam(data.items)).catch(() => {})
  }, [authFetch, profile?.has_team])

  const reloadProfile = useCallback(() => call(authFetch, '/me/profile').then(setProfile).catch(() => {}), [authFetch])
  const refreshAll = useCallback(async () => {
    await loadWigs()
    call(authFetch, '/me/profile').then(setProfile).catch(() => {})
  }, [authFetch, loadWigs])

  async function saveOwn(event) {
    event.preventDefault()
    if (!profile?.consent_at && !agree) {
      notify('Bạn cần tick đồng ý trước khi lưu thông tin cá nhân.', true)
      return
    }
    setBusy(true)
    try {
      if (!profile?.consent_at) await recordConsent() // phải có consent trước khi PUT
      await call(authFetch, '/me/profile', { method: 'PUT', body: JSON.stringify({ ...own, birth_date: own.birth_date || null }) })
      notify('Đã lưu thông tin của bạn.')
    } catch (error) {
      notify(error.message, true)
    } finally {
      setBusy(false)
    }
  }

  async function sendRequest(body) {
    await call(authFetch, '/me/change-requests', { method: 'POST', body: JSON.stringify(body) })
    notify('Đã gửi yêu cầu, HR sẽ xem xét.')
    await reloadProfile()
  }

  async function cancelRequest(id) {
    setBusy(true)
    try {
      await call(authFetch, `/me/change-requests/${id}/cancel`, { method: 'POST' })
      notify('Đã huỷ yêu cầu.')
      await reloadProfile()
    } catch (error) {
      notify(error.message, true)
    } finally {
      setBusy(false)
    }
  }

  async function recordConsent() {
    const data = await call(authFetch, '/me/profile/consent', { method: 'POST' })
    setProfile((current) => ({ ...current, consent_at: data.consent_at || new Date().toISOString() }))
  }

  async function acceptConsent() {
    try {
      await recordConsent()
    } catch (error) {
      notify(error.message, true)
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

  const setOwnField = (key) => (event) => setOwn((current) => ({ ...current, [key]: event.target.value }))
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
      {profile && profile.in_directory === false && (
        <section className="pf-nodir" role="alert">
          Bạn chưa có trong danh bạ nhân sự nên chưa có thông tin công việc. Hãy liên hệ HR để được thêm vào.
        </section>
      )}
      {profile && !profile.consent_at && (
        <section className="pf-consent" aria-label="Thông báo mục đích sử dụng dữ liệu">
          <h2>Về dữ liệu hồ sơ của bạn</h2>
          <ul>
            <li>Dữ liệu hồ sơ dùng để làm danh bạ nội bộ và theo dõi WIG.</li>
            <li>Ngày sinh, địa chỉ, liên hệ khẩn cấp chỉ bạn và HR xem được.</li>
            <li>Thông tin liên hệ khẩn cấp là của người thân, nên bạn cần đã được họ đồng ý trước khi nhập.</li>
            <li>Bạn có thể để trống mọi trường tuỳ chọn, sửa hoặc xoá bất cứ lúc nào.</li>
            <li>HR chỉ sửa thông tin công việc và sẽ ghi lại lịch sử thay đổi.</li>
          </ul>
          <button type="button" className="btn pf-btn dark" onClick={acceptConsent}>Tôi đã hiểu</button>
        </section>
      )}
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
              <dl className="pf-facts">
                {(() => {
                  const specs = Object.fromEntries((profile.requestable_fields?.length ? profile.requestable_fields : DEFAULT_REQUESTABLE).map((item) => [item.field, item]))
                  const pendingFields = new Set((profile.requests || []).filter((item) => item.status === 'pending').map((item) => item.field))
                  const canRequest = profile.in_directory !== false && !!profile.requestable_fields // chưa vào danh bạ thì khoá form yêu cầu sửa
                  const row = (key, label, raw, display) => raw && (
                    <FactRow key={key} label={label} display={display || raw} rawValue={raw} spec={specs[key]} pending={pendingFields.has(key)} canRequest={canRequest} onSubmit={sendRequest} />
                  )
                  return [
                    row('display_name', 'Họ tên', profile.display_name),
                    row('job_title', 'Chức danh', profile.job_title),
                    row('department', 'Phòng ban', profile.department),
                    row('campus', 'Cơ sở', profile.campus),
                    row('employment_start_date', 'Vào công ty', profile.employment_start_date, profile.employment_start_date && `${formatDate(profile.employment_start_date)}${profile.tenure ? ` (${profile.tenure})` : ''}`),
                    row('work_phone', 'Điện thoại công việc', profile.work_phone),
                    row('team_name', 'Tổ chuyên môn', profile.team_name),
                    row('extra_roles', 'Kiêm nhiệm', profile.extra_roles),
                    row('subjects', 'Môn dạy', profile.subjects),
                    row('grade_levels', 'Khối lớp phụ trách', profile.grade_levels),
                  ]
                })()}
                <div><dt>Email</dt><dd>{profile.email}</dd></div>
                {profile.employee_code && <div><dt>Mã nhân viên</dt><dd>{profile.employee_code}</dd></div>}
                {profile.level_name && <div><dt>Cấp bậc</dt><dd>{profile.level_name}</dd></div>}
                {profile.contract_type && <div><dt>Loại hợp đồng</dt><dd>{profile.contract_type}</dd></div>}
                {(profile.employment_type_label || profile.employment_type) && <div><dt>Hình thức làm việc</dt><dd>{profile.employment_type_label || profile.employment_type}</dd></div>}
                {profile.manager && <div><dt>Quản lý trực tiếp</dt><dd>{profile.manager.display_name || profile.manager.email}</dd></div>}
              </dl>
            </section>
            {profile.requests?.length > 0 && <RequestList requests={profile.requests} labelOf={(f) => (profile.requestable_fields || DEFAULT_REQUESTABLE).find((x) => x.field === f)?.label || f} onCancel={cancelRequest} busy={busy} />}
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
            <div className="pf-tabs" role="tablist">
              <button role="tab" aria-selected={tab === 'mine'} className={tab === 'mine' ? 'active' : ''} onClick={() => setTab('mine')}>WIG của tôi</button>
              <button role="tab" aria-selected={tab === 'info'} className={tab === 'info' ? 'active' : ''} onClick={() => setTab('info')}>Thông tin cá nhân</button>
              {profile.has_team && <button role="tab" aria-selected={tab === 'team'} className={tab === 'team' ? 'active' : ''} onClick={() => setTab('team')}>Nhóm của tôi</button>}
            </div>

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

            {tab === 'info' && (
              <form className="pf-card pf-form" onSubmit={saveOwn}>
                <div className="wide">
                  <h2>Thông tin cá nhân</h2>
                  <p className="pf-optional">Các mục này không bắt buộc.</p>
                  <p className="pf-muted">Bạn tự cập nhật. Chỉ bạn và quản trị viên xem được, quản lý trực tiếp không xem. Thông tin công việc (phòng ban, chức danh, hợp đồng...) do HR quản lý nên không sửa ở đây.</p>
                </div>
                <label className="pf-field">Điện thoại<input maxLength={30} value={own.phone} onChange={setOwnField('phone')} /></label>
                <label className="pf-field">Zalo<input maxLength={40} value={own.zalo} onChange={setOwnField('zalo')} placeholder="Số hoặc tên Zalo" /></label>
                <label className="pf-field">Ngày sinh<input type="date" value={own.birth_date} onChange={setOwnField('birth_date')} /></label>
                <label className="pf-field">Giới tính
                  <select value={own.gender} onChange={setOwnField('gender')}>
                    <option value="">Không nêu</option>
                    <option value="nam">Nam</option>
                    <option value="nu">Nữ</option>
                    <option value="khac">Khác</option>
                  </select>
                </label>
                <label className="pf-field wide">Địa chỉ liên hệ<input maxLength={300} value={own.address} onChange={setOwnField('address')} /></label>
                <label className="pf-field wide">Trình độ học vấn<input maxLength={200} value={own.education} onChange={setOwnField('education')} placeholder="Ví dụ: Cử nhân Sư phạm Anh, ĐH Sư phạm TP.HCM" /></label>
                <label className="pf-field wide">Chuyên môn, kỹ năng<textarea rows={2} maxLength={500} value={own.expertise} onChange={setOwnField('expertise')} placeholder="Ví dụ: IELTS 7.5, tuyển dụng, Excel nâng cao" /></label>
                <label className="pf-field wide">Giới thiệu ngắn<textarea rows={3} maxLength={500} value={own.bio} onChange={setOwnField('bio')} placeholder="Bạn phụ trách việc gì?" /></label>
                <div className="wide"><h3 className="pf-sub">Người liên hệ khẩn cấp</h3></div>
                <label className="pf-field">Họ tên<input maxLength={100} value={own.emergency_name} onChange={setOwnField('emergency_name')} /></label>
                <label className="pf-field">Quan hệ<input maxLength={60} value={own.emergency_relation} onChange={setOwnField('emergency_relation')} placeholder="Vợ/chồng, bố/mẹ, anh/chị..." /></label>
                <label className="pf-field wide">Điện thoại<input maxLength={30} value={own.emergency_phone} onChange={setOwnField('emergency_phone')} /></label>
                {!profile.consent_at && (
                  <label className="pf-check wide">
                    <input type="checkbox" checked={agree} onChange={(event) => setAgree(event.target.checked)} />
                    <span>Tôi đã đọc mục “Về dữ liệu hồ sơ của bạn” và đồng ý để công ty lưu thông tin cá nhân này.</span>
                  </label>
                )}
                <div className="pf-actions wide"><button className="btn pf-btn dark" disabled={busy || (!profile.consent_at && !agree)}>Lưu thông tin</button></div>
              </form>
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

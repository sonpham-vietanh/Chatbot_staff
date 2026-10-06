import { Fragment } from 'react'

/** Hiển thị markdown (kiểu Obsidian) thành React element — KHÔNG dùng innerHTML nên nội dung do
 * leader soạn không thể chèn script. Hỗ trợ: tiêu đề, đoạn, **đậm**, *nghiêng*, `code`, danh sách lồng
 * nhau, bảng, trích dẫn/callout (> [!note]), đường kẻ, khối code, [liên kết](http...) và [[wiki-link]]. */

const INLINE = /(`[^`\n]+`)|(\[\[[^\]\n]+\]\])|(\[[^\]\n]+\]\((?:https?:\/\/|mailto:)[^\s)]+\))|(\*\*[^*\n]+\*\*)|(\*[^*\s][^*\n]*\*)|(_[^_\s][^_\n]*_)/g

export function renderInline(text, linkMap, onOpenLink, keyPrefix = 'i') {
  const out = []
  let last = 0
  let n = 0
  for (const match of text.matchAll(INLINE)) {
    if (match.index > last) out.push(text.slice(last, match.index))
    const token = match[0]
    const key = `${keyPrefix}-${n++}`
    if (match[1]) out.push(<code key={key} className="md-code">{token.slice(1, -1)}</code>)
    else if (match[2]) {
      const [target, alias] = token.slice(2, -2).split('|')
      const clean = target.split('#')[0].trim()
      const hit = linkMap?.[clean]
      out.push(hit
        ? <button key={key} type="button" className="md-wikilink" onClick={() => onOpenLink?.(hit)}>{(alias || clean).trim()}</button>
        : <span key={key} className="md-wikilink missing" title="Trang này chưa có trong kho tri thức">{(alias || clean).trim()}</span>)
    } else if (match[3]) {
      const [, label, url] = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
      out.push(<a key={key} href={url} target="_blank" rel="noopener noreferrer">{label}</a>)
    } else if (match[4]) out.push(<strong key={key}>{token.slice(2, -2)}</strong>)
    else out.push(<em key={key}>{token.slice(1, -1)}</em>)
    last = match.index + token.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

const isTableSep = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line) && line.includes('-')
const splitRow = (line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
const listMatch = (line) => line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/)

function buildList(lines, start, linkMap, onOpenLink, key) {
  const items = []
  let i = start
  while (i < lines.length) {
    const m = listMatch(lines[i])
    if (!m) break
    items.push({ indent: m[1].replace(/\t/g, '    ').length, ordered: /\d/.test(m[2]), text: m[3] })
    i++
  }
  const render = (from, base) => {
    const nodes = []
    let idx = from
    let ordered = items[from]?.ordered
    while (idx < items.length && items[idx].indent >= base) {
      if (items[idx].indent > base) { idx++; continue }
      const item = items[idx]
      let next = idx + 1
      while (next < items.length && items[next].indent > base) next++
      const nested = next > idx + 1 ? render(idx + 1, items[idx + 1].indent) : null
      nodes.push(
        <li key={`${key}-${idx}`}>{renderInline(item.text, linkMap, onOpenLink, `${key}-${idx}`)}{nested?.node}</li>,
      )
      idx = next
    }
    const Tag = ordered ? 'ol' : 'ul'
    return { node: <Tag className={ordered ? 'md-ol' : 'md-ul'}>{nodes}</Tag>, next: idx }
  }
  return { node: render(0, items[0].indent).node, next: i }
}

export function MarkdownView({ source, linkMap, onOpenLink }) {
  const lines = (source || '').replace(/\r\n?/g, '\n').split('\n')
  const blocks = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const key = `b${i}`
    if (!line.trim()) { i++; continue }
    if (/^```/.test(line.trim())) {
      const body = []
      i++
      while (i < lines.length && !/^```/.test(lines[i].trim())) body.push(lines[i++])
      i++
      blocks.push(<pre key={key} className="md-pre"><code>{body.join('\n')}</code></pre>)
      continue
    }
    const heading = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/)
    if (heading) {
      const Tag = `h${Math.min(heading[1].length + 1, 6)}`
      blocks.push(<Tag key={key} className={`md-h md-h${heading[1].length}`}>{renderInline(heading[2], linkMap, onOpenLink, key)}</Tag>)
      i++
      continue
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { blocks.push(<hr key={key} className="md-hr" />); i++; continue }
    if (line.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = splitRow(line)
      const rows = []
      i += 2
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(splitRow(lines[i++]))
      blocks.push(
        <div key={key} className="md-table-wrap">
          <table className="md-table">
            <thead><tr>{head.map((c, k) => <th key={k}>{renderInline(c, linkMap, onOpenLink, `${key}h${k}`)}</th>)}</tr></thead>
            <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, k) => <td key={k}>{renderInline(c, linkMap, onOpenLink, `${key}r${ri}c${k}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      )
      continue
    }
    if (/^\s*>/.test(line)) {
      const quote = []
      while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ''))
      const callout = quote[0].match(/^\[!(\w+)\]\s*(.*)$/)
      blocks.push(
        <blockquote key={key} className={callout ? 'md-callout' : 'md-quote'}>
          {callout && <strong className="md-callout-title">{callout[2] || callout[1]}</strong>}
          {(callout ? quote.slice(1) : quote).map((q, k) => <p key={k}>{renderInline(q, linkMap, onOpenLink, `${key}q${k}`)}</p>)}
        </blockquote>,
      )
      continue
    }
    if (listMatch(line)) {
      const { node, next } = buildList(lines, i, linkMap, onOpenLink, key)
      blocks.push(<Fragment key={key}>{node}</Fragment>)
      i = next
      continue
    }
    const para = []
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|\s*>)/.test(lines[i]) && !listMatch(lines[i])) para.push(lines[i++])
    blocks.push(<p key={key} className="md-p">{para.map((p, k) => <Fragment key={k}>{k > 0 && <br />}{renderInline(p, linkMap, onOpenLink, `${key}p${k}`)}</Fragment>)}</p>)
  }
  return <div className="md">{blocks}</div>
}

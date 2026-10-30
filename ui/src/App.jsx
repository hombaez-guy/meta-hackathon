import React, { useEffect, useMemo, useState } from 'react'
import { api } from './api.js'

const fmt = (t) => (t || '').slice(0, 16).replace('T', ' ')
const RANGES = [['6h', '6h'], ['24h', '24h'], ['7d', '7d'], ['all', 'All']]
const RANGE_HOURS = { '6h': 6, '24h': 24, '7d': 168, all: 0 }
// Axis label: time of day when the window is short, date + time once it spans days
const tick = (t, spanHours) => {
  const d = new Date(t + 'Z')
  const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return spanHours <= 48 ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`
}

export default function App() {
  const [health, setHealth] = useState(null)
  const [conns, setConns] = useState({ server: null, muse: null })
  const [reports, setReports] = useState([])
  const [metrics, setMetrics] = useState([])
  const [selected, setSelected] = useState(null)
  const [report, setReport] = useState(null)
  const [busy, setBusy] = useState('')
  const [toast, setToast] = useState('')
  const [range, setRange] = useState('24h')
  // Reports carry UTC timestamps; keep whatever falls inside the chosen window
  const shown = useMemo(() => {
    const hours = RANGE_HOURS[range]
    if (!hours) return metrics
    const cutoff = Date.now() - hours * 3600 * 1000
    return metrics.filter((d) => Date.parse(d.t + 'Z') >= cutoff)
  }, [metrics, range])

  const refresh = async () => {
    const [h, c, r, m] = await Promise.all([api.health(), api.connections(), api.reports(), api.metrics()])
    setHealth(h); setConns(c); setReports(r); setMetrics(m)
    const id = selected || r[0]?.report_id
    if (id) { setSelected(id); setReport(await api.report(id)) }
  }
  useEffect(() => { refresh().catch((e) => setToast(e.message)) }, [])
  useEffect(() => { if (selected) api.report(selected).then(setReport).catch(() => {}) }, [selected])

  const say = (m) => { setToast(m); setTimeout(() => setToast(''), 4000) }

  const run = async () => {
    setBusy('run')
    try { const r = await api.run(); say(`Report ${r.report_id} written: ${r.status}`); setSelected(r.report_id); await refresh() }
    catch (e) { say(e.message) } finally { setBusy('') }
  }
  const apply = async (fix) => {
    if (!confirm(`Apply "${fix.title}"? Risk ${fix.risk}. ${fix.steps.length} commands will run on the server.`)) return
    setBusy(fix.fix_id)
    try { const f = await api.apply(fix.fix_id); say(`${f.state.toUpperCase()}: ${f.title}`); setReport(await api.report(selected)); setReports(await api.reports()) }
    catch (e) { say(e.message) } finally { setBusy('') }
  }
  const rerun = async (fix) => {
    setBusy(fix.fix_id)
    try { const f = await api.rerun(fix.fix_id); say(`New proposal (attempt ${f.attempt}): ${f.title}`); setReport(await api.report(selected)) }
    catch (e) { say(e.message) } finally { setBusy('') }
  }

  return (
    <div className="shell">
      <Sidebar conns={conns} health={health} onConns={setConns} say={say} />
      <main>
        <header className="top">
          <div>
            <h1>Foreman</h1>
            <div className="sub">Agents watch the server. You approve the fix.</div>
          </div>
          <button className="primary" onClick={run} disabled={busy === 'run'}>{busy === 'run' ? 'Reviewing…' : 'Run review now'}</button>
        </header>

        <div className="row">
          <section className="card reports">
            <h2>Reports</h2>
            {reports.length === 0 && <div className="empty">No reports yet. Drop Chip and Chuck JSON in data/inbox and run a review.</div>}
            <ul>
              {reports.map((r) => (
                <li key={r.report_id} className={r.report_id === selected ? 'sel' : ''} onClick={() => setSelected(r.report_id)}>
                  <span className={`dot ${r.status}`} />
                  <span className="when">{fmt(r.created_at)}</span>
                  <span className="hl">{r.headline}</span>
                  <span className="count">{r.findings} finding{r.findings === 1 ? '' : 's'}{r.open_fixes ? ` · ${r.open_fixes} to approve` : ''}</span>
                  <button className="view" title="Open the one-page report in a new tab" onClick={(e) => { e.stopPropagation(); window.open(`/api/reports/${r.report_id}/html`, '_blank', 'noopener') }}>View</button>
                </li>
              ))}
            </ul>
          </section>
          <section className="card graphs">
            <div className="range">
              {RANGES.map(([k, l]) => <button key={k} className={range === k ? 'on' : ''} onClick={() => setRange(k)}>{l}</button>)}
            </div>
            <Graph title="CPU · Memory · Disk (%)" series={[['cpu', 'CPU'], ['memory', 'Memory'], ['disk', 'Disk']]} data={shown} max={100} />
            <Graph title="Findings per report" series={[['findings', 'Findings']]} data={shown} bars />
          </section>
        </div>

        <div className="row">
          <LogSearch />
          <section className="card status">
            <h2>Latest status</h2>
            {report ? (
              <>
                <div className={`badge ${report.review.overall_status}`}>{report.review.overall_status}</div>
                <div className="hl2">{report.review.headline}</div>
                <p>{report.review.summary}</p>
                <div className="tiles">
                  <Tile k="CPU" v={report.chip.cpu_percent} /><Tile k="Memory" v={report.chip.memory_percent} /><Tile k="Disk" v={report.chip.disk_percent} />
                </div>
                <div className="actions">
                  <button className="primary" onClick={() => window.open(`/api/reports/${report.report_id}/html`, '_blank', 'noopener')}>View report</button>
                  <span className="hint">Opens the one-page report in a new tab.</span>
                </div>
              </>
            ) : <div className="empty">Select a report.</div>}
          </section>
        </div>

        <section className="card solutions">
          <h2>Solutions</h2>
          {!report && <div className="empty">Select a report.</div>}
          {report && report.review.findings.length === 0 && <div className="clear">{report.review.no_issues_statement}</div>}
          {report && report.review.findings.map((f) => (
            <div key={f.finding_id} className={`sol ${f.severity}`}>
              <div className="sol-issue">
                <div className="sol-h"><b>{f.title}</b><span className="sev">{f.severity} · {f.source}{f.trend ? ` · ${f.trend}` : ''}</span></div>
                <div className="muted"><span>Issue</span> {f.evidence}</div>
                <div className="muted"><span>If ignored</span> {f.impact}</div>
              </div>
              {f.fix ? (
                <div className="sol-fix">
                  <div className="sol-h"><b>Solution: {f.fix.title}</b><span className={`state ${f.fix.state}`}>{f.fix.state} · risk {f.fix.risk} · attempt {f.fix.attempt}</span></div>
                  <div>{f.fix.explanation}</div>
                  <ol>{f.fix.steps.map((s, i) => <li key={i}>{s.description} <code>{s.command}</code></li>)}</ol>
                  {f.fix.side_effects?.length > 0 && <div className="muted"><span>Side effects</span> {f.fix.side_effects.join('; ')}</div>}
                  {f.fix.rollback && <div className="muted"><span>Rollback</span> {f.fix.rollback}</div>}
                  {f.fix.conflicts_with_previous && <div className="muted warn"><span>Vs previous</span> {f.fix.conflicts_with_previous}</div>}
                  {f.fix.result && <details><summary>Execution output</summary><pre>{f.fix.result.slice(-1200)}</pre></details>}
                  <div className="actions">
                    <button className="primary" disabled={f.fix.state !== 'proposed' || busy === f.fix.fix_id} onClick={() => apply(f.fix)}>
                      {busy === f.fix.fix_id ? 'Working…' : 'Apply'}
                    </button>
                    <button disabled={f.fix.state === 'applied' || busy === f.fix.fix_id} onClick={() => rerun(f.fix)}>Rerun</button>
                  </div>
                </div>
              ) : f.needs_fix ? <div className="sol-fix muted">Fix pending.</div> : <div className="sol-fix muted">No command-level fix. Observation only.</div>}
            </div>
          ))}
        </section>
      </main>
      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}

function Sidebar({ conns, health, onConns, say }) {
  const mongoOn = health?.mongo === 'connected'
  const keySet = conns.muse?.api_key_set
  return (
    <aside>
      <div className="logo"><span className="mark">F</span><div><b>Foreman</b><div className="sub">by YOR.AI</div></div></div>
      <div className="panel">
        <h3>This server <span className={`pill ${health ? 'on' : ''}`}>{health ? 'watching' : 'offline'}</span></h3>
        <div className="kv"><span>Host</span><b>{health?.server || '…'}</b></div>
        <div className="kv"><span>Data</span><b>{mongoOn ? `Mongo · ${health.mongo_db}` : health?.mongo === 'unreachable' ? 'Mongo down · files' : 'Files'}</b></div>
        <div className="kv"><span>Reviews</span><b>{health?.review_interval_min ? `every ${health.review_interval_min} min` : 'manual'}</b></div>
      </div>
      <div className="panel">
        <h3>Muse <span className={`pill ${health && !health.mock && keySet ? 'on' : health?.mock ? 'mock' : ''}`}>{health?.mock ? 'mock' : keySet ? 'connected' : 'no key'}</span></h3>
        <div className="kv"><span>Reviewer</span><b>{health?.reviewer || '…'}</b></div>
        <div className="kv"><span>Fixer</span><b>{health?.fixer || '…'}</b></div>
        <div className="hint">Set in .env as REVIEWER_MODEL, FIXER_MODEL and MODEL_API_KEY.</div>
      </div>
      <div className="user"><span className="avatar">N</span><div><b>Admin</b><div className="sub">approver</div></div></div>
    </aside>
  )
}

function Tile({ k, v }) {
  const c = v > 90 ? 'crit' : v > 75 ? 'warn' : 'ok'
  return <div className="tile"><div className="k">{k}</div><div className="v">{Math.round(v)}%</div><div className="bar"><i className={c} style={{ width: `${Math.min(v, 100)}%` }} /></div></div>
}

function LogSearch() {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState([])
  const search = async (e) => { e?.preventDefault(); if (q.trim()) setHits(await api.logs(q.trim())) }
  return (
    <section className="card logs">
      <h2>Log search</h2>
      <form onSubmit={search}><input placeholder="Search the scraped logs (e.g. timeout, app.log)" value={q} onChange={(e) => setQ(e.target.value)} /><button type="submit">Search</button></form>
      <div className="results">
        {hits.length === 0 && <div className="empty">Results appear here.</div>}
        {hits.map((h, i) => (
          <div key={i} className={`hit ${h.level}`}><span className="lvl">{h.level}</span><span className="when">{fmt(h.date)}</span><b>{h.source}</b> {h.message}{h.count > 1 ? ` (×${h.count})` : ''}</div>
        ))}
      </div>
    </section>
  )
}

function Graph({ title, series, data, max, bars }) {
  const W = 480, H = 150, P = { l: 30, r: 8, t: 10, b: 20 }
  const n = data.length
  const top = max ?? Math.max(1, ...data.flatMap((d) => series.map(([k]) => d[k] ?? 0)))
  const x = (i) => P.l + (n <= 1 ? (W - P.l - P.r) / 2 : (i * (W - P.l - P.r)) / (n - 1))
  const y = (v) => H - P.b - ((v / top) * (H - P.t - P.b))
  const colors = ['var(--c1)', 'var(--c2)', 'var(--c3)']
  const ticks = max ? [0, 25, 50, 75, 100] : [0, top]
  const span = n > 1 ? (Date.parse(data[n - 1].t + 'Z') - Date.parse(data[0].t + 'Z')) / 3600000 : 0
  const every = Math.max(1, Math.ceil(n / 6))
  return (
    <div className="graph">
      <div className="gh"><span>{title}</span><span className="legend">{series.map(([k, l], i) => <i key={k} style={{ color: colors[i] }}>■ {l}</i>)}</span></div>
      <svg viewBox={`0 0 ${W} ${H}`}>
        {ticks.map((t) => <g key={t}><line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} className="grid" /><text x={P.l - 4} y={y(t) + 3} textAnchor="end" className="tick">{t}</text></g>)}
        {max && <line x1={P.l} x2={W - P.r} y1={y(75)} y2={y(75)} className="thr" />}
        {n === 0 && <text x={W / 2} y={H / 2} textAnchor="middle" className="tick">no data yet</text>}
        {series.map(([k], si) => bars
          ? data.map((d, i) => <rect key={i} x={x(i) - 6} y={y(d[k] ?? 0)} width="12" height={Math.max(0, H - P.b - y(d[k] ?? 0))} fill={colors[si]} rx="2" />)
          : <g key={k}><polyline points={data.map((d, i) => `${x(i)},${y(d[k] ?? 0)}`).join(' ')} fill="none" stroke={colors[si]} strokeWidth="2" />
              {data.map((d, i) => <circle key={i} cx={x(i)} cy={y(d[k] ?? 0)} r="2.5" fill={colors[si]} />)}</g>)}
        {data.map((d, i) => (i % every === 0 || i === n - 1) && (i === n - 1 || i + every <= n - 1 || n <= 6) && <text key={i} x={x(i)} y={H - 6} textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'} className="tick">{tick(d.t, span)}</text>)}
      </svg>
    </div>
  )
}

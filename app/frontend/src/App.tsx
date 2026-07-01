import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import {
  Package, Cpu, Cable, Database, FolderInput, Plug, RefreshCw,
  Trash2, Download, FileCog, Save, Search, Check, X, TriangleAlert, Circle,
  Sun, Moon, GitBranch,
} from 'lucide-react'
import './App.css'

type View = 'library' | 'pins' | 'netclasses' | 'database'

export const API_BASE = import.meta.env.DEV ? '' : 'http://127.0.0.1:8799'
export const api = (p: string) => `${API_BASE}${p}`
async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(api(url))
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json() as Promise<T>
}

/* ── toasts ─────────────────────────────────────────────── */
type Kind = 'ok' | 'bad' | 'info'
const ToastCtx = createContext<(msg: string, kind?: Kind) => void>(() => {})
const useToast = () => useContext(ToastCtx)

/* ── shared bits ────────────────────────────────────────── */
function Metric({ label, value, tone, icon }: { label: string; value: React.ReactNode; tone?: string; icon?: React.ReactNode }) {
  return (
    <div className={`metric ${tone ?? ''}`}>
      <div className="v">{value}</div>
      <div className="l">{icon}{label}</div>
    </div>
  )
}
function Header({ title, sub, children }: { title: string; sub: string; children?: React.ReactNode }) {
  return (
    <div className="row" style={{ justifyContent: 'space-between', marginBottom: 4 }}>
      <div>
        <h1 className="page">{title}</h1>
        <div className="page-sub">{sub}</div>
      </div>
      <div className="row">{children}</div>
    </div>
  )
}

/* ── types ──────────────────────────────────────────────── */
interface PackageInfo { package: string; mcus: number }
interface SwitchPin { pin: number; side: string; switch_class: string; conflict_roles: string
  routes_to: string; required_cell: string; minority_roles: string[] }
interface SwitchReport { package: string; must_switch: number; osc_optional: number; fixed: number
  adg714_cells: number; pins: SwitchPin[] }
interface MatrixPin { pin: number; side: string; pin_name: string; gpio: string
  roles: string; stability: string; needs_switch: boolean; required_cell: string }
interface PinMatrix { pins: MatrixPin[] }
interface NetClass { netclass: string; color?: string; track?: string | number; clearance?: number; members?: string[]; [k: string]: unknown }
interface DbStatus { cubemx_source: string; source_present: boolean; xml_files: number
  database: string; database_present: boolean; mcu_count: number }

/* ── Library (3-column: Workflow | Contents | Git/Log) ──── */
interface TreeItem { type: 'symbol' | 'footprint' | 'model'; name: string; location: string; date: string; dup: boolean; footprint?: string; ok?: boolean }
interface GitStatus { repo: boolean; branch?: string; ahead?: number; behind?: number; dirty?: boolean; changed_files?: number }
interface Commit { hash: string; short: string; subject: string; author: string; when: string }

function LibraryView() {
  const toast = useToast()
  const [tree, setTree] = useState<TreeItem[]>([])
  const [paths, setPaths] = useState<{ repo: string; libs: string; downloads: string } | null>(null)
  const [git, setGit] = useState<GitStatus | null>(null)
  const [commits, setCommits] = useState<Commit[]>([])
  const [selCommit, setSelCommit] = useState<string>('')
  const [diff, setDiff] = useState('')
  const [busy, setBusy] = useState(false)
  const [log, setLog] = useState<string[]>([])
  const [rtab, setRtab] = useState<'activity' | 'log'>('activity')
  const [over, setOver] = useState(false)
  const [processOnDrop, setPOD] = useState(true)
  const [fmt, setFmt] = useState<'all' | 'symbol' | 'footprint' | 'model'>('all')
  const [q, setQ] = useState('')
  const [dupOnly, setDupOnly] = useState(false)
  const [sel, setSel] = useState<TreeItem | null>(null)

  const logLine = (m: string) => setLog((l) => [`${new Date().toLocaleTimeString()}  ${m}`, ...l].slice(0, 200))
  const refresh = () => {
    getJSON<TreeItem[]>('/api/library/tree').then(setTree).catch((e) => toast(String(e), 'bad'))
  }
  const refreshGit = () => {
    getJSON<GitStatus>('/api/git/status').then(setGit).catch(() => {})
    getJSON<Commit[]>('/api/git/commits?n=40').then(setCommits).catch(() => {})
  }
  useEffect(() => { refresh(); refreshGit(); getJSON<{ repo: string; libs: string; downloads: string }>('/api/paths').then(setPaths).catch(() => {}) }, [])

  const op = async (path: string, ok: (j: Record<string, unknown>) => string, body?: unknown) => {
    setBusy(true)
    try {
      const r = await fetch(api(path), { method: 'POST', headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
      const j = await r.json()
      const msg = r.ok ? ok(j) : `Error: ${j.detail}`
      toast(msg, r.ok ? 'ok' : 'bad'); logLine(msg); refresh()
    } catch (e) { toast(String(e), 'bad'); logLine(String(e)) } finally { setBusy(false) }
  }
  const importFile = async (file: File) => {
    setBusy(true); logLine(`Importing ${file.name}…`)
    try {
      const fd = new FormData(); fd.append('file', file)
      const r = await fetch(api('/api/library/import'), { method: 'POST', body: fd })
      const j = await r.json()
      const msg = r.ok ? `Imported ${file.name}: ${j.symbols?.length ?? 0} symbol(s)` : `Error: ${j.detail}`
      toast(msg, r.ok ? 'ok' : 'bad'); logLine(msg); refresh()
    } catch (e) { toast(String(e), 'bad') } finally { setBusy(false) }
  }
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setOver(false)
    const files = Array.from(e.dataTransfer.files).filter((f) => f.name.endsWith('.zip'))
    if (!files.length) { toast('Drop .zip part files', 'bad'); return }
    if (processOnDrop) files.forEach(importFile)
    else toast(`${files.length} file(s) — enable “Process on drop” to import`, 'info')
  }

  const gitOp = async (path: string, label: string, body?: unknown) => {
    setBusy(true); logLine(`git: ${label}…`)
    try {
      const r = await fetch(api(path), { method: 'POST', headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
      const j = await r.json()
      const msg = j.ok ? `git ${label} ok` : `git ${label} failed: ${(j.output || j.detail || '').slice(0, 120)}`
      toast(msg, j.ok ? 'ok' : 'bad'); logLine(j.output || msg); refreshGit(); refresh()
    } catch (e) { toast(String(e), 'bad') } finally { setBusy(false) }
  }
  const commitPush = async () => {
    const m = window.prompt('Commit message:'); if (!m) return
    await gitOp('/api/git/commit', 'commit', { message: m })
    await gitOp('/api/git/push', 'push')
  }
  const openPath = (p?: string) => { if (p) fetch(api('/api/open'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: p }) }) }
  const showDiff = (ref: string) => { setSelCommit(ref); fetch(api(`/api/git/diff/${ref}`)).then((r) => r.text()).then(setDiff) }
  const del = () => { if (!sel) return; op('/api/library/remove', () => `Removed ${sel.name}`, { name: sel.name, remove_footprint: true }); setSel(null) }

  const shown = tree.filter((t) => (fmt === 'all' || t.type === fmt) && (!dupOnly || t.dup) && (t.name.toLowerCase().includes(q.toLowerCase())))
  const counts = { symbol: tree.filter((t) => t.type === 'symbol').length, footprint: tree.filter((t) => t.type === 'footprint').length, model: tree.filter((t) => t.type === 'model').length, dup: tree.filter((t) => t.dup).length }

  return (
    <>
      <div className="dropzone" onClick={() => document.getElementById('lib-file')?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)} onDrop={onDrop}
        style={over ? { borderColor: 'var(--accent)', background: 'var(--btn)' } : undefined}>
        <b>Drop part .zip files here</b> or click to browse — {' '}
        <label className="chk" onClick={(e) => e.stopPropagation()}><input type="checkbox" checked={processOnDrop} onChange={(e) => setPOD(e.target.checked)} />Process on drop</label>
        <input id="lib-file" type="file" accept=".zip" multiple hidden onChange={(e) => { Array.from(e.target.files || []).forEach(importFile); e.target.value = '' }} />
      </div>

      <div className="lib">
        {/* Workflow */}
        <div className="card"><div className="ct"><b>Workflow</b></div><div className="cb">
          <div className="steps">
            <button className="btn" disabled={busy} onClick={() => gitOp('/api/git/pull', 'pull')}><span className="step-n">0</span>Pull (fast-forward)</button>
            <button className="btn" disabled={busy} onClick={() => openPath(paths?.downloads)}><span className="step-n">1</span>Open downloads</button>
            <button className="btn" disabled={busy} onClick={() => op('/api/library/process-downloads', (j) => `Imported ${(j.imported as string[]).length}, cleared ${(j.cleared as string[]).length}`)}><span className="step-n">2</span>Process ZIPs</button>
            <button className="btn" disabled={busy} onClick={() => op('/api/library/dedupe', (j) => `Removed ${j.removed} duplicate(s)`)}><span className="step-n">3</span>Remove duplicates</button>
            <button className="btn" disabled={busy} onClick={commitPush}><span className="step-n">4</span>Stage, commit &amp; push</button>
          </div>
          <div className="section" style={{ margin: '14px 0 0' }}>
            <h3>Advanced</h3>
            <div className="steps">
              <button className="btn" disabled={busy} onClick={() => op('/api/library/register?dry_run=false', (j) => j.changed ? 'Registered in KiCad' : 'Already registered')}><Plug size={14} />Register in KiCad</button>
              <button className="btn ghost" onClick={() => { refresh(); refreshGit() }}><RefreshCw size={14} />Refresh</button>
            </div>
          </div>
          <div className="paths">
            <button className="btn ghost sm" title={paths?.repo} onClick={() => openPath(paths?.repo)}><FolderInput size={13} />Root</button>
            <button className="btn ghost sm" title={paths?.libs} onClick={() => openPath(paths?.libs)}><Package size={13} />Libs</button>
          </div>
        </div></div>

        {/* Contents */}
        <div className="card"><div className="ct"><b>Contents</b><span className="dim" style={{ fontSize: 12 }}>· {shown.length}</span><span className="grow" /><span className="search"><Search size={14} /><input type="text" placeholder="Filter…" value={q} onChange={(e) => setQ(e.target.value)} /></span></div><div className="cb">
          <div className="row" style={{ marginBottom: 8 }}>
            <select value={fmt} onChange={(e) => setFmt(e.target.value as typeof fmt)}>
              <option value="all">All types ({tree.length})</option>
              <option value="symbol">Symbols ({counts.symbol})</option>
              <option value="footprint">Footprints ({counts.footprint})</option>
              <option value="model">3D models ({counts.model})</option>
            </select>
            <label className="chk"><input type="checkbox" checked={dupOnly} onChange={(e) => setDupOnly(e.target.checked)} />Duplicates only ({counts.dup})</label>
            <span className="grow" />
            <button className="btn ghost sm" disabled={!sel} onClick={() => openPath(sel?.location)}><FolderInput size={13} />Open</button>
            <button className="btn danger sm" disabled={!sel} onClick={del}><Trash2 size={13} />Delete</button>
          </div>
          <div className="tree-wrap">
            <table className="tbl">
              <thead><tr><th style={{ width: 78 }}>Type</th><th>Name</th><th>Location</th><th style={{ width: 108 }}>Date</th></tr></thead>
              <tbody>
                {shown.map((t, i) => (
                  <tr key={t.type + t.name + i} className={sel === t ? 'sel' : ''} onClick={() => setSel(t)} style={{ cursor: 'pointer' }}>
                    <td><span className={`ftype ${t.type}`}>{t.type.slice(0, 3)}</span></td>
                    <td>{t.name}{t.dup && <span className="tag" style={{ marginLeft: 6 }}>dup</span>}</td>
                    <td className="mono dim" style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.location}</td>
                    <td className="dim">{t.date}</td>
                  </tr>
                ))}
                {!shown.length && <tr><td colSpan={4}><div className="empty">No items match.</div></td></tr>}
              </tbody>
            </table>
          </div>
          <div className="prev">
            {sel?.type === 'footprint'
              ? <div className="row"><img className="thumb" style={{ width: 90, height: 90 }} src={api(`/api/library/footprint/${encodeURIComponent(sel.name)}/svg`)} alt={sel.name} /><div className="info"><b>{sel.name}</b><br />footprint<br />{sel.location}</div></div>
              : sel ? <div className="info"><b>{sel.name}</b> — {sel.type}<br />{sel.location}<br />{sel.date}</div>
              : <div className="info">Select an item to preview.</div>}
          </div>
        </div></div>

        {/* Git / Log */}
        <div className="card"><div className="ct">
          <div className="seg"><button className={rtab === 'activity' ? 'on' : ''} onClick={() => setRtab('activity')}>Activity</button><button className={rtab === 'log' ? 'on' : ''} onClick={() => setRtab('log')}>Log</button></div>
          <span className="grow" />
          {git?.repo && <span className="pill"><GitBranch size={12} />{git.branch} {git.ahead ? `↑${git.ahead}` : ''}{git.behind ? `↓${git.behind}` : ''}</span>}
        </div><div className="cb">
          {rtab === 'activity' ? <>
            <div className="row" style={{ marginBottom: 6 }}>
              <button className="btn sm" disabled={busy} onClick={() => gitOp('/api/git/pull', 'pull')}>Pull</button>
              <button className="btn sm" disabled={busy} onClick={() => gitOp('/api/git/push', 'push')}>Push</button>
              <button className="btn sm" disabled={!selCommit} onClick={() => selCommit && showDiff(selCommit)}>Diff</button>
              <button className="btn sm" disabled={!selCommit} onClick={() => selCommit && window.confirm(`Checkout ${selCommit.slice(0, 7)}?`) && gitOp('/api/git/checkout', 'checkout', { ref: selCommit })}>Checkout</button>
              <button className="btn ghost sm" onClick={refreshGit}><RefreshCw size={13} /></button>
            </div>
            {git?.dirty && <div className="banner" style={{ margin: '0 0 8px' }}><TriangleAlert size={14} /><span>{git.changed_files} uncommitted change(s)</span></div>}
            <div className="commits">
              {commits.map((c) => (
                <div key={c.hash} className={`commit ${selCommit === c.hash ? 'sel' : ''}`} onClick={() => showDiff(c.hash)}>
                  <div className="s">{c.subject}</div>
                  <div className="m">{c.short} · {c.author} · {c.when}</div>
                </div>
              ))}
              {!commits.length && <div className="empty">No commits.</div>}
            </div>
            {diff && <pre className="diff">{diff.slice(0, 4000)}</pre>}
          </> : <div className="commits" style={{ fontFamily: 'var(--mono)', fontSize: 11.5 }}>{log.length ? log.map((l, i) => <div key={i} style={{ padding: '2px 0', color: 'var(--dim)' }}>{l}</div>) : <div className="empty">No activity yet.</div>}</div>}
        </div></div>
      </div>
    </>
  )
}

/* ── Pins ───────────────────────────────────────────────── */
function PinoutMap({ pins, selected, onSelect }: { pins: MatrixPin[]; selected: number | null; onSelect: (p: number) => void }) {
  if (!pins.length) return null
  const sides: Record<string, MatrixPin[]> = { Left: [], Bottom: [], Right: [], Top: [] }
  pins.forEach((p) => { (sides[p.side] || sides.Left).push(p) })
  Object.values(sides).forEach((a) => a.sort((x, y) => x.pin - y.pin))
  const B0 = 88, B1 = 272, len = B1 - B0
  const gap = (n: number) => len / (n + 1)
  const color = (p: MatrixPin) => (p.needs_switch ? '#e0805a' : '#5f6673')
  const dot = (p: MatrixPin, x: number, y: number) => (
    <g key={p.pin} onClick={() => onSelect(p.pin)} style={{ cursor: 'pointer' }}>
      <rect x={x - 5.5} y={y - 5.5} width={11} height={11} rx={2} fill={color(p)}
        stroke={selected === p.pin ? '#e7eaf0' : 'transparent'} strokeWidth={2} />
      <title>{`pin ${p.pin} · ${p.pin_name} · ${p.needs_switch ? 'switch' : 'fixed'}`}</title>
    </g>
  )
  const els: React.ReactNode[] = []
  sides.Left.forEach((p, i) => els.push(dot(p, B0 - 13, B0 + gap(sides.Left.length) * (i + 1))))
  sides.Bottom.forEach((p, i) => els.push(dot(p, B0 + gap(sides.Bottom.length) * (i + 1), B1 + 13)))
  sides.Right.forEach((p, i) => els.push(dot(p, B1 + 13, B1 - gap(sides.Right.length) * (i + 1))))
  sides.Top.forEach((p, i) => els.push(dot(p, B1 - gap(sides.Top.length) * (i + 1), B0 - 13)))
  return (
    <svg viewBox="0 0 360 360" role="img" aria-label="package pinout map" style={{ width: '100%', maxWidth: 440 }}>
      <rect x={B0} y={B0} width={len} height={len} rx={7} fill="var(--card)" stroke="var(--border-2)" />
      <circle cx={B0 + 16} cy={B0 + 16} r={4.5} fill="var(--dim)" />
      {els}
    </svg>
  )
}

function PinsView() {
  const toast = useToast()
  const [packages, setPackages] = useState<PackageInfo[]>([])
  const [pkg, setPkg] = useState('')
  const [report, setReport] = useState<SwitchReport | null>(null)
  const [matrix, setMatrix] = useState<PinMatrix | null>(null)
  const [tab, setTab] = useState<'map' | 'switch' | 'matrix'>('map')
  const [selPin, setSelPin] = useState<number | null>(null)

  useEffect(() => {
    getJSON<PackageInfo[]>('/api/pins/packages').then((p) => { setPackages(p); if (p[0]) setPkg(p[0].package) })
      .catch((e) => toast(String(e), 'bad'))
  }, [])
  useEffect(() => {
    if (!pkg) return
    setMatrix(null); setReport(null)
    getJSON<SwitchReport>(`/api/pins/${pkg}/switch-report`).then(setReport).catch((e) => toast(String(e), 'bad'))
    getJSON<PinMatrix>(`/api/pins/${pkg}/matrix`).then(setMatrix).catch(() => {})
  }, [pkg])

  const genAuthority = async () => {
    try {
      const r = await fetch(api(`/api/authority/generate?package=${pkg}`), { method: 'POST' })
      const j = await r.json()
      toast(r.ok ? `Wrote ${j.files?.length ?? 0} files → ${j.out_dir}` : `Error: ${j.detail}`, r.ok ? 'ok' : 'bad')
    } catch (e) { toast(String(e), 'bad') }
  }

  return (
    <>
      <Header title="Pins & switch fabric" sub="Which target socket pins need an ADG714 switch channel, derived from the full per-pin role set across the STM32F family.">
        <div className="field"><span className="dim" style={{ fontSize: 13 }}>Package</span>
          <select value={pkg} onChange={(e) => setPkg(e.target.value)}>
            {packages.map((p) => <option key={p.package} value={p.package}>{p.package} · {p.mcus} MCUs</option>)}
          </select>
        </div>
      </Header>

      {report && (
        <div className="section">
          <div className="grid">
            <Metric label="Must switch" value={report.must_switch} tone="bad" />
            <Metric label="Osc-optional" value={report.osc_optional} />
            <Metric label="Fixed" value={report.fixed} tone="ok" />
            <Metric label="ADG714 cells" value={report.adg714_cells} icon={<Cpu size={13} />} />
          </div>
        </div>
      )}

      <div className="section">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div className="seg">
            <button className={tab === 'map' ? 'on' : ''} onClick={() => setTab('map')}>Pinout map</button>
            <button className={tab === 'switch' ? 'on' : ''} onClick={() => setTab('switch')}>Switch cells</button>
            <button className={tab === 'matrix' ? 'on' : ''} onClick={() => setTab('matrix')}>Full matrix</button>
          </div>
          <div className="row">
            <a className="btn" href={api(`/api/pins/${pkg}/switch-cells.csv`)}><Download size={15} />Export CSV</a>
            <button className="btn" onClick={genAuthority}><FileCog size={15} />Generate authority</button>
          </div>
        </div>

        {tab === 'map' && matrix && (
          <div className="pinmap">
            <div className="pinmap-fig">
              <PinoutMap pins={matrix.pins} selected={selPin} onSelect={setSelPin} />
              <div className="legend">
                <span><i style={{ background: '#e0805a' }} />needs switch</span>
                <span><i style={{ background: '#5f6673' }} />fixed / IO</span>
                <span className="dim">pin 1 marked by the dot · click a pin</span>
              </div>
            </div>
            <div className="pinmap-detail">
              {(() => {
                const p = matrix.pins.find((x) => x.pin === selPin)
                if (!p) return <div className="dim" style={{ padding: 16 }}>Select a pin to inspect its role set across the family.</div>
                const sw = report?.pins.find((x) => x.pin === p.pin)
                return (
                  <div className="card">
                    <div className="ct"><b>Pin {p.pin}</b> · <span className="mono">{p.pin_name}</span>
                      {p.needs_switch ? <span className="tag" style={{ marginLeft: 8 }}>switch</span> : <span className="dim" style={{ marginLeft: 8 }}>fixed</span>}</div>
                    <div className="cb">
                      <dl className="kv">
                        <dt>Side</dt><dd className="dim">{p.side}</dd>
                        <dt>GPIO</dt><dd className="mono dim">{p.gpio || '—'}</dd>
                        <dt>Roles across family</dt><dd>{p.roles}</dd>
                        <dt>Stability</dt><dd>{p.stability}</dd>
                        {sw && <><dt>Conflict</dt><dd><span className="tag">{sw.conflict_roles}</span></dd>
                          <dt>Routes to</dt><dd className="mono accent">{sw.routes_to}</dd>
                          <dt>ADG714 cell</dt><dd className="mono dim">{sw.required_cell}</dd>
                          <dt>Minority</dt><dd className="dim">{sw.minority_roles.join(', ') || '—'}</dd></>}
                      </dl>
                    </div>
                  </div>
                )
              })()}
            </div>
          </div>
        )}
        {tab === 'switch' && report && (
          <table className="tbl">
            <thead><tr><th>Pin</th><th>Side</th><th>Conflict</th><th>Routes to</th><th>Cell</th><th>Minority</th></tr></thead>
            <tbody>
              {report.pins.map((p) => (
                <tr key={p.pin}>
                  <td>{p.pin}</td><td className="dim">{p.side}</td>
                  <td><span className="tag">{p.conflict_roles}</span></td>
                  <td className="mono accent">{p.routes_to}</td>
                  <td className="mono dim">{p.required_cell}</td>
                  <td className="dim">{p.minority_roles.join(', ') || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === 'matrix' && matrix && (
          <table className="tbl">
            <thead><tr><th>Pin</th><th>Name</th><th>GPIO</th><th>Roles across family</th><th>Stability</th><th>Cell</th></tr></thead>
            <tbody>
              {matrix.pins.map((p) => (
                <tr key={p.pin}>
                  <td>{p.pin}</td><td className="mono">{p.pin_name}</td><td className="mono dim">{p.gpio}</td>
                  <td>{p.roles}</td>
                  <td>{p.needs_switch ? <span className="tag">{p.stability}</span> : <span className="dim">{p.stability}</span>}</td>
                  <td className="mono dim">{p.required_cell}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}

/* ── Netclasses ─────────────────────────────────────────── */
function NetclassesView() {
  const toast = useToast()
  const [classes, setClasses] = useState<NetClass[]>([])
  const [path, setPath] = useState('')
  const [dirty, setDirty] = useState(false)
  const [proj, setProj] = useState('')

  useEffect(() => {
    getJSON<{ path: string; classes: NetClass[] }>('/api/netclasses')
      .then((d) => { setClasses(d.classes); setPath(d.path) }).catch((e) => toast(String(e), 'bad'))
  }, [])
  const edit = (i: number, k: string, v: unknown) => { setClasses((cs) => cs.map((c, j) => (j === i ? { ...c, [k]: v } : c))); setDirty(true) }
  const save = async () => {
    try {
      const r = await fetch(api('/api/netclasses'), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ classes }) })
      const j = await r.json()
      toast(r.ok ? `Saved ${j.classes} classes (.bak written)` : `Error: ${j.detail}`, r.ok ? 'ok' : 'bad'); if (r.ok) setDirty(false)
    } catch (e) { toast(String(e), 'bad') }
  }
  const apply = async () => {
    if (!proj) { toast('Enter a .kicad_pro path first', 'bad'); return }
    try {
      const r = await fetch(api('/api/netclasses/apply'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project_path: proj, dry_run: false }) })
      const j = await r.json()
      toast(r.ok ? (j.changed ? `Applied ${j.classes} classes / ${j.patterns} patterns (.bak written)` : 'Project already matches') : `Error: ${j.detail}`, r.ok ? 'ok' : 'bad')
    } catch (e) { toast(String(e), 'bad') }
  }

  return (
    <>
      <Header title="Netclasses" sub="The vault netclass standard the build cards and KiCad share. Edits round-trip to net-classes.yaml with comments preserved.">
        <button className="btn primary" onClick={save} disabled={!dirty}><Save size={15} />{dirty ? 'Save standard' : 'Saved'}</button>
      </Header>

      <div className="section">
        <div className="banner"><Cable size={16} /><span>Source: <span className="mono">{path || 'net-classes.yaml'}</span></span></div>
        <table className="tbl">
          <thead><tr><th>Netclass</th><th style={{ width: 150 }}>Color</th><th style={{ width: 120 }}>Track</th><th style={{ width: 90 }}>Clearance</th><th>Members</th></tr></thead>
          <tbody>
            {classes.map((c, i) => (
              <tr key={c.netclass}>
                <td className="mono">{c.netclass}</td>
                <td><div className="row" style={{ gap: 7 }}>
                  <input type="color" value={String(c.color ?? '#888888')} onChange={(e) => edit(i, 'color', e.target.value)} />
                  <input type="text" className="mono" style={{ width: 82 }} value={String(c.color ?? '')} onChange={(e) => edit(i, 'color', e.target.value)} />
                </div></td>
                <td><input type="text" value={String(c.track ?? '')} onChange={(e) => edit(i, 'track', e.target.value)} /></td>
                <td><input type="text" style={{ width: 66 }} value={String(c.clearance ?? '')} onChange={(e) => edit(i, 'clearance', e.target.value === '' ? '' : Number(e.target.value))} /></td>
                <td><input type="text" className="mono" style={{ width: '100%' }} value={(c.members ?? []).join(', ')} onChange={(e) => edit(i, 'members', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="section">
        <h3>Apply to a KiCad project</h3>
        <div className="row">
          <input type="text" style={{ flex: 1, minWidth: 260 }} className="mono" placeholder="path to a .kicad_pro…" value={proj} onChange={(e) => setProj(e.target.value)} />
          <button className="btn" onClick={apply}><Cable size={15} />Apply to project</button>
        </div>
      </div>
    </>
  )
}

/* ── Database ───────────────────────────────────────────── */
function DatabaseView() {
  const toast = useToast()
  const [st, setSt] = useState<DbStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const refresh = () => { getJSON<DbStatus>('/api/database/status').then(setSt).catch((e) => toast(String(e), 'bad')) }
  useEffect(refresh, [])
  const rebuild = async () => {
    setBusy(true); toast('Building the database from CubeMX XML…', 'info')
    try {
      const r = await fetch(api('/api/database/build'), { method: 'POST' })
      const j = await r.json()
      toast(r.ok ? `Built ${j.mcus} MCUs · ${j.pins} pins · ${j.roles} roles (.bak written)` : `Error: ${j.detail}`, r.ok ? 'ok' : 'bad'); refresh()
    } catch (e) { toast(String(e), 'bad') } finally { setBusy(false) }
  }
  return (
    <>
      <Header title="Database" sub="The STM32 pin database is built from scratch out of the CubeMX MCU XML — owned by this app. Rebuilding reproduces the hand-verified ground truth (LQFP64 = 11 switch pins).">
        <button className="btn primary" onClick={rebuild} disabled={busy || !st?.source_present}><RefreshCw size={15} />{busy ? 'Building…' : 'Rebuild from CubeMX'}</button>
      </Header>
      {st && (
        <div className="section">
          <div className="grid">
            <Metric label="CubeMX XML files" value={st.xml_files} tone={st.source_present ? 'ok' : 'bad'} icon={<FolderInput size={13} />} />
            <Metric label="MCUs in database" value={st.mcu_count} tone={st.mcu_count ? 'ok' : 'bad'} icon={<Cpu size={13} />} />
            <Metric label="Database" value={st.database_present ? 'ready' : 'missing'} tone={st.database_present ? 'ok' : 'bad'} icon={<Database size={13} />} />
          </div>
          <div className="banner">
            <Database size={16} />
            <span>Source: <span className="mono">{st.cubemx_source}</span><br />Database: <span className="mono">{st.database}</span></span>
          </div>
          {!st.source_present && <div className="banner bad"><TriangleAlert size={16} /><span>CubeMX source not found. Set <span className="mono">HWKIT_CUBEMX</span> to the db/mcu folder.</span></div>}
        </div>
      )}
    </>
  )
}

/* ── shell ──────────────────────────────────────────────── */
const NAV: { id: View; label: string; icon: React.ReactNode }[] = [
  { id: 'library', label: 'Manager', icon: <Package size={16} /> },
  { id: 'pins', label: 'Pins', icon: <Cpu size={16} /> },
  { id: 'netclasses', label: 'Netclasses', icon: <Cable size={16} /> },
  { id: 'database', label: 'Database', icon: <Database size={16} /> },
]

export default function App() {
  const [view, setView] = useState<View>('library')
  const [toasts, setToasts] = useState<{ id: number; msg: string; kind: Kind }[]>([])
  const [health, setHealth] = useState<{ database_present: boolean } | null>(null)
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')

  useEffect(() => { document.documentElement.dataset.theme = theme }, [theme])
  const notify = useMemo(() => (msg: string, kind: Kind = 'info') => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, msg, kind }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000)
  }, [])
  useEffect(() => { getJSON<{ database_present: boolean }>('/api/health').then(setHealth).catch(() => setHealth(null)) }, [])

  return (
    <ToastCtx.Provider value={notify}>
      <div className="app">
        <div className="header">
          <span className="logo"><Package size={16} /></span>
          {NAV.map((n) => (
            <button key={n.id} className={`navtab ${view === n.id ? 'active' : ''}`} onClick={() => setView(n.id)}>{n.icon}{n.label}</button>
          ))}
          <span className="grow" />
          <button className="icon-btn" title="Toggle theme" aria-label="Toggle theme" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
            {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
          </button>
          <span className="branch"><GitBranch size={13} />main</span>
          <span className="activity"><span className="dot" />Idle</span>
        </div>

        <div className="body">
          {view === 'library' && <LibraryView />}
          {view === 'pins' && <PinsView />}
          {view === 'netclasses' && <NetclassesView />}
          {view === 'database' && <DatabaseView />}
        </div>

        <div className="statusbar">
          <span>{NAV.find((n) => n.id === view)?.label}</span>
          <span className="grow" />
          <span className={`chip ${health ? (health.database_present ? 'ok' : 'bad') : ''}`}>{health ? (health.database_present ? 'Database ready' : 'No database') : 'Connecting…'}</span>
          <span className="dim">127.0.0.1:8799</span>
        </div>
      </div>
      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            {t.kind === 'ok' ? <Check size={15} /> : t.kind === 'bad' ? <X size={15} /> : <Circle size={9} />}
            <span>{t.msg}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

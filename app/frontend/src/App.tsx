import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import {
  Package, Cpu, Cable, Database, Upload, FolderInput, Layers, Plug, RefreshCw,
  Wrench, Trash2, Download, FileCog, Save, Search, Check, X, TriangleAlert, Circle,
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
        <h1 style={{ fontSize: 22, fontWeight: 650, margin: 0, letterSpacing: '-.3px' }}>{title}</h1>
        <div style={{ color: 'var(--faint)', fontSize: 13, marginTop: 3, maxWidth: '64ch' }}>{sub}</div>
      </div>
      <div className="row">{children}</div>
    </div>
  )
}

/* ── types ──────────────────────────────────────────────── */
interface Audit { symbols: number; footprints: number; models: number; healthy: boolean
  summary: { symbols_bad_nickname: number; footprints_missing_model: number } }
interface CatalogEntry { symbol: string; footprint: string; footprint_ok: boolean }
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

/* ── Library ────────────────────────────────────────────── */
function LibraryView() {
  const toast = useToast()
  const [audit, setAudit] = useState<Audit | null>(null)
  const [catalog, setCatalog] = useState<CatalogEntry[]>([])
  const [busy, setBusy] = useState(false)
  const [q, setQ] = useState('')
  const [schem, setSchem] = useState('')

  const refresh = () => {
    getJSON<Audit>('/api/library/audit').then(setAudit).catch((e) => toast(String(e), 'bad'))
    getJSON<CatalogEntry[]>('/api/library/catalog').then(setCatalog).catch(() => {})
  }
  useEffect(refresh, [])

  const post = async (path: string, ok: (j: Record<string, unknown>) => string) => {
    setBusy(true)
    try {
      const r = await fetch(api(path), { method: 'POST' })
      const j = await r.json()
      toast(r.ok ? ok(j) : `Error: ${j.detail}`, r.ok ? 'ok' : 'bad')
      refresh()
    } catch (e) { toast(String(e), 'bad') } finally { setBusy(false) }
  }
  const onImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; if (!file) return
    setBusy(true)
    try {
      const fd = new FormData(); fd.append('file', file)
      const r = await fetch(api('/api/library/import'), { method: 'POST', body: fd })
      const j = await r.json()
      toast(r.ok ? `Imported ${j.symbols?.length ?? 0} symbol(s)` : `Error: ${j.detail}`, r.ok ? 'ok' : 'bad')
      refresh()
    } catch (err) { toast(String(err), 'bad') } finally { setBusy(false); e.target.value = '' }
  }
  const onRemove = async (name: string) => {
    setBusy(true)
    try {
      const r = await fetch(api('/api/library/remove'), { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, remove_footprint: true }) })
      const j = await r.json()
      toast(r.ok ? `Removed ${name}` : `Error: ${j.detail}`, r.ok ? 'ok' : 'bad'); refresh()
    } catch (e) { toast(String(e), 'bad') } finally { setBusy(false) }
  }
  const onRepair = async () => {
    if (!schem) { toast('Enter a .kicad_sch path first', 'bad'); return }
    setBusy(true)
    try {
      const r = await fetch(api('/api/library/repair-schematic'), { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: schem, dry_run: false }) })
      const j = await r.json()
      toast(r.ok ? `Repaired ${j.count} placed symbol(s) (.bak written)` : `Error: ${j.detail}`, r.ok ? 'ok' : 'bad')
    } catch (e) { toast(String(e), 'bad') } finally { setBusy(false) }
  }

  const shown = catalog.filter((c) => c.symbol.toLowerCase().includes(q.toLowerCase()) || c.footprint.toLowerCase().includes(q.toLowerCase()))

  return (
    <>
      <Header title="Library" sub="The shared KiCad library. Imports are made schematic-ready automatically — the footprint nickname and 3D-model link are fixed on the way in.">
        <label className="btn primary">
          <Upload size={15} />{busy ? 'Working…' : 'Import part'}
          <input type="file" accept=".zip" hidden onChange={onImport} disabled={busy} />
        </label>
      </Header>

      {audit && (
        <div className="section">
          <div className="grid">
            <Metric label="Symbols" value={audit.symbols} />
            <Metric label="Footprints" value={audit.footprints} />
            <Metric label="3D models" value={audit.models} />
            <Metric label="Unresolved footprints" value={audit.summary.symbols_bad_nickname} tone={audit.summary.symbols_bad_nickname ? 'bad' : 'ok'} />
            <Metric label="Missing 3D models" value={audit.summary.footprints_missing_model} tone={audit.summary.footprints_missing_model ? 'bad' : 'ok'} />
          </div>
          <div className={`banner ${audit.healthy ? 'ok' : 'bad'}`}>
            {audit.healthy ? <Check size={16} /> : <TriangleAlert size={16} />}
            <span>{audit.healthy
              ? 'Library is healthy — every symbol resolves its footprint and 3D model.'
              : `${audit.summary.symbols_bad_nickname} symbol(s) resolve no footprint and ${audit.summary.footprints_missing_model} footprint(s) have no 3D model. Run “Register in KiCad”, or re-import.`}</span>
          </div>
        </div>
      )}

      <div className="section">
        <h3>Maintenance</h3>
        <div className="row">
          <button className="btn" onClick={() => post('/api/library/process-downloads', (j) => `Imported ${(j.imported as string[]).length}, cleared ${(j.cleared as string[]).length}`)} disabled={busy}><FolderInput size={15} />Process downloads</button>
          <button className="btn" onClick={() => post('/api/library/dedupe', (j) => `Removed ${j.removed} duplicate(s)`)} disabled={busy}><Layers size={15} />Dedupe</button>
          <button className="btn" onClick={() => post('/api/library/register?dry_run=false', (j) => j.changed ? 'Registered libraries + ${MY3DMODELS} in KiCad' : 'KiCad already registered')} disabled={busy}><Plug size={15} />Register in KiCad</button>
          <button className="btn ghost" onClick={refresh}><RefreshCw size={15} />Refresh</button>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <input type="text" style={{ flex: 1, minWidth: 240 }} className="mono" placeholder="path to a .kicad_sch to repair placed symbols…" value={schem} onChange={(e) => setSchem(e.target.value)} />
          <button className="btn" onClick={onRepair} disabled={busy}><Wrench size={15} />Repair schematic</button>
        </div>
      </div>

      <div className="section">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h3 style={{ margin: 0 }}>Catalog · {shown.length}{q && ` of ${catalog.length}`}</h3>
          <div className="search"><Search size={15} /><input type="text" placeholder="Filter parts…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        </div>
        <table className="tbl">
          <thead><tr><th style={{ width: 58 }}>Preview</th><th>Symbol</th><th>Footprint</th><th style={{ width: 90 }}>Resolves</th><th style={{ width: 60 }}></th></tr></thead>
          <tbody>
            {shown.map((c) => {
              const fp = (c.footprint || '').split(':').pop() || ''
              return (
                <tr key={c.symbol}>
                  <td>{fp && <img className="thumb" loading="lazy" alt={fp} src={api(`/api/library/footprint/${encodeURIComponent(fp)}/svg`)} onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden' }} />}</td>
                  <td>{c.symbol}</td>
                  <td className="mono dim">{c.footprint || '—'}</td>
                  <td>{c.footprint_ok
                    ? <span className="pill ok"><span className="dot" />resolved</span>
                    : <span className="pill bad"><span className="dot" />unresolved</span>}</td>
                  <td><button className="btn danger sm" onClick={() => onRemove(c.symbol)} disabled={busy} aria-label={`Remove ${c.symbol}`}><Trash2 size={14} /></button></td>
                </tr>
              )
            })}
            {!shown.length && <tr><td colSpan={5}><div className="empty">No parts{q ? ' match your filter' : ' yet — import one to get started'}.</div></td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}

/* ── Pins ───────────────────────────────────────────────── */
function PinsView() {
  const toast = useToast()
  const [packages, setPackages] = useState<PackageInfo[]>([])
  const [pkg, setPkg] = useState('')
  const [report, setReport] = useState<SwitchReport | null>(null)
  const [matrix, setMatrix] = useState<PinMatrix | null>(null)
  const [tab, setTab] = useState<'switch' | 'matrix'>('switch')

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
            <button className={tab === 'switch' ? 'on' : ''} onClick={() => setTab('switch')}>Switch cells</button>
            <button className={tab === 'matrix' ? 'on' : ''} onClick={() => setTab('matrix')}>Full matrix</button>
          </div>
          <div className="row">
            <a className="btn" href={api(`/api/pins/${pkg}/switch-cells.csv`)}><Download size={15} />Export CSV</a>
            <button className="btn" onClick={genAuthority}><FileCog size={15} />Generate authority</button>
          </div>
        </div>

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
  { id: 'library', label: 'Library', icon: <Package size={17} /> },
  { id: 'pins', label: 'Pins & switch', icon: <Cpu size={17} /> },
  { id: 'netclasses', label: 'Netclasses', icon: <Cable size={17} /> },
  { id: 'database', label: 'Database', icon: <Database size={17} /> },
]

export default function App() {
  const [view, setView] = useState<View>('library')
  const [toasts, setToasts] = useState<{ id: number; msg: string; kind: Kind }[]>([])
  const [health, setHealth] = useState<{ database_present: boolean } | null>(null)

  const notify = useMemo(() => (msg: string, kind: Kind = 'info') => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, msg, kind }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000)
  }, [])
  useEffect(() => { getJSON<{ database_present: boolean }>('/api/health').then(setHealth).catch(() => setHealth(null)) }, [])

  return (
    <ToastCtx.Provider value={notify}>
      <div className="app">
        <nav className="side">
          <div className="brand">
            <span className="logo"><Package size={17} /></span>
            <div><b>Hardware</b><span>KiCad + STM32 fabric</span></div>
          </div>
          <div className="nav">
            {NAV.map((n) => (
              <button key={n.id} className={`nav-item ${view === n.id ? 'active' : ''}`} onClick={() => setView(n.id)}>{n.icon}{n.label}</button>
            ))}
          </div>
          <div className="side-foot">
            <span className={`pill ${health ? (health.database_present ? 'ok' : 'bad') : ''}`}>
              <span className="dot" />{health ? (health.database_present ? 'Database ready' : 'No database') : 'Connecting…'}
            </span>
          </div>
        </nav>
        <div className="col">
          <div className="topbar">
            <div style={{ display: 'flex', alignItems: 'center', gap: 9, color: 'var(--dim)', fontSize: 13 }}>
              {NAV.find((n) => n.id === view)?.icon}{NAV.find((n) => n.id === view)?.label}
            </div>
            <div className="spacer" />
            <span className="pill"><Plug size={13} />127.0.0.1:8799</span>
          </div>
          <div className="main">
            {view === 'library' && <LibraryView />}
            {view === 'pins' && <PinsView />}
            {view === 'netclasses' && <NetclassesView />}
            {view === 'database' && <DatabaseView />}
          </div>
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

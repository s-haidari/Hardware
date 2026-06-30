import { useEffect, useState } from 'react'
import './App.css'

type View = 'library' | 'pins' | 'netclasses'

interface Audit {
  libs_root: string
  symbols: number
  footprints: number
  models: number
  healthy: boolean
  summary: { symbols_bad_nickname: number; footprints_missing_model: number }
}
interface CatalogEntry { symbol: string; footprint: string; footprint_ok: boolean }
interface PackageInfo { package: string; mcus: number }
interface SwitchPin {
  pin: number; side: string; switch_class: string
  conflict_roles: string; routes_to: string; required_cell: string; minority_roles: string[]
}
interface SwitchReport {
  package: string; must_switch: number; osc_optional: number; fixed: number
  adg714_cells: number; pins: SwitchPin[]
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json() as Promise<T>
}

function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className={`stat ${tone ?? ''}`}>
      <div className="stat-n">{value}</div>
      <div className="stat-l">{label}</div>
    </div>
  )
}

function LibraryView() {
  const [audit, setAudit] = useState<Audit | null>(null)
  const [catalog, setCatalog] = useState<CatalogEntry[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const refresh = () => {
    getJSON<Audit>('/api/library/audit').then(setAudit).catch((e) => setMsg(String(e)))
    getJSON<CatalogEntry[]>('/api/library/catalog').then(setCatalog).catch(() => {})
  }
  useEffect(refresh, [])

  const onImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setBusy(true); setMsg('')
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await fetch('/api/library/import', { method: 'POST', body: fd })
      const j = await r.json()
      setMsg(r.ok ? `Imported ${j.symbols?.length ?? 0} symbol(s), ${j.footprints?.length ?? 0} footprint(s)` : `Error: ${j.detail}`)
      refresh()
    } catch (err) { setMsg(String(err)) } finally { setBusy(false); e.target.value = '' }
  }

  return (
    <div className="view">
      <h1>Library</h1>
      <p className="sub">Shared KiCad library — symbols, footprints, and 3D models. Imports are made schematic-ready: footprint nickname and 3D-model link are fixed automatically.</p>

      {audit && (
        <>
          <div className="grid">
            <Stat label="Symbols" value={audit.symbols} />
            <Stat label="Footprints" value={audit.footprints} />
            <Stat label="3D models" value={audit.models} />
            <Stat label="Bad footprint refs" value={audit.summary.symbols_bad_nickname} tone={audit.summary.symbols_bad_nickname ? 'bad' : 'ok'} />
            <Stat label="Missing 3D models" value={audit.summary.footprints_missing_model} tone={audit.summary.footprints_missing_model ? 'bad' : 'ok'} />
          </div>
          <div className={`banner ${audit.healthy ? 'ok' : 'bad'}`}>
            {audit.healthy
              ? 'Library is healthy — every symbol resolves its footprint and 3D model.'
              : `${audit.summary.symbols_bad_nickname} symbol(s) resolve no footprint and ${audit.summary.footprints_missing_model} footprint(s) have no 3D model. Re-import to fix.`}
          </div>
        </>
      )}

      <div className="row">
        <label className="btn">
          {busy ? 'Importing…' : 'Import part (.zip)'}
          <input type="file" accept=".zip" hidden onChange={onImport} disabled={busy} />
        </label>
        <button className="btn ghost" onClick={refresh}>Refresh</button>
        {msg && <span className="msg">{msg}</span>}
      </div>

      <h3>Catalog ({catalog.length})</h3>
      <table>
        <thead><tr><th>Symbol</th><th>Footprint</th><th>Resolves?</th></tr></thead>
        <tbody>
          {catalog.map((c) => (
            <tr key={c.symbol}>
              <td>{c.symbol}</td>
              <td className="mono">{c.footprint || '—'}</td>
              <td>{c.footprint_ok ? <span className="ok">✓</span> : <span className="bad">✗</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function PinsView() {
  const [packages, setPackages] = useState<PackageInfo[]>([])
  const [pkg, setPkg] = useState('')
  const [report, setReport] = useState<SwitchReport | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    getJSON<PackageInfo[]>('/api/pins/packages')
      .then((p) => { setPackages(p); if (p[0]) setPkg(p[0].package) })
      .catch((e) => setErr(String(e)))
  }, [])
  useEffect(() => {
    if (!pkg) return
    getJSON<SwitchReport>(`/api/pins/${pkg}/switch-report`).then(setReport).catch((e) => setErr(String(e)))
  }, [pkg])

  return (
    <div className="view">
      <h1>Pins &amp; switch fabric</h1>
      <p className="sub">Which target socket pins need an ADG714 switch channel, derived from the full per-pin role set across the STM32F family.</p>
      {err && <div className="banner bad">{err}</div>}
      <div className="row">
        <label>Package&nbsp;
          <select value={pkg} onChange={(e) => setPkg(e.target.value)}>
            {packages.map((p) => <option key={p.package} value={p.package}>{p.package} ({p.mcus})</option>)}
          </select>
        </label>
        <a className="btn ghost" href={`/api/pins/${pkg}/switch-cells.csv`}>Export CSV</a>
      </div>
      {report && (
        <>
          <div className="grid">
            <Stat label="Must switch" value={report.must_switch} tone="bad" />
            <Stat label="Osc-optional" value={report.osc_optional} />
            <Stat label="Fixed" value={report.fixed} tone="ok" />
            <Stat label="ADG714 cells" value={report.adg714_cells} />
          </div>
          <table>
            <thead><tr><th>Pin</th><th>Side</th><th>Conflict</th><th>Routes to</th><th>Cell</th><th>Minority</th></tr></thead>
            <tbody>
              {report.pins.map((p) => (
                <tr key={p.pin}>
                  <td>{p.pin}</td><td>{p.side}</td>
                  <td><span className="tag">{p.conflict_roles}</span></td>
                  <td className="mono accent">{p.routes_to}</td>
                  <td className="mono">{p.required_cell}</td>
                  <td className="mut">{p.minority_roles.join(', ') || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

interface NetClass {
  netclass: string; color?: string; track?: string | number
  clearance?: number; members?: string[]; [k: string]: unknown
}

function NetclassesView() {
  const [classes, setClasses] = useState<NetClass[]>([])
  const [path, setPath] = useState('')
  const [dirty, setDirty] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    getJSON<{ path: string; classes: NetClass[] }>('/api/netclasses')
      .then((d) => { setClasses(d.classes); setPath(d.path) })
      .catch((e) => setMsg(String(e)))
  }, [])

  const edit = (i: number, key: string, val: unknown) => {
    setClasses((cs) => cs.map((c, j) => (j === i ? { ...c, [key]: val } : c)))
    setDirty(true)
  }

  const save = async () => {
    setMsg('Saving…')
    try {
      const r = await fetch('/api/netclasses', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ classes }),
      })
      const j = await r.json()
      setMsg(r.ok ? `Saved ${j.classes} classes — .bak written` : `Error: ${j.detail}`)
      if (r.ok) setDirty(false)
    } catch (e) { setMsg(String(e)) }
  }

  return (
    <div className="view">
      <h1>Netclasses</h1>
      <p className="sub">The vault netclass standard the build cards and the KiCad project share. Edits write back to <span className="mono">{path || 'net-classes.yaml'}</span> (a .bak is kept).</p>
      <div className="row">
        <button className="btn" onClick={save} disabled={!dirty}>{dirty ? 'Save standard' : 'Saved'}</button>
        {msg && <span className="msg">{msg}</span>}
      </div>
      <table>
        <thead><tr><th>Netclass</th><th>Color</th><th>Track</th><th>Clearance</th><th>Members</th></tr></thead>
        <tbody>
          {classes.map((c, i) => (
            <tr key={c.netclass}>
              <td className="mono">{c.netclass}</td>
              <td>
                <span className="row" style={{ gap: 6, margin: 0 }}>
                  <input type="color" value={String(c.color ?? '#888888')} onChange={(e) => edit(i, 'color', e.target.value)} />
                  <input className="mono" style={{ width: 84 }} value={String(c.color ?? '')} onChange={(e) => edit(i, 'color', e.target.value)} />
                </span>
              </td>
              <td><input style={{ width: 110 }} value={String(c.track ?? '')} onChange={(e) => edit(i, 'track', e.target.value)} /></td>
              <td><input style={{ width: 70 }} value={String(c.clearance ?? '')} onChange={(e) => edit(i, 'clearance', e.target.value === '' ? '' : Number(e.target.value))} /></td>
              <td><input className="mono" style={{ width: '100%' }} value={(c.members ?? []).join(', ')} onChange={(e) => edit(i, 'members', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function App() {
  const [view, setView] = useState<View>('library')
  const nav: { id: View; label: string }[] = [
    { id: 'library', label: 'Library' },
    { id: 'pins', label: 'Pins / Switch' },
    { id: 'netclasses', label: 'Netclasses' },
  ]
  return (
    <div className="app">
      <nav className="side">
        <div className="brand">Hardware</div>
        {nav.map((n) => (
          <button key={n.id} className={view === n.id ? 'active' : ''} onClick={() => setView(n.id)}>{n.label}</button>
        ))}
        <div className="side-foot">KiCad library + STM32 switch fabric</div>
      </nav>
      <main>
        {view === 'library' && <LibraryView />}
        {view === 'pins' && <PinsView />}
        {view === 'netclasses' && <NetclassesView />}
      </main>
    </div>
  )
}

import { createIcons, Database, Trash2, Folder, ChevronDown, AlertTriangle, Star, Eye, Info, Download, RefreshCw } from 'lucide'
import { inspectSource, inspectFile, refreshSource } from '../api'
import { mkEl, val, wireCollapse } from '../dom'
import { mutate } from '../history'
import { META, SOURCE_SCHEMAS, EXT_FORMAT } from '../state'
import { comboField, wireCombos, setComboOptions, ensureComboOption } from '../combo'
import { CRS_COMBO_OPTIONS } from '../crs-list'
import { updateSourceBadge, formatSourceProgress, PENDING_RETRY_MS } from '../schema'
import { openFileExplorer } from '../file-explorer'
import { attachCrsFormatCheck } from '../validation'
import type { Source } from '../types'

// An xlsx/csv source whose geometry lives in a WKT/WKB column or a lon/lat pair usually
// names those columns something predictable — check for the common spellings so the
// geometry picker can suggest (not require) the right choice instead of making the user
// hunt through the column list themselves. Only ever used as a first-guess default, never
// enforced.
// Friendly display labels for the format combo — options otherwise fall back to their raw
// code (e.g. "gpkg"), which is clear enough on its own; "oapif" alone isn't.
const FORMAT_LABELS: Record<string, string> = {
  oapif: '<span title="OGC API - Features">OAPIF</span>',
  postgres: '<span title="PostgreSQL / PostGIS">Postgres</span>',
}

const TABULAR_GEOM_FORMATS = ['xlsx', 'csv', 'json']
const HEADER_ROW_FORMATS = ['xlsx', 'csv']
const WKT_COLUMN_NAMES = ['geom', 'geometry', 'wkt', 'the_geom', 'shape', 'wkt_geom', 'geom_wkt']
const X_COLUMN_NAMES = ['lon', 'lng', 'long', 'longitude', 'x']
const Y_COLUMN_NAMES = ['lat', 'latitude', 'y']

type TabularGeometrySuggestion =
  | { mode: 'wkt'; geomField: string }
  | { mode: 'xy'; xField: string; yField: string }

function detectTabularGeometryColumns(colNames: string[]): TabularGeometrySuggestion | null {
  const lower = colNames.map(n => n.toLowerCase())
  const wktIdx = lower.findIndex(n => WKT_COLUMN_NAMES.includes(n))
  if (wktIdx >= 0) return { mode: 'wkt', geomField: colNames[wktIdx] }
  const xIdx = lower.findIndex(n => X_COLUMN_NAMES.includes(n))
  const yIdx = lower.findIndex(n => Y_COLUMN_NAMES.includes(n))
  if (xIdx >= 0 && yIdx >= 0) return { mode: 'xy', xField: colNames[xIdx], yField: colNames[yIdx] }
  return null
}

function buildSourceCardMarkup(s: Partial<Source>): string {
  return `
    <div class="item-head" style="cursor:pointer; user-select:none;">
      <span class="tag"><i data-lucide="database" style="width:12px;height:12px;margin-right:2px"></i>source</span>
      <span class="item-title" style="font-family:var(--mono); font-size:11px; font-weight:600; margin-left:8px; color:var(--ink);"></span>
      <span class="schema-badge" style="margin-left:8px;"></span>
      <span class="spacer"></span>
      <button type="button" class="mini ghost" data-source-preview title="Preview this source" aria-label="Preview this source's raw features, before any steps or mapping"><i data-lucide="eye" style="width:12px;height:12px"></i></button>
      <button type="button" class="mini ghost set-base-btn" data-set-base title="Set as base source" aria-label="Set as base source" aria-pressed="false"><i data-lucide="star" style="width:12px;height:12px"></i> base</button>
      <button class="mini danger ghost" data-del aria-label="Remove this source"><i data-lucide="trash-2" style="width:12px;height:12px"></i> remove</button>
      <i data-lucide="chevron-down" class="card-chevron" style="width:14px;height:14px;color:var(--muted);transition:transform 0.2s;margin-left:8px;"></i>
    </div>
    <div class="card-content" style="margin-top:12px;">
      <div class="card-warning" data-incomplete hidden>
        <i data-lucide="alert-triangle" style="width:12px;height:12px;flex-shrink:0"></i>
        <span></span>
      </div>
      <div class="row">
        <label class="field grow">id<input data-k="id" placeholder="places"></label>
        <label class="field grow">format<span class="auto-chip" data-auto-format hidden>from extension</span>${comboField('data-k="format"', s.format ?? '', META.formats.map(f => FORMAT_LABELS[f] ? { value: f, label: FORMAT_LABELS[f] } : f))}</label>
      </div>
      <label class="field" style="margin-top:8px">uri / path / url
        <div style="display:flex;gap:6px">
          <input data-k="uri" placeholder="data/places.gpkg" data-uri-placeholder style="flex:1">
          <button type="button" class="mini ghost data-browse-btn" title="Browse files" style="padding:10px;flex-shrink:0"><i data-lucide="folder"></i></button>
        </div>
      </label>
      <div class="row" style="margin-top:8px">
        <label class="field grow">layer / typename / sheet / collection
          ${comboField('data-k="layer"', s.layer ?? '', [], '—')}
        </label>
        <label class="field grow">crs<span class="auto-chip" data-auto-crs hidden>detected</span>${comboField('data-k="crs"', s.crs ?? '', CRS_COMBO_OPTIONS, 'EPSG:4326')}</label>
        <label class="field-check"><input type="checkbox" data-k="make_valid" checked> repair invalid geometry</label>
      </div>
      <div class="card-warning" data-crs-warning hidden>
        <i data-lucide="alert-triangle" style="width:12px;height:12px;flex-shrink:0"></i>
        <span></span>
      </div>
      <div class="card-warning card-note" data-reader-note hidden>
        <i data-lucide="info" style="width:12px;height:12px;flex-shrink:0"></i>
        <span></span>
      </div>
      <div class="card-warning card-note download-note" data-download-note hidden
           title="Previews reuse this download until you refresh it. Run always downloads fresh data.">
        <i data-lucide="download" style="width:12px;height:12px;flex-shrink:0"></i>
        <span class="download-note-text"></span>
        <button type="button" class="mini download-refresh" data-refresh-download title="Download this source again">
          <i data-lucide="refresh-cw" style="width:12px;height:12px"></i><span>refresh</span>
        </button>
      </div>
      <div class="row" data-header-row style="display:none; margin-top:8px;">
        <label class="field grow">header row
          <select data-k="header_row">
            <option value="">auto-detect</option>
            <option value="true">first row is headers</option>
            <option value="false">no header row</option>
          </select>
        </label>
      </div>
      <div class="row" data-json-records style="display:none; margin-top:8px;">
        <label class="field grow">records path
          <input data-k="records" placeholder="data.items — leave empty if the file is the array itself">
        </label>
      </div>
      <div class="row" data-tabular-geom style="display:none; margin-top:8px;">
        <label class="field grow">geometry<span class="auto-chip" data-auto-geom hidden>detected</span>
          <select data-geom-mode>
            <option value="">none (tabular only)</option>
            <option value="xy">point from X / Y columns</option>
            <option value="wkt">WKT / WKB / GeoJSON column</option>
          </select>
        </label>
      </div>
      <div class="row" data-tabular-geom-xy style="display:none; margin-top:8px;">
        <label class="field grow">X / longitude column
          ${comboField('data-k="x_field"', s.x_field ?? '', [], '—')}
        </label>
        <label class="field grow">Y / latitude column
          ${comboField('data-k="y_field"', s.y_field ?? '', [], '—')}
        </label>
      </div>
      <div class="row" data-tabular-geom-wkt style="display:none; margin-top:8px;">
        <label class="field grow">WKT / WKB / GeoJSON column
          ${comboField('data-k="geom_field"', s.geom_field ?? '', [], '—')}
        </label>
      </div>
      <p class="hint" data-arcgis style="display:none">ArcGIS REST: uri = the service root (…/MapServer or …/FeatureServer, no trailing id). Pick the sublayer below. Geometry fetched as EPSG:4326.</p>
      <p class="hint" data-oapif style="display:none">OGC API - Features: uri = the API root (e.g. https://host) — not an /items URL. Pick the collection below.</p>
      <p class="hint" data-json style="display:none">JSON: plain records (an array of objects, or newline-delimited objects), not GeoJSON — use the geojson format for a FeatureCollection. Nested objects come through as struct columns (map them with an expression like <code>address.city</code>).</p>
      <p class="hint" data-postgres style="display:none">Postgres / PostGIS: uri = a full connection string (e.g. postgresql://user:pass@host:5432/dbname). Pick the table below (schema.table, or just table for the public schema).</p>
      <div class="schema-list" style="display:none;font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:8px;border-top:1px dashed var(--line);padding-top:6px;"></div>
    </div>`
}

// ---- source card ----
export function sourceCard(s: Partial<Source> = {}, syncFn: () => void): HTMLElement {
  const c = mkEl('div', { className: 'card collapsed' })
  c.innerHTML = buildSourceCardMarkup(s)

  wireCombos(c)

  c.querySelector('[data-set-base]')!.addEventListener('click', (e) => {
    e.stopPropagation()
    const id = val(c, 'id')
    if (!id) return
    c.dispatchEvent(new CustomEvent('set-base', { bubbles: true, detail: { id } }))
  })

  // Handled in main.ts, which owns what the map/table preview is showing.
  c.querySelector('[data-source-preview]')!.addEventListener('click', (e) => {
    e.stopPropagation()
    const id = val(c, 'id')
    if (!id) return
    const plCard = c.closest('.pipeline-card')
    const pipelineIdx = Array.from(document.querySelectorAll('.pipeline-card')).indexOf(plCard!)
    c.dispatchEvent(new CustomEvent('preview-source', { bubbles: true, detail: { id, pipelineIdx } }))
  })

  c.querySelector('[data-del]')!.addEventListener('click', (e) => {
    e.stopPropagation()
    const id = val(c, 'id')
    mutate('remove source', { undoToast: `Removed source${id ? ` "${id}"` : ''}` })
    if (id) delete SOURCE_SCHEMAS[id]
    c.classList.add('slide-out')
    setTimeout(() => { c.remove(); syncFn() }, 250)
  })

  for (const [k, v] of Object.entries(s)) {
    if (k === 'make_valid') continue
    const inp = c.querySelector<HTMLInputElement | HTMLSelectElement>(`[data-k="${k}"]`)
    if (inp && v != null) inp.value = String(v)
  }
  c.querySelector<HTMLInputElement>('[data-k="make_valid"]')!.checked = s.make_valid !== false

  c.querySelector<HTMLButtonElement>('.data-browse-btn')!.onclick = (e) => {
    e.stopPropagation()
    openFileExplorer(c.querySelector<HTMLInputElement>('[data-k="uri"]')!)
  }

  c.querySelectorAll('[data-k]').forEach(i => {
    i.addEventListener('input', syncFn)
    i.addEventListener('change', syncFn)
  })

  const fmt = c.querySelector<HTMLInputElement>('[data-k="format"]')!

  const toggles = wireFormatToggles(c, fmt, s, syncFn)
  const { toggleTabularGeomSub, geomModeSel } = toggles

  const { autoDetectFormat, autoDetectOapifCollection, autoDetectArcgisLayer } =
    createAutoDetectors(c, fmt, toggles)

  const { doInspect, doInspectFile } = createInspectors(c, syncFn, geomModeSel, toggleTabularGeomSub)

  c.querySelectorAll('[data-k="uri"],[data-k="format"],[data-k="layer"],[data-k="crs"],[data-k="header_row"],[data-k="records"],[data-k="x_field"],[data-k="y_field"],[data-k="geom_field"]').forEach(i => {
    i.addEventListener('input', doInspect)
    i.addEventListener('change', doInspect)
  })
  geomModeSel.addEventListener('input', doInspect)
  geomModeSel.addEventListener('change', doInspect)
  const uriInp = c.querySelector<HTMLInputElement>('[data-k="uri"]')!
  uriInp.addEventListener('input', autoDetectFormat)
  uriInp.addEventListener('change', autoDetectFormat)
  uriInp.addEventListener('change', autoDetectOapifCollection)
  uriInp.addEventListener('change', autoDetectArcgisLayer)
  c.querySelectorAll('[data-k="uri"],[data-k="format"]').forEach(i => {
    i.addEventListener('input', doInspectFile)
    i.addEventListener('change', doInspectFile)
  })
  c.querySelector('[data-k="id"]')!.addEventListener('input', doInspect)
  attachCrsFormatCheck(c.querySelector<HTMLInputElement>('[data-k="crs"]')!)

  if (s.uri) {
    setTimeout(async () => {
      await doInspectFile()
      if (s.layer) {
        const sel = c.querySelector<HTMLInputElement>('[data-k="layer"]')
        if (sel) {
          ensureComboOption(sel, s.layer)
          sel.value = s.layer
        }
      }
      if (s.id) doInspect()
    }, 100)
  }

  wireTitle(c, fmt)
  wireIncompleteWarning(c, uriInp)

  wireCollapse(c, { headerSel: '.item-head', chevronSel: '.card-chevron', bodySel: '.card-content' })

  createIcons({ icons: { Database, Trash2, Folder, ChevronDown, AlertTriangle, Star, Eye, Info, Download, RefreshCw } })
  return c
}

type FormatToggles = ReturnType<typeof wireFormatToggles>

/** Infer the format (and, for OAPIF/ArcGIS item URLs, the layer) from what was typed as the uri. */
function createAutoDetectors(c: HTMLElement, fmt: HTMLInputElement, toggles: FormatToggles) {
  const { toggleArcgis, toggleOapif, togglePostgres } = toggles

  // Auto-detect format from file extension when URI changes
  const autoDetectFormat = () => {
    const uri = val(c, 'uri')
    if (!uri) return
    const bare = uri.split('?')[0].split('#')[0]
    const ext = bare.split('.').pop()?.toLowerCase() ?? ''
    let detected = EXT_FORMAT[ext]
    if (!detected) {
      const lower = uri.toLowerCase()
      if (lower.includes('service=wfs') ||
          /\/wfs[./? ]|\/wfs$/.test(lower) ||
          /\.wfs[./? ]|\.wfs$/.test(lower)) {
        detected = 'wfs'
      } else if (/^postgres(ql)?:\/\//.test(lower)) {
        detected = 'postgres'
      }
    }
    if (detected && fmt.value !== detected) {
      fmt.value = detected
      showChip(c, '[data-auto-format]', true)
      fmt.dispatchEvent(new Event('change', { bubbles: true }))
      toggleArcgis()
      toggleOapif()
      togglePostgres()
    }
  }

  // Trim a pasted URL back to the root and pull the trailing id into the layer field.
  const adoptRootAndLayer = (format: string, root: string, layerValue: string) => {
    fmt.value = format
    showChip(c, '[data-auto-format]', true)
    fmt.dispatchEvent(new Event('change', { bubbles: true }))
    toggleArcgis()
    toggleOapif()
    const uriInp = c.querySelector<HTMLInputElement>('[data-k="uri"]')!
    uriInp.value = root
    const layerSel = c.querySelector<HTMLInputElement>('[data-k="layer"]')
    if (layerSel) {
      ensureComboOption(layerSel, layerValue)
      layerSel.value = layerValue
      layerSel.dispatchEvent(new Event('change', { bubbles: true }))
    }
    uriInp.dispatchEvent(new Event('change', { bubbles: true }))
  }

  // Pasting/committing a full OGC API - Features /collections/{id}/items URL: trim the
  // uri back to the API root and pull the collection id into the layer field, since the
  // items endpoint itself isn't a readable data source on its own (it needs an Accept
  // header and pagination, which is what fetch_oapif() on the backend handles). Bound to
  // 'change' (fires on blur, and after a paste once the field commits) rather than every
  // 'input' keystroke, since it rewrites the field's value and doing that mid-typing
  // would yank the cursor out from under someone still typing a collection id.
  const autoDetectOapifCollection = () => {
    const uri = val(c, 'uri')
    if (!uri) return
    const bare = uri.split('?')[0].split('#')[0]
    const collMatch = bare.match(/^(.*?)\/collections\/([^/?#]+)\/items\/?$/i)
    if (!collMatch) return
    adoptRootAndLayer('oapif', collMatch[1], collMatch[2])
  }

  // Pasting/committing a full ArcGIS REST sublayer URL (.../MapServer/0 or
  // .../FeatureServer/3): trim the uri back to the service root and pull the
  // sublayer id into the layer field, mirroring autoDetectOapifCollection above.
  const autoDetectArcgisLayer = () => {
    const uri = val(c, 'uri')
    if (!uri) return
    const bare = uri.split('?')[0].split('#')[0]
    const m = bare.match(/^(.*\/(?:MapServer|FeatureServer))\/(\d+)\/?$/i)
    if (!m) return
    adoptRootAndLayer('arcgis_rest', m[1], m[2])
  }

  return { autoDetectFormat, autoDetectOapifCollection, autoDetectArcgisLayer }
}

/** The two debounced backend probes: schema (/api/inspect) and layer list (/api/inspect_file). */
function createInspectors(
  c: HTMLElement,
  syncFn: () => void,
  geomModeSel: HTMLSelectElement,
  toggleTabularGeomSub: () => void,
) {
  let inspectTimer = 0
  let inspectSeq = 0
  let pendingText: string | null = null
  let lastSource: Source | null = null  // what the last inspect sent, for "refresh"
  let fileTimer = 0

  // "refresh" on the download note: download again in the background, then re-inspect —
  // which shows the progress and, once done, re-runs the preview (syncFn).
  const refreshBtn = c.querySelector<HTMLButtonElement>('[data-refresh-download]')!
  refreshBtn.addEventListener('click', async e => {
    e.preventDefault()
    e.stopPropagation()
    if (!lastSource) return
    // Busy until the next inspect answers (updateDownloadNote resets it).
    refreshBtn.disabled = true
    refreshBtn.querySelector('svg')?.classList.add('spin-animation')
    refreshBtn.querySelector('span')!.textContent = 'refreshing…'
    const d = await refreshSource(lastSource)
    if (!d.ok && !d.pending && d.error) {
      updateSourceBadge(c, { ok: false, error: d.error })
      updateDownloadNote(c, null)
      return
    }
    if (d.pending && d.progress) pendingText = formatSourceProgress(d.progress)
    doInspect()
  })

  const doInspect = () => {
    clearTimeout(inspectTimer)
    inspectTimer = window.setTimeout(async () => {
      const id = val(c, 'id')
      const format = val(c, 'format')
      const uri = val(c, 'uri')
      const layer = val(c, 'layer')
      const crs = val(c, 'crs')
      const headerRow = val(c, 'header_row')
      const records = format === 'json' ? val(c, 'records') : ''
      const xField = val(c, 'x_field')
      const yField = val(c, 'y_field')
      const geomField = val(c, 'geom_field')
      updateReaderNote(c, null)
      if (!id || !uri) { updateSourceBadge(c, null); updateCrsWarning(c, null); return }
      if (format === 'wfs' && !layer) {
        updateSourceBadge(c, { ok: false, error: 'select a layer typename from the dropdown first' })
        updateCrsWarning(c, null)
        return
      }
      if (format === 'oapif' && !layer) {
        updateSourceBadge(c, { ok: false, error: 'select a collection from the dropdown first' })
        updateCrsWarning(c, null)
        return
      }
      if (format === 'arcgis_rest' && !layer) {
        updateSourceBadge(c, { ok: false, error: 'select a sublayer from the dropdown first' })
        updateCrsWarning(c, null)
        return
      }
      if (format === 'postgres' && !layer) {
        updateSourceBadge(c, { ok: false, error: 'select a table from the dropdown first' })
        updateCrsWarning(c, null)
        return
      }
      const srcObj = {
        id, format, uri,
        ...(layer ? { layer } : {}),
        ...(crs ? { crs } : {}),
        ...(headerRow ? { header_row: headerRow === 'true' } : {}),
        ...(records ? { records } : {}),
        ...(xField ? { x_field: xField } : {}),
        ...(yField ? { y_field: yField } : {}),
        ...(geomField ? { geom_field: geomField } : {}),
      } as Source
      lastSource = srcObj
      // Keep showing the last progress text while asking again, not a flash of "inspecting…".
      updateSourceBadge(c, { loading: true, loadingText: pendingText })
      const seq = ++inspectSeq
      try {
        const d = await inspectSource(srcObj)
        if (seq !== inspectSeq) return  // a newer inspect owns the badge now
        if (d.pending && d.progress) {
          // Downloading / converting in the background: show what, and ask again.
          pendingText = formatSourceProgress(d.progress)
          updateSourceBadge(c, { loading: true, loadingText: pendingText })
          window.setTimeout(() => { if (seq === inspectSeq) doInspect() }, PENDING_RETRY_MS)
          return
        }
        pendingText = null
        if (d.ok && d.columns) {
          SOURCE_SCHEMAS[id] = d.columns
          updateSourceBadge(c, { ok: true, columns: d.columns })
          updateCrsWarning(c, d.crs_warning)
          updateReaderNote(c, d.reader_note)
          updateDownloadNote(c, d.download)
          // Offer the just-inspected columns as suggestions for the geometry-column
          // pickers below — same idea as doInspectFile populating the `layer` combo.
          if (TABULAR_GEOM_FORMATS.includes(format)) {
            const colOptions = d.columns.map(col => col.name)
            ;['x_field', 'y_field', 'geom_field'].forEach(k => {
              const sel = c.querySelector<HTMLInputElement>(`[data-k="${k}"]`)
              if (sel) setComboOptions(sel, colOptions)
            })
            // A fresh xlsx/csv with no geometry chosen yet: if a column looks like an
            // obvious WKT/WKB field, or there's a plausible lon/lat pair, pre-select it
            // instead of leaving the user to go find the right column name themselves —
            // same "auto-fill + say so" treatment as the auto-detected format/CRS chips
            // above. Never fires again once something's actually picked (including
            // "none"), so it won't clobber a deliberate choice on a later re-inspect.
            if (!xField && !yField && !geomField && geomModeSel.value === '') {
              const suggestion = detectTabularGeometryColumns(colOptions)
              if (suggestion?.mode === 'wkt') {
                geomModeSel.value = 'wkt'
                const geomInp = c.querySelector<HTMLInputElement>('[data-k="geom_field"]')
                if (geomInp) {
                  showChip(c, '[data-auto-geom]', true)
                  ensureComboOption(geomInp, suggestion.geomField)
                  geomInp.value = suggestion.geomField
                  toggleTabularGeomSub()
                  geomInp.dispatchEvent(new Event('change', { bubbles: true }))
                }
              } else if (suggestion?.mode === 'xy') {
                geomModeSel.value = 'xy'
                const xInp = c.querySelector<HTMLInputElement>('[data-k="x_field"]')
                const yInp = c.querySelector<HTMLInputElement>('[data-k="y_field"]')
                if (xInp && yInp) {
                  showChip(c, '[data-auto-geom]', true)
                  ensureComboOption(xInp, suggestion.xField)
                  xInp.value = suggestion.xField
                  ensureComboOption(yInp, suggestion.yField)
                  yInp.value = suggestion.yField
                  toggleTabularGeomSub()
                  yInp.dispatchEvent(new Event('change', { bubbles: true }))
                }
              }
            }
          }
        } else {
          delete SOURCE_SCHEMAS[id]
          updateSourceBadge(c, { ok: false, error: d.error })
          updateCrsWarning(c, null)
        }
      } catch {
        delete SOURCE_SCHEMAS[id]
        updateSourceBadge(c, { ok: false, error: 'connection failed' })
        updateCrsWarning(c, null)
      }
      syncFn()
    }, 500)
  }

  const doInspectFile = () => {
    clearTimeout(fileTimer)
    fileTimer = window.setTimeout(async () => {
      const uri = val(c, 'uri')
      const format = val(c, 'format')
      const layerSel = c.querySelector<HTMLInputElement>('[data-k="layer"]')
      const crsInp = c.querySelector<HTMLInputElement>('[data-k="crs"]')
      if (!uri || !format) return
      // Remote sources (wfs, arcgis_rest) are a full round trip; without a badge this
      // looked like nothing was happening.
      updateSourceBadge(c, { loading: true, loadingText: pendingText })
      let inspectUri = uri
      if (format === 'wfs') {
        try {
          const u = new URL(uri.startsWith('http') ? uri : `https://${uri}`)
          u.searchParams.delete('request')
          u.searchParams.delete('REQUEST')
          inspectUri = u.toString()
        } catch { /* keep as-is */ }
      }
      try {
        const d = await inspectFile(inspectUri, format)
        if (d.pending && d.progress) {
          // A remote file still downloading in the background: show it, and ask again.
          pendingText = formatSourceProgress(d.progress)
          updateSourceBadge(c, { loading: true, loadingText: pendingText })
          window.setTimeout(() => {
            if (val(c, 'uri') === uri && val(c, 'format') === format) doInspectFile()
          }, PENDING_RETRY_MS)
          return
        }
        if (d.ok) {
          if (layerSel && d.layers && d.layers.length > 0) {
            const current = layerSel.value
            setComboOptions(layerSel, d.layers)
            const values = d.layers.map(l => typeof l === 'string' ? l : l.value)
            layerSel.value = values.includes(current) ? current : ''
            if (format !== 'wfs' && format !== 'arcgis_rest' && !values.includes(current) && d.layers.length > 0) {
              layerSel.value = values[0]
              layerSel.dispatchEvent(new Event('change', { bubbles: true }))
            }
          }
          if (crsInp && d.default_crs) {
            showChip(c, '[data-auto-crs]', crsInp.value !== d.default_crs)
            crsInp.value = d.default_crs
            crsInp.dispatchEvent(new Event('input', { bubbles: true }))
          }
        } else {
          // Previously console.error only — the user saw nothing at all.
          updateSourceBadge(c, { ok: false, error: d.error || 'could not read this file' })
        }
      } catch (e) {
        console.error('Failed to inspect file:', e)
        updateSourceBadge(c, { ok: false, error: `could not read this file: ${e instanceof Error ? e.message : String(e)}` })
      }
    }, 400)
  }

  return { doInspect, doInspectFile }
}

// A value the editor filled in on your behalf should say so — these used to overwrite a
// deliberate choice with no visible trace.
function showChip(c: HTMLElement, sel: string, on: boolean): void {
  const chip = c.querySelector<HTMLElement>(sel)
  if (chip) chip.hidden = !on
}

// Shows/hides the "this crs looks wrong for the sampled coordinates" hint from
// /api/inspect's crs_warning — see crs_extent_warning in sources.py.
function updateCrsWarning(c: HTMLElement, warning: string | null | undefined): void {
  updateCardHint(c, '[data-crs-warning]', warning)
}

// /api/inspect's reader_note: the source is read via pyogrio rather than ST_Read (a file
// over 2 GiB on Windows) — see large_file_reader_note in sources.py.
function updateReaderNote(c: HTMLElement, note: string | null | undefined): void {
  updateCardHint(c, '[data-reader-note]', note)
}

const ago = (seconds: number): string => {
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`
  return `${Math.round(seconds / 86400)} d ago`
}

// /api/inspect's download: previews of a remote file source read an earlier download until
// refreshed (runs always fetch fresh) — say when it was made, with a "refresh" link.
function updateDownloadNote(c: HTMLElement, dl: { bytes: number; age: number; parquet: boolean } | null | undefined): void {
  const box = c.querySelector<HTMLElement>('[data-download-note]')
  if (!box) return
  const btn = box.querySelector<HTMLButtonElement>('[data-refresh-download]')!
  btn.disabled = false
  btn.querySelector('svg')?.classList.remove('spin-animation')
  btn.querySelector('span')!.textContent = 'refresh'
  box.hidden = !dl
  if (!dl) return
  // The facts only; what they mean for previews vs runs is the note's tooltip.
  const parts = [`Downloaded ${ago(dl.age)}`, `${(dl.bytes / 1e6).toFixed(1)} MB`]
  if (dl.parquet) parts.push('read via Parquet')
  box.querySelector('.download-note-text')!.textContent = parts.join(' · ')
}

function updateCardHint(c: HTMLElement, sel: string, text: string | null | undefined): void {
  const box = c.querySelector<HTMLElement>(sel)
  if (!box) return
  box.hidden = !text
  const msg = box.querySelector('span')
  if (msg) msg.textContent = text ?? ''
}

const URI_PLACEHOLDERS: Record<string, string> = {
  gpkg: 'data/places.gpkg',
  geojson: 'data/places.geojson',
  gml: 'data/places.gml',
  fgdb: 'data/places.gdb',
  wfs: 'https://example.com/geoserver/wfs',
  arcgis_rest: 'https://example.com/arcgis/rest/services/Places/FeatureServer',
  oapif: 'https://example.com',
  parquet: 'data/places.parquet',
  flatgeobuf: 'data/places.fgb',
  shp: 'data/places.shp',
  xlsx: 'data/places.xlsx',
  csv: 'data/places.csv',
  json: 'https://api.example.com/places.json',
  postgres: 'postgresql://user:pass@host:5432/dbname',
}

/** Format-driven show/hide of the per-format hints and the xlsx/csv-only rows. */
function wireFormatToggles(
  c: HTMLElement, fmt: HTMLInputElement, s: Partial<Source>, syncFn: () => void,
) {
  const uriPlaceholderInp = c.querySelector<HTMLInputElement>('[data-uri-placeholder]')!
  const updateUriPlaceholder = () => {
    uriPlaceholderInp.placeholder = URI_PLACEHOLDERS[fmt.value] ?? 'data/places.gpkg'
  }
  fmt.addEventListener('input', updateUriPlaceholder)
  fmt.addEventListener('change', updateUriPlaceholder)

  const toggleArcgis = () => {
    const hint = c.querySelector<HTMLElement>('[data-arcgis]')!
    hint.style.display = fmt.value === 'arcgis_rest' ? 'block' : 'none'
  }
  const toggleOapif = () => {
    const hint = c.querySelector<HTMLElement>('[data-oapif]')!
    hint.style.display = fmt.value === 'oapif' ? 'block' : 'none'
    fmt.title = fmt.value === 'oapif' ? 'OGC API - Features' : ''
  }
  const togglePostgres = () => {
    const hint = c.querySelector<HTMLElement>('[data-postgres]')!
    hint.style.display = fmt.value === 'postgres' ? 'block' : 'none'
  }
  fmt.addEventListener('input', toggleArcgis)
  fmt.addEventListener('change', toggleArcgis)
  fmt.addEventListener('input', toggleOapif)
  fmt.addEventListener('change', toggleOapif)
  fmt.addEventListener('input', togglePostgres)
  fmt.addEventListener('change', togglePostgres)
  // json: the hint plus the records-path row.
  const toggleJson = () => {
    const show = fmt.value === 'json'
    c.querySelector<HTMLElement>('[data-json]')!.style.display = show ? 'block' : 'none'
    c.querySelector<HTMLElement>('[data-json-records]')!.style.display = show ? 'flex' : 'none'
  }
  fmt.addEventListener('input', toggleJson)
  fmt.addEventListener('change', toggleJson)
  toggleArcgis()
  toggleOapif()
  togglePostgres()
  toggleJson()
  updateUriPlaceholder()

  // Header-row override: xlsx/csv only (GDAL's HEADERS open option exists for both drivers).
  const toggleHeaderRow = () => {
    const row = c.querySelector<HTMLElement>('[data-header-row]')!
    row.style.display = HEADER_ROW_FORMATS.includes(fmt.value) ? 'flex' : 'none'
  }
  // Geometry-from-columns: xlsx/csv/json only. Not a GDAL feature any of these readers
  // provides — the XLSX driver has no geometry support at all, the CSV driver's own
  // X_POSSIBLE_NAMES/GEOM_POSSIBLE_NAMES open options came with real sharp edges, and json
  // isn't read through GDAL at all (see sources.py's _apply_tabular_geometry) — so the
  // engine builds the geometry itself in SQL, identically for every tabular format.
  const geomModeSel = c.querySelector<HTMLSelectElement>('[data-geom-mode]')!
  const toggleTabularGeom = () => {
    const row = c.querySelector<HTMLElement>('[data-tabular-geom]')!
    row.style.display = TABULAR_GEOM_FORMATS.includes(fmt.value) ? 'flex' : 'none'
  }
  const toggleTabularGeomSub = () => {
    const xyRow = c.querySelector<HTMLElement>('[data-tabular-geom-xy]')!
    const wktRow = c.querySelector<HTMLElement>('[data-tabular-geom-wkt]')!
    const active = TABULAR_GEOM_FORMATS.includes(fmt.value) ? geomModeSel.value : ''
    xyRow.style.display = active === 'xy' ? 'flex' : 'none'
    wktRow.style.display = active === 'wkt' ? 'flex' : 'none'
  }
  fmt.addEventListener('input', toggleHeaderRow)
  fmt.addEventListener('change', toggleHeaderRow)
  fmt.addEventListener('input', toggleTabularGeom)
  fmt.addEventListener('change', toggleTabularGeom)
  fmt.addEventListener('input', toggleTabularGeomSub)
  fmt.addEventListener('change', toggleTabularGeomSub)
  geomModeSel.addEventListener('input', toggleTabularGeomSub)
  geomModeSel.addEventListener('change', toggleTabularGeomSub)
  // geom_mode is derived UI state, not a saved field — pick its initial value from
  // whichever real geometry field(s) were hydrated onto the card.
  geomModeSel.value = s.x_field && s.y_field ? 'xy' : s.geom_field ? 'wkt' : ''
  geomModeSel.addEventListener('input', syncFn)
  geomModeSel.addEventListener('change', syncFn)
  toggleHeaderRow()
  toggleTabularGeom()
  toggleTabularGeomSub()

  return { toggleArcgis, toggleOapif, togglePostgres, toggleTabularGeomSub, geomModeSel }
}

function wireTitle(c: HTMLElement, fmt: HTMLInputElement): void {
  const idInp = c.querySelector<HTMLInputElement>('[data-k="id"]')!
  const titleEl = c.querySelector<HTMLElement>('.item-title')!
  const updateTitle = () => {
    const id = idInp.value.trim()
    titleEl.textContent = id ? `${id} (${fmt.value})` : ''
  }
  idInp.addEventListener('input', updateTitle)
  fmt.addEventListener('change', updateTitle)
  updateTitle()
}

// Flag a card that collectPipelineDef() will drop for want of an id or uri, instead of
// letting it vanish from the generated YAML without explanation.
function wireIncompleteWarning(c: HTMLElement, uriInp: HTMLInputElement): void {
  const update = () => {
    const box = c.querySelector<HTMLElement>('[data-incomplete]')
    if (!box) return
    const missing: string[] = []
    if (!val(c, 'id')) missing.push('an id')
    if (!val(c, 'uri')) missing.push('a uri')
    box.hidden = missing.length === 0
    const msg = box.querySelector('span')
    if (msg) msg.textContent = `Not included in the pipeline yet — needs ${missing.join(' and ')}.`
  }
  c.querySelector<HTMLInputElement>('[data-k="id"]')!.addEventListener('input', update)
  uriInp.addEventListener('input', update)
  update()
}

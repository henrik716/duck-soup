import { createIcons, Database, Trash2, Folder, ChevronDown, AlertTriangle } from 'lucide'
import { inspectSource, inspectFile } from '../api'
import { mkEl, val, wireCollapse } from '../dom'
import { mutate } from '../history'
import { META, SOURCE_SCHEMAS, EXT_FORMAT } from '../state'
import { comboField, wireCombos, setComboOptions, ensureComboOption } from '../combo'
import { updateSourceBadge } from '../schema'
import { openFileExplorer } from '../file-explorer'
import type { Source } from '../types'

// ---- source card ----
export function sourceCard(s: Partial<Source> = {}, syncFn: () => void): HTMLElement {
  const c = mkEl('div', { className: 'card collapsed' })
  c.innerHTML = `
    <div class="item-head" style="cursor:pointer; user-select:none;">
      <span class="tag"><i data-lucide="database" style="width:12px;height:12px;margin-right:2px"></i>source</span>
      <span class="item-title" style="font-family:var(--mono); font-size:11px; font-weight:600; margin-left:8px; color:var(--ink);"></span>
      <span class="schema-badge" style="margin-left:8px;"></span>
      <span class="spacer"></span>
      <button class="mini danger ghost" data-del aria-label="Remove this source"><i data-lucide="trash-2" style="width:12px;height:12px"></i> remove</button>
      <i data-lucide="chevron-down" class="card-chevron" style="width:14px;height:14px;color:var(--muted);transition:transform 0.2s;margin-left:8px;"></i>
    </div>
    <div class="card-content" style="margin-top:12px;">
      <div class="card-warning" data-incomplete hidden>
        <i data-lucide="alert-triangle" style="width:12px;height:12px;flex-shrink:0"></i>
        <span></span>
      </div>
      <div class="row">
        <label class="field grow">id<input data-k="id" placeholder="ambassader"></label>
        <label class="field grow">format<span class="auto-chip" data-auto-format hidden>from extension</span>${comboField('data-k="format"', s.format ?? '', META.formats)}</label>
      </div>
      <label class="field" style="margin-top:8px">uri / path / url
        <div style="display:flex;gap:6px">
          <input data-k="uri" placeholder="data/Ambassader.gpkg" style="flex:1">
          <button type="button" class="mini ghost data-browse-btn" title="Browse files" style="padding:10px;flex-shrink:0"><i data-lucide="folder"></i></button>
        </div>
      </label>
      <div class="row" style="margin-top:8px">
        <label class="field grow">layer / typename / sheet
          ${comboField('data-k="layer"', s.layer ?? '', [], '—')}
        </label>
        <label class="field grow">crs<span class="auto-chip" data-auto-crs hidden>detected</span><input data-k="crs" placeholder="EPSG:4326"></label>
        <label class="field" style="flex:0 0 auto"><span>&nbsp;</span>
          <span style="display:flex;align-items:center;gap:6px;color:var(--ink);font-family:system-ui;white-space:nowrap">
            <input type="checkbox" data-k="make_valid" checked style="width:auto;margin:0"> repair invalid geometry</span></label>
      </div>
      <p class="hint" data-arcgis style="display:none">ArcGIS REST: uri = layer endpoint (…/FeatureServer/0). Geometry fetched as EPSG:4326.</p>
      <div class="schema-list" style="display:none;font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:8px;border-top:1px dashed var(--line);padding-top:6px;"></div>
    </div>`

  wireCombos(c)

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
  const toggleArcgis = () => {
    const hint = c.querySelector<HTMLElement>('[data-arcgis]')!
    hint.style.display = fmt.value === 'arcgis_rest' ? 'block' : 'none'
  }
  fmt.addEventListener('input', toggleArcgis)
  fmt.addEventListener('change', toggleArcgis)
  toggleArcgis()

  // A value the editor filled in on your behalf should say so — these used to overwrite a
  // deliberate choice with no visible trace.
  const showChip = (sel: string, on: boolean) => {
    const chip = c.querySelector<HTMLElement>(sel)
    if (chip) chip.hidden = !on
  }

  // Flag a card that collectPipelineDef() will drop for want of an id or uri, instead of
  // letting it vanish from the generated YAML without explanation.
  const updateIncompleteWarning = () => {
    const box = c.querySelector<HTMLElement>('[data-incomplete]')
    if (!box) return
    const missing: string[] = []
    if (!val(c, 'id')) missing.push('an id')
    if (!val(c, 'uri')) missing.push('a uri')
    box.hidden = missing.length === 0
    const msg = box.querySelector('span')
    if (msg) msg.textContent = `Not included in the pipeline yet — needs ${missing.join(' and ')}.`
  }

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
      }
    }
    if (detected && fmt.value !== detected) {
      fmt.value = detected
      showChip('[data-auto-format]', true)
      fmt.dispatchEvent(new Event('change', { bubbles: true }))
      toggleArcgis()
    }
  }

  let inspectTimer = 0
  let fileTimer = 0

  const doInspect = () => {
    clearTimeout(inspectTimer)
    inspectTimer = window.setTimeout(async () => {
      const id = val(c, 'id')
      const format = val(c, 'format')
      const uri = val(c, 'uri')
      const layer = val(c, 'layer')
      const crs = val(c, 'crs')
      if (!id || !uri) { updateSourceBadge(c, null); return }
      if (format === 'wfs' && !layer) {
        updateSourceBadge(c, { ok: false, error: 'select a layer typename from the dropdown first' })
        return
      }
      const srcObj = { id, format, uri, ...(layer ? { layer } : {}), ...(crs ? { crs } : {}) } as Source
      updateSourceBadge(c, { loading: true })
      try {
        const d = await inspectSource(srcObj)
        if (d.ok && d.columns) {
          SOURCE_SCHEMAS[id] = d.columns
          updateSourceBadge(c, { ok: true, columns: d.columns })
        } else {
          delete SOURCE_SCHEMAS[id]
          updateSourceBadge(c, { ok: false, error: d.error })
        }
      } catch {
        delete SOURCE_SCHEMAS[id]
        updateSourceBadge(c, { ok: false, error: 'connection failed' })
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
      updateSourceBadge(c, { loading: true })
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
        if (d.ok) {
          if (layerSel && d.layers && d.layers.length > 0) {
            const current = layerSel.value
            setComboOptions(layerSel, d.layers)
            layerSel.value = d.layers.includes(current) ? current : ''
            if (format !== 'wfs' && !d.layers.includes(current) && d.layers.length > 0) {
              layerSel.value = d.layers[0]
              layerSel.dispatchEvent(new Event('change', { bubbles: true }))
            }
          }
          if (crsInp && d.default_crs) {
            showChip('[data-auto-crs]', crsInp.value !== d.default_crs)
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

  c.querySelectorAll('[data-k="uri"],[data-k="format"],[data-k="layer"],[data-k="crs"]').forEach(i => {
    i.addEventListener('input', doInspect)
    i.addEventListener('change', doInspect)
  })
  const uriInp = c.querySelector<HTMLInputElement>('[data-k="uri"]')!
  uriInp.addEventListener('input', autoDetectFormat)
  uriInp.addEventListener('change', autoDetectFormat)
  c.querySelectorAll('[data-k="uri"],[data-k="format"]').forEach(i => {
    i.addEventListener('input', doInspectFile)
    i.addEventListener('change', doInspectFile)
  })
  c.querySelector('[data-k="id"]')!.addEventListener('input', doInspect)

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

  const idInp = c.querySelector<HTMLInputElement>('[data-k="id"]')!
  const titleEl = c.querySelector<HTMLElement>('.item-title')!
  const updateTitle = () => {
    const id = idInp.value.trim()
    const fmtVal = fmt.value
    titleEl.textContent = id ? `${id} (${fmtVal})` : ''
  }
  idInp.addEventListener('input', updateTitle)
  fmt.addEventListener('change', updateTitle)
  updateTitle()

  idInp.addEventListener('input', updateIncompleteWarning)
  uriInp.addEventListener('input', updateIncompleteWarning)
  updateIncompleteWarning()

  wireCollapse(c, { headerSel: '.item-head', chevronSel: '.card-chevron', bodySel: '.card-content' })

  createIcons({ icons: { Database, Trash2, Folder, ChevronDown, AlertTriangle } })
  return c
}

import { createIcons, Package, Trash2, ChevronDown, Plus, Copy } from 'lucide'
import { mkEl, refreshIcons, wireCollapse } from '../dom'
import { mutate } from '../history'
import { collectPipelineDef, wireDragReorder } from './pipeline-card'
import { mapRow, type MapRowElement } from './map-row'
import { attachCrsFormatCheck, attachLiveValidation, renderValidationMsg } from '../validation'
import type { Config, MapItem, OutputLayer } from '../types'

// ---- output layer card ----
// One entry in a pipeline's `layers:` list — the same upstream chain, written
// out with its own name/CRS/filter. Add more than one to fan a converged
// chain out into several layers (e.g. matched vs. unmatched) in one pass.
// A layer can also carry its own `mapping:`, which replaces the pipeline's mapping
// for that layer only (engine.py: `layer.mapping or self.p.mapping`).
export function outputLayerCard(ol: Partial<OutputLayer> = {}, syncFn: () => void): HTMLElement {
  const c = mkEl('div', { className: 'card collapsed' })
  c.innerHTML = `
    <div class="item-head" style="cursor:pointer; user-select:none;">
      <span class="tag"><i data-lucide="package" style="width:12px;height:12px;margin-right:2px"></i>layer</span>
      <span class="item-title" style="font-family:var(--mono); font-size:11px; font-weight:600; margin-left:8px; color:var(--ink);"></span>
      <span class="spacer"></span>
      <button class="mini danger ghost" data-del aria-label="Remove this output layer"><i data-lucide="trash-2" style="width:12px;height:12px"></i> remove</button>
      <i data-lucide="chevron-down" class="card-chevron" style="width:14px;height:14px;color:var(--muted);transition:transform 0.2s;margin-left:8px;"></i>
    </div>
    <div class="card-content" style="margin-top:12px;">
      <div class="row">
        <label class="field grow">layer name<input data-k="layer" placeholder="MyLayer"></label>
        <label class="field grow">CRS<input data-k="crs" placeholder="EPSG:25833"></label>
      </div>
      <label class="field" style="margin-top:8px">filter (optional SQL; rows where this is false are excluded from this layer)
        <input data-k="filter" placeholder="county IS NOT NULL">
        <div class="expr-validation-msg" data-filter-validation></div>
      </label>
      <div style="margin-top:10px;padding-top:10px;border-top:1px dashed var(--line)">
        <label style="display:flex;align-items:center;gap:6px;color:var(--ink);font-family:system-ui;cursor:pointer">
          <input type="checkbox" data-ol-custom-mapping style="width:auto;margin:0"> own column mapping for this layer</label>
        <p class="hint" style="margin-top:4px" data-ol-mapping-hint></p>
        <div data-ol-mapping-body style="display:none">
          <div class="ol-mapping"></div>
          <div style="display:flex;gap:6px;margin-top:6px">
            <button class="mini ghost" type="button" data-ol-addcol><i data-lucide="plus" style="width:12px;height:12px"></i> column</button>
            <button class="mini ghost" type="button" data-ol-copy title="Replace this layer's columns with a copy of the pipeline's mapping tab, to edit from there"><i data-lucide="copy" style="width:12px;height:12px"></i> copy pipeline mapping</button>
          </div>
        </div>
      </div>
    </div>`

  c.querySelector('[data-del]')!.addEventListener('click', (e) => {
    e.stopPropagation()
    const name = c.querySelector<HTMLInputElement>('[data-k="layer"]')?.value.trim()
    mutate('remove output layer', { undoToast: `Removed layer${name ? ` "${name}"` : ''}` })
    c.classList.add('slide-out')
    setTimeout(() => { c.remove(); syncFn() }, 250)
  })

  for (const [k, v] of Object.entries(ol)) {
    if (k === 'mapping') continue
    const inp = c.querySelector<HTMLInputElement>(`[data-k="${k}"]`)
    if (inp && v != null) inp.value = String(v)
  }

  c.querySelectorAll('[data-k]').forEach(i => {
    i.addEventListener('input', syncFn)
    i.addEventListener('change', syncFn)
  })

  wireLayerMapping(c, ol.mapping ?? [], syncFn)

  // The engine applies this filter to the step chain's own columns, before this layer's
  // mapping runs (see engine.py's _final_select: `layer.filter` narrows `prev`, the
  // pre-mapping view) — so the throwaway check config uses the pipeline's real steps as-is,
  // not its mapping.
  const filterInp = c.querySelector<HTMLInputElement>('[data-k="filter"]')!
  const filterMsg = c.querySelector<HTMLElement>('[data-filter-validation]')!
  const filterValidation = attachLiveValidation({
    getValue: () => filterInp.value,
    buildPreviewConfig: (draft): Config | null => {
      const card = c.closest('.pipeline-card') as HTMLElement | null
      if (!card) return null
      const pdef = collectPipelineDef(card)
      if (!pdef.base) return null
      return {
        name: 'layer_filter_preview',
        output: 'preview.gpkg',
        pipelines: [{
          ...pdef,
          mapping: [{ to: '_check', expr: `CASE WHEN (${draft}) THEN 1 ELSE 0 END` }],
          layers: [{ layer: 'preview', crs: pdef.working_crs || 'EPSG:25833' }],
        }],
      }
    },
    render: (state, message) => renderValidationMsg(filterMsg, state, message),
  })
  filterInp.addEventListener('input', () => filterValidation.schedule())
  filterInp.addEventListener('blur', () => filterValidation.runNow())

  const layerInp = c.querySelector<HTMLInputElement>('[data-k="layer"]')!
  const crsInp = c.querySelector<HTMLInputElement>('[data-k="crs"]')!
  attachCrsFormatCheck(crsInp)
  const titleEl = c.querySelector<HTMLElement>('.item-title')!
  const updateTitle = () => {
    const name = layerInp.value.trim()
    const crsV = crsInp.value.trim()
    titleEl.textContent = name ? `${name}${crsV ? ' · ' + crsV : ''}` : ''
  }
  layerInp.addEventListener('input', updateTitle)
  crsInp.addEventListener('input', updateTitle)
  updateTitle()

  wireCollapse(c, { headerSel: '.item-head', chevronSel: '.card-chevron', bodySel: '.card-content' })

  createIcons({ icons: { Package, Trash2, ChevronDown, Plus, Copy } })
  return c
}

/** The layer's own mapping, or undefined when it uses the pipeline's. Read by collectPipelineDef. */
export function readLayerMapping(c: HTMLElement): MapItem[] | undefined {
  if (!c.querySelector<HTMLInputElement>('[data-ol-custom-mapping]')?.checked) return undefined
  const rows = [...c.querySelectorAll('.ol-mapping > .map-item')]
    .map(w => (w as MapRowElement)._readMapping?.() ?? null)
    .filter((x): x is MapItem => x !== null)
  // An empty list means "fall back to the pipeline mapping" in the engine, so don't emit it.
  return rows.length ? rows : undefined
}

function wireLayerMapping(c: HTMLElement, initial: MapItem[], syncFn: () => void): void {
  const toggle = c.querySelector<HTMLInputElement>('[data-ol-custom-mapping]')!
  const body = c.querySelector<HTMLElement>('[data-ol-mapping-body]')!
  const hint = c.querySelector<HTMLElement>('[data-ol-mapping-hint]')!
  const listEl = c.querySelector<HTMLElement>('.ol-mapping')!

  const render = () => {
    body.style.display = toggle.checked ? '' : 'none'
    const n = listEl.querySelectorAll(':scope > .map-item').length
    hint.textContent = !toggle.checked
      ? 'Off: this layer gets the columns from the mapping tab.'
      : n
        ? `This layer writes these ${n} column${n !== 1 ? 's' : ''} instead of the mapping tab's. Same upstream columns, same rules.`
        : 'No columns yet — add some, or copy the pipeline mapping to start from. Until then this layer uses the mapping tab.'
  }
  const sync = () => { render(); syncFn() }

  initial.forEach(m => listEl.appendChild(mapRow(m, sync)))
  toggle.checked = initial.length > 0

  toggle.addEventListener('change', () => {
    mutate(toggle.checked ? 'use own layer mapping' : 'use pipeline mapping for layer')
    sync()
  })
  c.querySelector('[data-ol-addcol]')!.addEventListener('click', () => {
    mutate('add layer mapping column')
    const row = mapRow({}, sync)
    listEl.appendChild(row)
    refreshIcons()
    sync()
    setTimeout(() => row.querySelector<HTMLInputElement>('[data-to]')?.focus(), 50)
  })
  c.querySelector('[data-ol-copy]')!.addEventListener('click', () => {
    const card = c.closest('.pipeline-card') as HTMLElement | null
    if (!card) return
    const pipelineMapping = collectPipelineDef(card).mapping
    mutate('copy pipeline mapping to layer')
    listEl.innerHTML = ''
    // structuredClone so codelist objects aren't shared with the pipeline's rows.
    pipelineMapping.forEach(m => listEl.appendChild(mapRow(structuredClone(m), sync)))
    refreshIcons()
    sync()
  })
  wireDragReorder(listEl, '.map-item.dragging', undefined, 'reorder layer mapping', sync)
  render()
}

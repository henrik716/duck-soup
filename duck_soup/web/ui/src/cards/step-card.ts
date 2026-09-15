import {
  createIcons, MapPin, Link, Radar, Maximize2, Crosshair, Scissors, Eraser,
  Layers, GitMerge, ArrowUp, ArrowDown, ArrowRight, X, Trash2, Plus, ChevronDown,
  Filter as FilterIcon, Combine, Camera, GripVertical,
} from 'lucide'
import { mkEl, esc, wireCollapse } from '../dom'
import { META } from '../state'
import { comboField, wireCombos } from '../combo'
import { mutate } from '../history'
import { collectPipelineDef } from './pipeline-card'
import { resolveSchemaScoped, collectAvailableColumnsScoped } from '../schema'
import { attachLiveValidation, attachMembershipCheck, renderValidationMsg } from '../validation'
import type { Step, SpatialJoin, AttributeJoin, NearestNeighbor, IntersectOverlay, Dissolve, Config } from '../types'

// ---- step card ----
const STEP_ICONS: Record<string, string> = {
  spatial_join: 'map-pin',
  attribute_join: 'link',
  nearest_neighbor: 'radar',
  buffer: 'maximize-2',
  centroid: 'crosshair',
  clip: 'scissors',
  erase: 'eraser',
  dissolve: 'layers',
  intersect_overlay: 'git-merge',
  filter: 'filter',
  merge: 'combine',
  snapshot: 'camera',
}

const STEP_LABELS: Record<string, string> = {
  spatial_join: 'spatial join',
  attribute_join: 'attribute join',
  nearest_neighbor: 'nearest neighbor',
  buffer: 'buffer',
  centroid: 'centroid',
  clip: 'clip',
  erase: 'erase',
  dissolve: 'dissolve',
  intersect_overlay: 'intersect overlay',
  filter: 'filter',
  merge: 'merge',
  snapshot: 'snapshot',
}

const STEP_HINTS: Record<string, string> = {
  spatial_join: 'Copies attributes from the first spatially matching feature in the join source.',
  attribute_join: 'Copies attributes from a row in the join source matched on a column value.',
  nearest_neighbor: 'Copies attributes (and optionally the distance) from the closest feature in another source, regardless of overlap.',
  buffer: 'Expands (positive) or shrinks (negative) feature geometry by a fixed distance in working CRS units (e.g. metres). Requires a spatial base source.',
  centroid: 'Replaces each feature\'s geometry with its centroid point. Requires a spatial base source.',
  clip: 'Clips base features to their overlap with a mask source. Features with no overlap are dropped. Both sources must be spatial.',
  erase: 'Removes the overlap with a mask source from base feature geometry. Non-overlapping features are kept unchanged. Both sources must be spatial.',
  dissolve: 'Merges features that share the same group-by column values, combining their geometries via union. All columns not in the group-by list are dropped.',
  intersect_overlay: 'Produces one output row per intersecting (base × source) pair with the intersection as geometry. Row count may increase significantly. Both sources must be spatial.',
  filter: 'Drops rows where this SQL condition is false — e.g. reject rows a prior join left unmatched.',
  merge: 'Appends rows from another source (matched by column name; non-overlapping columns become NULL). Row count adds rather than multiplies.',
  snapshot: 'Names this step\'s current state so a later step can join back against it — fork mid-pipeline (after some processing), not just from a raw source.',
}

export function stepCard(kind: Step['type'], st: Partial<Step> = {}, syncFn: () => void, sourceIds: string[] = []): HTMLElement {
  const c = mkEl('div', { className: 'card collapsed' })
  c.dataset['type'] = kind

  const hasSrc = ['spatial_join', 'attribute_join', 'nearest_neighbor', 'clip', 'erase', 'intersect_overlay', 'merge'].includes(kind)
  const hasPredicate = ['spatial_join', 'clip', 'erase'].includes(kind)
  const hasFields = ['spatial_join', 'attribute_join', 'nearest_neighbor', 'intersect_overlay'].includes(kind)

  // Rendering these as the value rather than leaving the combo blank: collectPipelineDef
  // already falls back to exactly these (`val(c,'predicate') || 'intersects'`), so a blank
  // field always meant "intersects" — it just never said so. The emitted YAML is unchanged.
  const predicateVal = (st as Partial<SpatialJoin>).predicate ?? 'intersects'
  const matchVal = (st as Partial<SpatialJoin>).match ?? 'first'

  let bodyHtml = ''
  if (hasSrc) {
    bodyHtml += `<div class="row" style="margin-top:8px">
      <label class="field grow">source${comboField('data-k="source"', '', sourceIds, '', 'No sources defined yet — add one above')}</label>
      ${hasPredicate ? `<label class="field grow">predicate${comboField('data-k="predicate"', predicateVal, META.predicates)}</label>` : ''}
      ${kind === 'spatial_join' ? `<label class="field grow">match${comboField('data-k="match"', matchVal, ['first', 'all'], 'first')}</label>` : ''}
      ${kind === 'attribute_join' ? `
        <label class="field grow">left (upstream column or SQL literal)${comboField('data-k="left" data-from-list="1"', '', [], "category or 'Embassies'", 'No upstream columns yet — set the base source')}
          <div class="expr-validation-msg" data-left-validation></div></label>
        <label class="field grow">right (column on the join source)${comboField('data-k="right" data-src-col="1"', '', [], '— column —', 'Pick a source for this step first')}</label>` : ''}
    </div>`
    if (kind === 'merge') {
      bodyHtml += `<p class="hint" style="margin-top:8px">${STEP_HINTS['merge']}</p>`
    }
  }
  if (kind === 'filter') {
    bodyHtml += `<div class="row" style="margin-top:8px">
      <label class="field grow">where (SQL boolean expression)<input data-k="where" placeholder="matched_name IS NOT NULL">
        <div class="expr-validation-msg" data-where-validation></div></label>
    </div>`
    bodyHtml += `<p class="hint" style="margin-top:8px">${STEP_HINTS['filter']}</p>`
  }
  if (kind === 'snapshot') {
    bodyHtml += `<div class="row" style="margin-top:8px">
      <label class="field grow">snapshot id (name this step's output; usable as "source" in a later step)<input data-k="id" placeholder="matched_ports"></label>
    </div>`
    bodyHtml += `<p class="hint" style="margin-top:8px">${STEP_HINTS['snapshot']}</p>`
  }
  if (kind === 'nearest_neighbor') {
    bodyHtml += `<div class="row" style="margin-top:8px">
      <label class="field grow">max distance (optional, CRS units)<input data-k="max_distance" type="number" step="any" placeholder="unbounded"></label>
      <label class="field grow">distance field (optional)<input data-k="distance_field" placeholder="distance_m"></label>
    </div>`
  }
  if (kind === 'buffer') {
    bodyHtml += `<div class="row" style="margin-top:8px">
      <label class="field grow">distance (CRS units)<input data-k="distance" type="number" step="any" placeholder="100"></label>
    </div>`
  }
  if (kind === 'centroid') {
    bodyHtml += `<p class="hint" style="margin-top:8px">${STEP_HINTS['centroid']}</p>`
  }
  if (kind === 'dissolve') {
    bodyHtml += `<p class="hint" style="margin-top:8px">⚠ All attributes not listed here are dropped after this step.</p>`
    bodyHtml += `<div style="margin-top:10px">
      <span class="head" style="font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;display:block;margin-bottom:6px">group by columns&nbsp;<span style="font-weight:normal">(empty = dissolve all)</span></span>
      <div data-by-cols></div>
      <button class="mini ghost" data-addby style="margin-top:6px"><i data-lucide="plus" style="width:12px;height:12px"></i> column</button>
    </div>`
  }
  if (hasFields) {
    bodyHtml += `<div style="margin-top:10px">
      <span class="head" style="font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;display:block;margin-bottom:6px">pulled fields&nbsp; source column → output name</span>
      <div data-fields></div>
      <div class="fields-empty" data-fields-empty>Nothing pulled yet — this step will match features but copy no attributes.</div>
      <div style="display:flex;gap:6px;margin-top:6px">
        <button class="mini ghost" data-addfield><i data-lucide="plus" style="width:12px;height:12px"></i> field</button>
        <button class="mini ghost" data-addallfields title="Pull every column of the join source that isn't pulled yet"><i data-lucide="plus" style="width:12px;height:12px"></i> all columns</button>
      </div>
    </div>`
  }

  // Universal: any step can target a named branch (from an earlier snapshot)
  // instead of the main chain. For snapshot itself this means "snapshot from
  // this branch" rather than "operate on this branch".
  const branchLabel = kind === 'snapshot'
    ? 'snapshot from branch (optional; default = main chain)'
    : 'apply to branch (optional; default = main chain)'
  bodyHtml += `<div class="row" style="margin-top:10px;padding-top:10px;border-top:1px dashed var(--line)">
    <label class="field grow">${branchLabel}${comboField('data-k="branch"', '', [], 'main chain')}</label>
  </div>`

  c.innerHTML = `
    <div class="item-head" style="cursor:pointer; user-select:none;">
      <span class="drag-handle" tabindex="0" role="button" aria-label="Reorder this step — drag, or hold Alt and press the up or down arrow"><i data-lucide="grip-vertical" style="width:14px;height:14px"></i></span>
      <span class="tag" title="${STEP_HINTS[kind]}"><i data-lucide="${STEP_ICONS[kind]}" style="width:12px;height:12px;margin-right:2px"></i>${STEP_LABELS[kind]}</span>
      <span class="item-title" style="font-family:var(--mono); font-size:11px; font-weight:600; margin-left:8px; color:var(--ink);"></span>
      <span class="spacer"></span>
      <button class="mini ghost data-step-preview" title="Preview up to this step" aria-label="Preview the data up to and including this step"><i data-lucide="eye" style="width:12px;height:12px"></i></button>
      <button class="mini ghost" data-up title="Move up (Alt+Up)" aria-label="Move this step earlier"><i data-lucide="arrow-up" style="width:12px;height:12px"></i></button>
      <button class="mini ghost" data-down title="Move down (Alt+Down)" aria-label="Move this step later"><i data-lucide="arrow-down" style="width:12px;height:12px"></i></button>
      <button class="mini danger ghost" data-del aria-label="Remove this step"><i data-lucide="trash-2" style="width:12px;height:12px"></i> remove</button>
      <i data-lucide="chevron-down" class="card-chevron" style="width:14px;height:14px;color:var(--muted);transition:transform 0.2s;margin-left:8px;"></i>
    </div>
    <div class="card-content" style="margin-top:12px;">
      <p class="hint" style="margin-bottom:10px">${STEP_HINTS[kind]}</p>
      ${bodyHtml}
    </div>`

  wireCombos(c)

  // Builds a throwaway single-column preview Config scoped to the steps that run *before*
  // this card, mirroring the pipeline state this field's SQL actually executes against —
  // reused by both attribute_join.left and filter.where below. Only called from user
  // interaction (not on hydrate), so loading a saved pipeline with many such steps doesn't
  // fire a burst of /api/preview requests for fields nobody is currently looking at.
  const buildStepFieldPreviewConfig = (fieldExpr: string, wrapBoolean: boolean): Config | null => {
    const card = c.closest('.pipeline-card') as HTMLElement | null
    if (!card) return null
    const pdef = collectPipelineDef(card)
    if (!pdef.base) return null
    const idx = c.parentElement ? Array.from(c.parentElement.children).indexOf(c) : -1
    const priorSteps = idx >= 0 ? pdef.steps.slice(0, idx) : pdef.steps
    const expr = wrapBoolean ? `CASE WHEN (${fieldExpr}) THEN 1 ELSE 0 END` : fieldExpr
    return {
      name: 'field_preview',
      output: 'preview.gpkg',
      pipelines: [{
        ...pdef,
        steps: priorSteps,
        mapping: [{ to: '_check', expr }],
        layers: [{ layer: 'preview', crs: pdef.working_crs || 'EPSG:25833' }],
      }],
    }
  }

  if (kind === 'attribute_join') {
    const leftInp = c.querySelector<HTMLInputElement>('[data-k="left"]')!
    const leftMsg = c.querySelector<HTMLElement>('[data-left-validation]')!
    const leftValidation = attachLiveValidation({
      getValue: () => leftInp.value,
      buildPreviewConfig: draft => buildStepFieldPreviewConfig(draft, false),
      render: (state, message) => renderValidationMsg(leftMsg, state, message),
    })
    leftInp.addEventListener('input', () => leftValidation.schedule())
    leftInp.addEventListener('blur', () => leftValidation.runNow())

    // `right` is a bare column name on the join source, not arbitrary SQL — checking it
    // against the join source's real schema is instant and needs no network round trip.
    // (Deliberately not read back from the combo's own option list: that list re-injects
    // whatever the field currently holds via ensureComboOption, on every keystroke elsewhere
    // in the card, so a bogus value would get "validated" by its own presence a moment later.)
    const rightInp = c.querySelector<HTMLInputElement>('[data-k="right"]')!
    const rightCheck = attachMembershipCheck(rightInp, () => {
      const card = c.closest('.pipeline-card')
      const srcId = c.querySelector<HTMLInputElement>('[data-k="source"]')?.value.trim() || ''
      return card && srcId ? resolveSchemaScoped(card, srcId).map(col => col.name) : []
    })
    const srcInp = c.querySelector<HTMLInputElement>('[data-k="source"]')
    srcInp?.addEventListener('change', () => rightCheck.check())
    srcInp?.addEventListener('input', () => rightCheck.check())
  }

  if (kind === 'filter') {
    const whereInp = c.querySelector<HTMLInputElement>('[data-k="where"]')!
    const whereMsg = c.querySelector<HTMLElement>('[data-where-validation]')!
    const whereValidation = attachLiveValidation({
      getValue: () => whereInp.value,
      buildPreviewConfig: draft => buildStepFieldPreviewConfig(draft, true),
      render: (state, message) => renderValidationMsg(whereMsg, state, message),
    })
    whereInp.addEventListener('input', () => whereValidation.schedule())
    whereInp.addEventListener('blur', () => whereValidation.runNow())
  }

  c.querySelector('.data-step-preview')!.addEventListener('click', (e) => {
    e.stopPropagation()
    const parent = c.parentElement
    if (parent) {
      const siblings = Array.from(parent.querySelectorAll(':scope > .card'))
      const stepIdx = siblings.indexOf(c) + 1
      const plCard = c.closest('.pipeline-card')
      const plCards = Array.from(document.querySelectorAll('.pipeline-card'))
      const pipelineIdx = plCards.indexOf(plCard!)
      c.dispatchEvent(new CustomEvent('preview-step', {
        bubbles: true,
        detail: { stepIdx, pipelineIdx }
      }))
    }
  })

  c.querySelector('[data-del]')!.addEventListener('click', (e) => {
    e.stopPropagation()
    mutate('remove step', { undoToast: `Removed ${STEP_LABELS[kind]} step` })
    c.classList.add('slide-out'); setTimeout(() => { c.remove(); syncFn() }, 250)
  })

  const upBtn = c.querySelector<HTMLButtonElement>('[data-up]')!
  const downBtn = c.querySelector<HTMLButtonElement>('[data-down]')!

  // Steps at the ends of the list used to keep an enabled-looking button that did nothing.
  const updateMoveButtons = () => {
    upBtn.disabled = !c.previousElementSibling
    downBtn.disabled = !c.nextElementSibling
  }
  // Siblings change as steps are added, removed or reordered around this card.
  const observeSiblings = () => {
    if (!c.parentElement) return
    new MutationObserver(updateMoveButtons).observe(c.parentElement, { childList: true })
  }

  const move = (dir: -1 | 1) => {
    const sib = dir === -1 ? c.previousElementSibling : c.nextElementSibling
    if (!sib) return
    mutate(dir === -1 ? 'move step up' : 'move step down')
    if (dir === -1) c.parentNode!.insertBefore(c, sib)
    else c.parentNode!.insertBefore(sib, c)
    c.classList.add('card-swap')
    setTimeout(() => c.classList.remove('card-swap'), 300)
    updateMoveButtons()
    syncFn()
  }

  upBtn.addEventListener('click', e => { e.stopPropagation(); move(-1) })
  downBtn.addEventListener('click', e => { e.stopPropagation(); move(1) })

  // Drag-to-reorder, matching the pattern mapping rows already use — the actual reorder
  // mutation snapshot and insertion-point logic live in the .pl-steps container's
  // dragover/drop handlers (pipeline-card.ts), same as .pl-mapping's. Alt+Arrow above stays
  // as the keyboard-only equivalent.
  const dragHandle = c.querySelector<HTMLElement>('.drag-handle')!
  dragHandle.addEventListener('mousedown', () => { c.draggable = true })
  c.addEventListener('dragstart', () => { c.classList.add('dragging') })
  c.addEventListener('dragend', () => {
    c.draggable = false
    c.classList.remove('dragging')
    c.parentElement?.querySelectorAll('.drop-target-above, .drop-target-below')
      .forEach(el => el.classList.remove('drop-target-above', 'drop-target-below'))
  })

  // Keyboard equivalent for the drag/click-only reorder.
  c.addEventListener('keydown', e => {
    if (!e.altKey) return
    if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); upBtn.focus() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); move(1); downBtn.focus() }
  })

  // fields rows (spatial_join, attribute_join, nearest_neighbor, intersect_overlay)
  //
  // The row reads left-to-right the way the data flows: pick the source column first (the
  // side the UI can actually populate), and the output name is auto-filled from it. The
  // reverse order meant naming an output before you could see what columns existed.
  const fieldsBox = c.querySelector<HTMLElement>('[data-fields]')
  const fieldsEmpty = c.querySelector<HTMLElement>('[data-fields-empty]')
  const updateFieldsEmpty = () => {
    if (fieldsEmpty && fieldsBox) fieldsEmpty.hidden = fieldsBox.children.length > 0
  }
  const addField = (out = '', col = '') => {
    if (!fieldsBox) return
    const r = mkEl('div', { className: 'kv' })
    r.style.marginTop = '6px'
    r.innerHTML = `${comboField('data-fc', col, col ? [col] : [], '— column —', 'Pick a source for this step first')}
      <span class="arrow"><i data-lucide="arrow-right" style="width:12px;height:12px;color:var(--muted)"></i></span>
      <input data-fo placeholder="output_name" value="${esc(out)}">
      <button class="mini danger ghost" data-fdel aria-label="Remove this pulled field"><i data-lucide="x" style="width:12px;height:12px"></i></button>`
    const outInp = r.querySelector<HTMLInputElement>('[data-fo]')!
    const colInp = r.querySelector<HTMLInputElement>('[data-fc]')!

    // Only ever overwrite a name we filled in ourselves — once the user types their own,
    // changing the column must leave it alone. Adding a row with just a column (bulk "all
    // columns") names it after that column up front.
    if (!out && col) {
      outInp.value = col
      outInp.dataset['autofilled'] = '1'
    }
    const autoName = () => {
      const picked = colInp.value.trim()
      if (!picked) return
      if (outInp.value.trim() === '' || outInp.dataset['autofilled'] === '1') {
        outInp.value = picked
        outInp.dataset['autofilled'] = '1'
      }
    }

    r.querySelector('[data-fdel]')!.addEventListener('click', () => {
      r.remove(); updateFieldsEmpty(); syncFn()
    })
    outInp.addEventListener('input', () => { delete outInp.dataset['autofilled']; syncFn() })
    colInp.addEventListener('input', () => { autoName(); syncFn() })
    colInp.addEventListener('change', () => { autoName(); syncFn() })
    wireCombos(r)
    fieldsBox.appendChild(r)
    updateFieldsEmpty()
    createIcons({ icons: { ArrowRight, X, ChevronDown } })
  }
  c.querySelector('[data-addfield]')?.addEventListener('click', (e) => { e.stopPropagation(); addField(); syncFn() })

  // Pulling 20 columns one row at a time was the main tedium here. The schema lives in
  // schema.ts, which transitively imports this module, so rather than close that import
  // cycle the card exposes _addField and asks the pipeline card (which already imports
  // resolveSchemaScoped) to do the lookup — same shape as the preview-step event above.
  ;(c as unknown as { _addField: typeof addField })._addField = addField
  c.querySelector('[data-addallfields]')?.addEventListener('click', (e) => {
    e.stopPropagation()
    c.dispatchEvent(new CustomEvent('pull-all-fields', { bubbles: true }))
  })

  // dissolve by-col rows
  const byColsBox = c.querySelector<HTMLElement>('[data-by-cols]')
  const addByCol = (col = '') => {
    if (!byColsBox) return
    const r = mkEl('div', { className: 'kv' })
    r.style.marginTop = '6px'
    r.innerHTML = `${comboField('data-by-col data-from-list="1"', col, [], 'column_name')}
      <button class="mini danger ghost" data-bydel><i data-lucide="x" style="width:12px;height:12px"></i></button>`
    r.querySelector('[data-bydel]')!.addEventListener('click', () => { r.remove(); syncFn() })
    const inp = r.querySelector<HTMLInputElement>('[data-by-col]')!
    inp.addEventListener('input', syncFn)
    inp.addEventListener('change', syncFn)
    wireCombos(r)
    attachMembershipCheck(inp, () => {
      const card = c.closest('.pipeline-card')
      return card ? collectAvailableColumnsScoped(card) : []
    })
    byColsBox.appendChild(r)
    createIcons({ icons: { X, ChevronDown } })
  }
  c.querySelector('[data-addby]')?.addEventListener('click', (e) => { e.stopPropagation(); addByCol(); syncFn() })

  // hydrate scalar fields
  for (const [k, v] of Object.entries(st)) {
    if (k === 'fields' || k === 'by' || k === 'type') continue
    const inp = c.querySelector<HTMLInputElement | HTMLSelectElement>(`[data-k="${k}"]`)
    if (inp && v != null) inp.value = String(v)
  }
  // hydrate pulled fields
  if (hasFields) {
    Object.entries((st as Partial<SpatialJoin | AttributeJoin | NearestNeighbor | IntersectOverlay>).fields || {}).forEach(([o, col]) => addField(o, col))
    updateFieldsEmpty()
  }
  // hydrate dissolve by-cols
  if (kind === 'dissolve') {
    ;((st as Partial<Dissolve>).by || []).forEach(col => addByCol(col))
  }

  const titleEl = c.querySelector<HTMLElement>('.item-title')!
  const updateTitle = () => {
    const srcEl = c.querySelector<HTMLSelectElement | HTMLInputElement>('[data-k="source"]')
    const srcVal = srcEl ? srcEl.value.trim() : ''
    let text: string
    if (srcVal) {
      text = `→ ${srcVal}`
    } else if (kind === 'buffer') {
      const dist = c.querySelector<HTMLInputElement>('[data-k="distance"]')?.value ?? ''
      text = dist ? `(${dist}m)` : ''
    } else if (kind === 'filter') {
      const w = c.querySelector<HTMLInputElement>('[data-k="where"]')?.value ?? ''
      text = w ? `(${w.length > 30 ? w.slice(0, 30) + '…' : w})` : ''
    } else if (kind === 'snapshot') {
      const idv = c.querySelector<HTMLInputElement>('[data-k="id"]')?.value ?? ''
      text = idv ? `» ${idv}` : ''
    } else {
      text = ''
    }
    const branchVal = c.querySelector<HTMLInputElement>('[data-k="branch"]')?.value.trim() ?? ''
    if (branchVal) text += `${text ? ' ' : ''}@${branchVal}`
    titleEl.textContent = text
  }
  c.querySelector('[data-k="source"]')?.addEventListener('change', updateTitle)
  c.querySelector('[data-k="source"]')?.addEventListener('input', updateTitle)
  c.querySelector('[data-k="distance"]')?.addEventListener('input', updateTitle)
  c.querySelector('[data-k="where"]')?.addEventListener('input', updateTitle)
  c.querySelector('[data-k="branch"]')?.addEventListener('input', updateTitle)
  c.querySelector('[data-k="branch"]')?.addEventListener('change', updateTitle)
  if (kind === 'snapshot') c.querySelector('[data-k="id"]')?.addEventListener('input', updateTitle)
  updateTitle()

  wireCollapse(c, { headerSel: '.item-head', chevronSel: '.card-chevron', bodySel: '.card-content' })

  // The card is appended by the caller, so defer until it has a parent to observe.
  setTimeout(() => { updateMoveButtons(); observeSiblings() }, 0)

  c.querySelector('[data-k="source"]')?.addEventListener('change', syncFn)
  c.querySelectorAll('[data-k]').forEach(i => i.addEventListener('input', syncFn))

  createIcons({ icons: { MapPin, Link, Radar, Maximize2, Crosshair, Scissors, Eraser, Layers, GitMerge, ArrowUp, ArrowDown, Trash2, Plus, ChevronDown, Filter: FilterIcon, Combine, Camera, GripVertical } })
  return c
}

import { mkEl, esc, wireCollapse, helpLink } from '../dom'
import { META } from '../state'
import { comboField, wireCombos } from '../combo'
import { mutate } from '../history'
import { wireDragReorder } from './pipeline-card'
import { mapRow, type MapRowElement } from './map-row'
import { resolveSchemaScoped, collectColumnsBeforeStep } from '../schema'
import { attachLiveValidation, attachMembershipCheck, renderValidationMsg } from '../validation'
import { exprField, exprValue } from '../expr-field'
import { fieldPreviewConfig } from '../field-preview'
import type { Step, SpatialJoin, AttributeJoin, NearestNeighbor, IntersectOverlay, LineOverlay, Dissolve, CombineLines, Attributes, ExprColumn, MapItem, Aggregate } from '../types'
import { renderIcons } from '../icons'
import { attachConditionBuilder } from '../condition-builder'
import { howOptionsHtml } from './aggregate-row'
import { wireMatchWarning } from './match-check'
import { addAggregateRow, droppedColumns, readAggregateRows, type AggregateListContext } from './aggregate-row'
import { showToast } from '../toast'
import { quoteIdent } from '../aggregates'

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
  combine_lines: 'spline',
  intersect_overlay: 'git-merge',
  line_overlay: 'split',
  filter: 'filter',
  merge: 'combine',
  snapshot: 'camera',
  attributes: 'table-properties',
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
  combine_lines: 'combine lines',
  intersect_overlay: 'intersect overlay',
  line_overlay: 'line overlay',
  filter: 'filter',
  merge: 'merge',
  snapshot: 'snapshot',
  attributes: 'update attributes',
}

const STEP_HINTS: Record<string, string> = {
  spatial_join: 'Copies attributes from the spatially matching feature in the join source: the first match, a row per match, or a summary of all the matches (a count, a list, a sum, …).',
  attribute_join: 'Copies attributes from a row in the join source matched on a column value, or summarizes all the matching rows (a count, a list, a sum, …).',
  nearest_neighbor: 'Copies attributes (and optionally the distance) from the closest feature in another source, regardless of overlap.',
  buffer: 'Expands (positive) or shrinks (negative) feature geometry by a fixed distance in working CRS units (e.g. metres). Requires a spatial base source.',
  centroid: 'Replaces each feature\'s geometry with its centroid point. Requires a spatial base source.',
  clip: 'Clips base features to their overlap with a mask source. Features with no overlap are dropped. Both sources must be spatial.',
  erase: 'Removes the overlap with a mask source from base feature geometry. Non-overlapping features are kept unchanged. Both sources must be spatial.',
  dissolve: 'Merges features that share the same group-by values, combining their geometries via union. Group by columns or SQL expressions. Every other column is dropped unless you keep it, with one value per group (most common, sum, all of them, …).',
  combine_lines: 'Joins lines that meet end to end into longer lines: one feature per connected chain of lines that share the group-by values. A chain stops where three or more lines meet. Every other column is dropped unless you keep it, with one value per line (most common, sum, all of them, …). Features that are not lines are kept one per feature.',
  intersect_overlay: 'Produces one output row per intersecting (base × source) pair with the intersection as geometry. Row count may increase significantly. Both sources must be spatial.',
  line_overlay: 'Cuts the lines where they lie on top of the source lines. Shared stretches get the source fields; every other piece is kept with empty fields, so the complete network flows on to the next step. Chain one per overlay dataset.',
  filter: 'Drops rows where this SQL condition is false — e.g. reject rows a prior join left unmatched.',
  merge: 'Appends rows from another source (matched by column name; non-overlapping columns become NULL). Row count adds rather than multiplies.',
  snapshot: 'Names this step\'s current state so a later step can join back against it — fork mid-pipeline (after some processing), not just from a raw source.',
  attributes: 'Adds, changes or removes columns mid-pipeline, with the same rules as the mapping.',
}

const HEAD_STYLE = 'font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;display:block;margin-bottom:6px'
const PLUS_ICON = '<i data-lucide="plus" style="width:12px;height:12px"></i>'

// A join's `match`: what to do when a feature matches several.
const MATCH_OPTIONS = [
  { value: 'first', label: '<span style="font-family:var(--mono);font-weight:600;">first</span> <span data-tag style="font-size:10px;color:var(--muted);">keep one match</span>' },
  { value: 'all', label: '<span style="font-family:var(--mono);font-weight:600;">all</span> <span data-tag style="font-size:10px;color:var(--muted);">a row per match</span>' },
  { value: 'summarize', label: '<span style="font-family:var(--mono);font-weight:600;">summarize</span> <span data-tag style="font-size:10px;color:var(--muted);">count, list, sum … the matches</span>' },
]

const KINDS_WITH_SOURCE = ['spatial_join', 'attribute_join', 'nearest_neighbor', 'clip', 'erase', 'intersect_overlay', 'line_overlay', 'merge']
const KINDS_WITH_PREDICATE = ['spatial_join', 'clip', 'erase']
const KINDS_WITH_FIELDS = ['spatial_join', 'attribute_join', 'nearest_neighbor', 'intersect_overlay', 'line_overlay']
export const KINDS_WITH_REJECTS = ['spatial_join', 'attribute_join', 'nearest_neighbor', 'clip', 'filter']

// What `rejects` catches for each step type (RejectsMixin in config.py).
const REJECTS_HINTS: Record<string, string> = {
  spatial_join: 'Features with no match go to this layer instead of on down the chain with empty fields.',
  attribute_join: 'Rows with no match go to this layer instead of on down the chain with empty fields.',
  nearest_neighbor: 'Features with no neighbour (within max distance) go to this layer instead of on down the chain with empty fields.',
  clip: 'Features clip would drop (outside the mask, or only touching it) go to this layer, unclipped, instead of being discarded.',
  filter: 'Rows the filter drops (where the condition is false or NULL) go to this layer instead of being discarded.',
}

function buildStepBodyHtml(kind: Step['type'], st: Partial<Step>, sourceIds: string[]): string {
  const hasSrc = KINDS_WITH_SOURCE.includes(kind)
  const hasPredicate = KINDS_WITH_PREDICATE.includes(kind)
  const hasFields = KINDS_WITH_FIELDS.includes(kind)

  // Rendering these as the value rather than leaving the combo blank: collectPipelineDef
  // already falls back to exactly these (`val(c,'predicate') || 'intersects'`), so a blank
  // field always meant "intersects" — it just never said so. The emitted YAML is unchanged.
  const predicateVal = (st as Partial<SpatialJoin>).predicate ?? 'intersects'
  const matchVal = (st as Partial<SpatialJoin>).match ?? 'first'
  const onMultipleVal = (st as Partial<SpatialJoin>).on_multiple ?? 'first'

  let bodyHtml = ''
  if (hasSrc) {
    // Top-aligned for attribute_join: left/right carry a validation line under their input,
    // which would lift them above the source field in a bottom-aligned row.
    bodyHtml += `<div class="row" style="margin-top:8px${kind === 'attribute_join' ? ';align-items:flex-start' : ''}">
      <label class="field grow">source${comboField('data-k="source"', '', sourceIds, '', 'No sources defined yet — add one above')}</label>
      ${hasPredicate ? `<label class="field grow">predicate${comboField('data-k="predicate"', predicateVal, META.predicates)}</label>` : ''}
      ${kind === 'spatial_join' ? `<label class="field grow" title="What to do when a feature matches several">match${comboField('data-k="match"', matchVal, MATCH_OPTIONS, 'first')}</label>
        <label class="field grow" data-on-multiple-field title="Which match to keep when a feature matches several — only applies to match: first">when several match${comboField('data-k="on_multiple"', onMultipleVal, [
          { value: 'first', label: '<span style="font-family:var(--mono);font-weight:600;">first</span> <span data-tag style="font-size:10px;color:var(--muted);">join source row order</span>' },
          { value: 'largest_overlap', label: '<span style="font-family:var(--mono);font-weight:600;">largest_overlap</span> <span data-tag style="font-size:10px;color:var(--muted);">biggest intersection area</span>' },
        ], 'first')}</label>` : ''}
      ${kind === 'attribute_join' ? `
        <label class="field grow" title="A column on the running row, or a SQL literal such as 'Embassies'">left column${comboField('data-k="left" data-from-list="1"', '', [], "category or 'Embassies'", 'No upstream columns yet — set the base source')}
          <div class="expr-validation-msg" data-left-validation></div></label>
        <label class="field grow" title="The column on the join source to match against">right column${comboField('data-k="right" data-src-col="1"', '', [], '— column —', 'Pick a source for this step first')}
          <div class="expr-validation-msg" aria-hidden="true"></div></label>
        <label class="field grow" title="What to do when a row matches several rows of the source">match${comboField('data-k="match"', matchVal, MATCH_OPTIONS.filter(o => o.value !== 'all'), 'first')}
          <div class="expr-validation-msg" aria-hidden="true"></div></label>` : ''}
    </div>
    ${kind === 'spatial_join' || kind === 'attribute_join' ? '<div class="expr-validation-msg" data-match-warning></div>' : ''}`
    if (kind === 'merge') {
      bodyHtml += `<p class="hint" style="margin-top:8px">${STEP_HINTS['merge']}</p>`
    }
  }
  if (kind === 'filter') {
    bodyHtml += `<div class="row" style="margin-top:8px">
      <label class="field grow">condition<input data-k="where" placeholder="matched_name IS NOT NULL">
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
  if (kind === 'line_overlay') {
    bodyHtml += `<div class="row" style="margin-top:8px">
      <label class="field grow">tolerance (optional, CRS units)<input data-k="tolerance" type="number" step="any" min="0" placeholder="none — lines must match exactly"></label>
    </div>`
    bodyHtml += `<p class="hint" style="margin-top:8px">${STEP_HINTS['line_overlay']}</p>`
  }
  if (kind === 'buffer') {
    bodyHtml += `<div class="row" style="margin-top:8px">
      <label class="field grow">distance (CRS units)<input data-k="distance" type="number" step="any" placeholder="100"></label>
    </div>`
  }
  if (kind === 'centroid') {
    bodyHtml += `<p class="hint" style="margin-top:8px">${STEP_HINTS['centroid']}</p>`
  }
  if (kind === 'dissolve' || kind === 'combine_lines') {
    const per = kind === 'dissolve' ? 'group' : 'line'
    const empty = kind === 'dissolve' ? 'dissolve all' : 'combine all lines'
    if (kind === 'combine_lines') {
      const preserve = (st as Partial<CombineLines>).preserve_direction !== false
      bodyHtml += `<div class="row" style="margin-top:8px">
        <label class="field-check" title="Unticked, a line may be reversed to join one that runs the other way. Leave it ticked when direction matters (one-way streets, measures).">
          <input type="checkbox" data-k="preserve_direction" ${preserve ? 'checked' : ''}> only join lines running the same way</label>
      </div>`
    }
    bodyHtml += `<details class="st-pool" open style="margin-top:10px">
      <summary class="head" style="${HEAD_STYLE};cursor:pointer">columns reaching this step&nbsp;<span style="font-weight:normal;text-transform:none;letter-spacing:0">(click one to keep it · <b>by</b> to group by it · struck through = dropped)</span></summary>
      <div class="st-pool-list"></div>
    </details>
    <div style="margin-top:10px">
      <span class="head" style="${HEAD_STYLE}">group by&nbsp;<span style="font-weight:normal">(a column, or a SQL expression with a name · empty = ${empty})</span></span>
      <div data-by-cols></div>
      <button class="mini ghost" data-addby style="margin-top:6px">${PLUS_ICON} key</button>
    </div>
    <div style="margin-top:10px">
      <span class="head" style="${HEAD_STYLE}">other columns&nbsp;<span style="font-weight:normal">(one value per ${per})</span></span>
      <p class="hint" style="margin:0 0 6px">A column you don't group by is dropped unless you keep it here. Pick how its values are combined: one value, the most common, all of them, a sum, …</p>
      <div data-aggs></div>
      <div style="display:flex;gap:6px;margin-top:6px">
        <button class="mini ghost" data-addagg>${PLUS_ICON} column</button>
        <button class="mini ghost" data-keepall title="Keep every dropped column, as one value each">${PLUS_ICON} keep all</button>
        <button class="mini ghost" data-addcount title="How many features each ${per} was made from">${PLUS_ICON} count</button>
      </div>
    </div>`
  }
  if (kind === 'attributes') {
    bodyHtml += `<p class="hint st-keep-note" style="margin-top:8px">✓ Every column keeps going to the next step as it is. List only the ones to add or change, and drop the ones you don't want.</p>`
    bodyHtml += `<details class="st-pool" open style="margin-top:10px">
      <summary class="head" style="${HEAD_STYLE};cursor:pointer">available here&nbsp;<span style="font-weight:normal;text-transform:none;letter-spacing:0">(all kept unless dropped · click one to change it, × to drop it)</span></summary>
      <div class="st-pool-list"></div>
    </details>
    <div style="margin-top:10px">
      <span class="head" style="${HEAD_STYLE}">add or change columns&nbsp;<span style="font-weight:normal">(in order · each can use the ones above it)</span></span>
      <div class="st-columns"></div>
      <div class="fields-empty" data-columns-empty>Nothing changed yet. Add a column here only to create one or change its value.</div>
      <button class="mini ghost" data-addcolumn style="margin-top:6px">${PLUS_ICON} column</button>
    </div>
    <div style="margin-top:10px">
      <span class="head" style="${HEAD_STYLE}">drop columns</span>
      <div data-drop-cols></div>
      <button class="mini ghost" data-adddrop style="margin-top:6px">${PLUS_ICON} column</button>
    </div>`
  }
  if (hasFields) {
    bodyHtml += `<div style="margin-top:10px">
      <span class="head" style="font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;display:block;margin-bottom:6px">pulled fields&nbsp; source column → output name</span>
      <p class="hint f-how" style="margin:0 0 6px">Each field combines the values of all the matches: how many there are, all of them, the most common, a sum, …</p>
      <div data-fields></div>
      <div class="fields-empty" data-fields-empty>Nothing pulled yet — this step will match features but copy no attributes.</div>
      <div style="display:flex;gap:6px;margin-top:6px">
        <button class="mini ghost" data-addfield><i data-lucide="plus" style="width:12px;height:12px"></i> field</button>
        <button class="mini ghost" data-addallfields title="Pull every column of the join source that isn't pulled yet"><i data-lucide="plus" style="width:12px;height:12px"></i> all columns</button>
      </div>
    </div>`
  }

  if (KINDS_WITH_REJECTS.includes(kind)) {
    bodyHtml += `<div class="row" style="margin-top:10px">
      <label class="field grow" title="${REJECTS_HINTS[kind]} The layer is written to the output with every column as it is at this step (no mapping), in the first output layer's CRS.">rejects → output layer (optional)<input data-k="rejects" placeholder="none"></label>
    </div>
    <p class="hint" style="margin-top:4px">${REJECTS_HINTS[kind]}</p>`
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
  return bodyHtml
}

function buildStepCardMarkup(kind: Step['type'], bodyHtml: string): string {
  return `
    <div class="item-head" style="cursor:pointer; user-select:none;">
      <span class="drag-handle" tabindex="0" role="button" aria-label="Reorder this step — drag, or hold Alt and press the up or down arrow"><i data-lucide="grip-vertical" style="width:14px;height:14px"></i></span>
      <span class="tag" title="${STEP_HINTS[kind]}"><i data-lucide="${STEP_ICONS[kind]}" style="width:12px;height:12px;margin-right:2px"></i>${STEP_LABELS[kind]}</span>
      <span class="item-title" style="font-family:var(--mono); font-size:11px; font-weight:600; margin-left:8px; color:var(--ink);"></span>
      <span class="spacer"></span>
      ${helpLink('reference/steps/', kind, `the ${STEP_LABELS[kind]} step`)}
      <button class="mini ghost data-step-preview" title="Preview the result of this step" aria-label="Preview the data after this step and all steps before it"><i data-lucide="eye" style="width:12px;height:12px"></i></button>
      <button class="mini ghost" data-step-sql title="Show the SQL this step runs" aria-label="Show the SQL this step runs"><i data-lucide="code" style="width:12px;height:12px"></i></button>
      <button class="mini ghost" data-up title="Move up (Alt+Up)" aria-label="Move this step earlier"><i data-lucide="arrow-up" style="width:12px;height:12px"></i></button>
      <button class="mini ghost" data-down title="Move down (Alt+Down)" aria-label="Move this step later"><i data-lucide="arrow-down" style="width:12px;height:12px"></i></button>
      <button class="mini danger ghost" data-del aria-label="Remove this step"><i data-lucide="trash-2" style="width:12px;height:12px"></i> remove</button>
      <i data-lucide="chevron-down" class="card-chevron" style="width:14px;height:14px;color:var(--muted);transition:transform 0.2s;margin-left:8px;"></i>
    </div>
    <div class="card-content" style="margin-top:12px;">
      <p class="hint" style="margin-bottom:10px">${STEP_HINTS[kind]}</p>
      ${bodyHtml}
    </div>`
}

export function stepCard(kind: Step['type'], st: Partial<Step> = {}, syncFn: () => void, sourceIds: string[] = []): HTMLElement {
  const c = mkEl('div', { className: 'card collapsed' })
  c.dataset['type'] = kind
  const hasFields = KINDS_WITH_FIELDS.includes(kind)

  c.innerHTML = buildStepCardMarkup(kind, buildStepBodyHtml(kind, st, sourceIds))

  wireCombos(c)

  wireFieldValidation(c, kind)

  c.querySelector('.data-step-preview')!.addEventListener('click', (e) => {
    e.stopPropagation()
    const loc = stepLocation(c)
    if (loc) c.dispatchEvent(new CustomEvent('preview-step', { bubbles: true, detail: loc }))
  })

  c.querySelector('[data-step-sql]')!.addEventListener('click', (e) => {
    e.stopPropagation()
    const loc = stepLocation(c)
    if (loc) c.dispatchEvent(new CustomEvent('show-step-sql', { bubbles: true, detail: loc }))
  })

  c.querySelector('[data-del]')!.addEventListener('click', (e) => {
    e.stopPropagation()
    mutate('remove step', { undoToast: `Removed ${STEP_LABELS[kind]} step` })
    c.classList.add('slide-out'); setTimeout(() => { c.remove(); syncFn() }, 250)
  })

  wireStepReorder(c, syncFn)

  const { addField, updateFieldsEmpty } = wireFieldsList(c, syncFn)
  const { addByKey, addAggregate } = wireDissolveLists(c, kind, syncFn)
  const { addColumn, addDropCol } = wireAttributeLists(c, syncFn)

  // hydrate scalar fields
  for (const [k, v] of Object.entries(st)) {
    if (['fields', 'by', 'aggregates', 'columns', 'drop', 'type', 'preserve_direction'].includes(k)) continue
    const inp = c.querySelector<HTMLInputElement | HTMLSelectElement>(`[data-k="${k}"]`)
    if (inp && v != null) inp.value = String(v)
  }
  // hydrate pulled fields
  if (hasFields) {
    Object.entries((st as Partial<SpatialJoin | AttributeJoin | NearestNeighbor | IntersectOverlay | LineOverlay>).fields || {}).forEach(([o, spec]) =>
      typeof spec === 'string' ? addField(o, spec) : addField(o, spec.from ?? '', spec.expr ? 'sql' : spec.how ?? 'all', spec.expr ?? ''))
    updateFieldsEmpty()
  }
  // hydrate dissolve/combine_lines keys and aggregates, attribute columns and drops
  if (kind === 'dissolve' || kind === 'combine_lines') {
    ;((st as Partial<Dissolve | CombineLines>).by || []).forEach(key => addByKey(key))
    ;((st as Partial<Dissolve | CombineLines>).aggregates || []).forEach(agg => addAggregate(agg))
  }
  if (kind === 'attributes') {
    ;((st as Partial<Attributes>).columns || []).forEach(m => addColumn(m))
    ;((st as Partial<Attributes>).drop || []).forEach(col => addDropCol(col))
  }

  // After hydrating: the builder shows the loaded condition as rows when it can.
  if (kind === 'filter') {
    attachConditionBuilder(c.querySelector<HTMLInputElement>('[data-k="where"]')!, () => {
      const card = c.closest('.pipeline-card')
      return card ? collectColumnsBeforeStep(card, c) : []
    })
  }

  // on_multiple is a tie-break for match: first — with match: all every match is kept,
  // so there's nothing to choose between and the field would only mislead.
  if (kind === 'spatial_join') {
    const matchInp = c.querySelector<HTMLInputElement>('[data-k="match"]')!
    const onMultipleField = c.querySelector<HTMLElement>('[data-on-multiple-field]')!
    const syncOnMultiple = () => { onMultipleField.style.display = ['all', 'summarize'].includes(matchInp.value.trim()) ? 'none' : '' }
    matchInp.addEventListener('input', syncOnMultiple)
    matchInp.addEventListener('change', syncOnMultiple)
    syncOnMultiple()
  }

  // match: summarize turns each pulled field into "how to combine the matches" (CSS shows the
  // rows' how dropdowns on .summarizing), and match: first gets a warning when it drops matches.
  if (kind === 'spatial_join' || kind === 'attribute_join') {
    const matchInp = c.querySelector<HTMLInputElement>('[data-k="match"]')!
    const syncSummarizing = () => c.classList.toggle('summarizing', matchInp.value.trim() === 'summarize')
    matchInp.addEventListener('input', syncSummarizing)
    matchInp.addEventListener('change', syncSummarizing)
    syncSummarizing()
    wireMatchWarning(c, kind)
  }

  wireStepTitle(c, kind)

  wireCollapse(c, { headerSel: '.item-head', chevronSel: '.card-chevron', bodySel: '.card-content' })

  c.querySelector('[data-k="source"]')?.addEventListener('change', syncFn)
  c.querySelectorAll('[data-k]').forEach(i => i.addEventListener('input', syncFn))

  renderIcons(c)
  return c
}

/** This step card's 1-based step number and its pipeline's index, as the preview and SQL
 *  events carry them. */
function stepLocation(c: HTMLElement): { stepIdx: number; pipelineIdx: number } | null {
  const parent = c.parentElement
  const plCard = c.closest('.pipeline-card')
  if (!parent || !plCard) return null
  const stepIdx = Array.from(parent.querySelectorAll(':scope > .card')).indexOf(c) + 1
  const pipelineIdx = Array.from(document.querySelectorAll('.pipeline-card')).indexOf(plCard)
  return { stepIdx, pipelineIdx }
}

// A throwaway single-column preview Config over the steps that run *before* this card, the
// rows this field's SQL actually executes against — used by attribute_join.left and
// filter.where. Only called from user interaction (not on hydrate), so loading a saved
// pipeline with many such steps doesn't fire a burst of /api/preview requests for fields
// nobody is currently looking at.
function buildStepFieldPreviewConfig(c: HTMLElement, fieldExpr: string, wrapBoolean: boolean) {
  const expr = wrapBoolean ? `CASE WHEN (${fieldExpr}) THEN 1 ELSE 0 END` : fieldExpr
  return fieldPreviewConfig(c, [{ to: '_check', expr }])
}

function wireFieldValidation(c: HTMLElement, kind: Step['type']): void {
  if (kind === 'attribute_join') {
    const leftInp = c.querySelector<HTMLInputElement>('[data-k="left"]')!
    const leftMsg = c.querySelector<HTMLElement>('[data-left-validation]')!
    const leftValidation = attachLiveValidation({
      getValue: () => leftInp.value,
      buildPreviewConfig: draft => buildStepFieldPreviewConfig(c, draft, false),
      render: (state, message) => renderValidationMsg(leftMsg, state, message),
    })
    leftInp.addEventListener('input', () => leftValidation.schedule())
    leftInp.addEventListener('blur', () => leftValidation.runNow())
    // `left` is SQL, so a column picked from the list whose name isn't a bare identifier
    // (`NET.VEGLENKESEKVENSID` would read as table NET's column) goes in double-quoted.
    leftInp.addEventListener('change', () => {
      const v = leftInp.value.trim()
      const scope = c.closest('.pipeline-card')
      if (!v || /^[A-Za-z_][A-Za-z0-9_]*$/.test(v) || !scope) return
      if (!collectColumnsBeforeStep(scope, c).some(col => col.name === v)) return
      leftInp.value = quoteIdent(v)
      leftInp.dispatchEvent(new Event('input', { bubbles: true }))
    })

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
      buildPreviewConfig: draft => buildStepFieldPreviewConfig(c, draft, true),
      render: (state, message) => renderValidationMsg(whereMsg, state, message),
    })
    whereInp.addEventListener('input', () => whereValidation.schedule())
    whereInp.addEventListener('blur', () => whereValidation.runNow())
  }
}

function wireStepReorder(c: HTMLElement, syncFn: () => void): void {
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
  // dragover/drop handlers (pipeline-card.ts), same as .pl-mapping's. Alt+Arrow below stays
  // as the keyboard-only equivalent.
  const dragHandle = c.querySelector<HTMLElement>('.drag-handle')!
  dragHandle.addEventListener('mousedown', () => { c.draggable = true })
  // Only the card's own drag: an attributes step's column rows are draggable too, and their
  // drag events bubble up here.
  c.addEventListener('dragstart', e => { if (e.target === c) c.classList.add('dragging') })
  c.addEventListener('dragend', e => {
    if (e.target !== c) return
    c.draggable = false
    c.classList.remove('dragging')
    c.parentElement?.querySelectorAll('.drop-target-above, .drop-target-below')
      .forEach(el => el.classList.remove('drop-target-above', 'drop-target-below'))
  })

  // Keyboard equivalent for the drag/click-only reorder. Not from inside a column row, whose
  // Alt+Arrow moves the row (map-row.ts).
  c.addEventListener('keydown', e => {
    if (!e.altKey || (e.target as Element).closest('.map-item')) return
    if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); upBtn.focus() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); move(1); downBtn.focus() }
  })

  // The card is appended by the caller, so defer until it has a parent to observe.
  setTimeout(() => { updateMoveButtons(); observeSiblings() }, 0)
}

// fields rows (spatial_join, attribute_join, nearest_neighbor, intersect_overlay)
//
// The row reads left-to-right the way the data flows: pick the source column first (the
// side the UI can actually populate), and the output name is auto-filled from it. The
// reverse order meant naming an output before you could see what columns existed.
function wireFieldsList(c: HTMLElement, syncFn: () => void) {
  const fieldsBox = c.querySelector<HTMLElement>('[data-fields]')
  const fieldsEmpty = c.querySelector<HTMLElement>('[data-fields-empty]')
  const updateFieldsEmpty = () => {
    if (fieldsEmpty && fieldsBox) fieldsEmpty.hidden = fieldsBox.children.length > 0
  }
  // `how` and `expr` are for match: summarize (the row's how dropdown and SQL field, which
  // only show then): how to combine the column's values over a feature's matches.
  const addField = (out = '', col = '', how = '', expr = '') => {
    if (!fieldsBox) return
    const r = mkEl('div', { className: 'kv' })
    r.style.marginTop = '6px'
    const initialHow = how || 'all'
    r.dataset['how'] = initialHow
    r.innerHTML = `<select data-fhow class="f-how" title="How to combine the matches' values" style="flex:0 0 22%">${howOptionsHtml(initialHow)}<option value="sql"${initialHow === 'sql' ? ' selected' : ''}>SQL…</option></select>
      <span class="arrow f-how f-of">of</span>
      ${comboField('data-fc', col, col ? [col] : [], '— column —', 'Pick a source for this step first')}
      <input data-fexpr class="f-expr" placeholder="sum(population)" value="${esc(expr)}">
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
    const howSel = r.querySelector<HTMLSelectElement>('[data-fhow]')!
    howSel.addEventListener('change', () => {
      r.dataset['how'] = howSel.value
      if (howSel.value === 'count' && !outInp.value.trim()) { outInp.value = 'count'; outInp.dataset['autofilled'] = '1' }
      syncFn()
    })
    r.querySelector('[data-fexpr]')!.addEventListener('input', syncFn)
    colInp.addEventListener('input', () => { autoName(); syncFn() })
    colInp.addEventListener('change', () => { autoName(); syncFn() })
    wireCombos(r)
    fieldsBox.appendChild(r)
    updateFieldsEmpty()
    renderIcons(r)
  }
  c.querySelector('[data-addfield]')?.addEventListener('click', (e) => { e.stopPropagation(); addField(); syncFn() })

  // Pulling 20 columns one row at a time was the main tedium here. The schema lives in
  // schema.ts, which transitively imports this module, so rather than close that import
  // cycle the card exposes _addField and asks the pipeline card (which already imports
  // resolveSchemaScoped) to do the lookup — same shape as the preview-step event.
  ;(c as unknown as { _addField: typeof addField })._addField = addField
  c.querySelector('[data-addallfields]')?.addEventListener('click', (e) => {
    e.stopPropagation()
    c.dispatchEvent(new CustomEvent('pull-all-fields', { bubbles: true }))
  })

  return { addField, updateFieldsEmpty }
}

// dissolve, combine_lines: group keys (a column, or an expression with a name) and aggregates
function wireDissolveLists(c: HTMLElement, kind: Step['type'], syncFn: () => void) {
  const byBox = c.querySelector<HTMLElement>('[data-by-cols]')
  // Only what reaches this step: a later step's columns don't exist yet here.
  const columns = () => {
    const card = c.closest('.pipeline-card')
    return card ? collectColumnsBeforeStep(card, c) : []
  }

  const addByKey = (key: string | ExprColumn = '') => {
    if (!byBox) return
    const r = mkEl('div', { className: 'kv' })
    r.dataset['byRow'] = ''
    r.style.marginTop = '6px'
    const isExpr = typeof key !== 'string'
    let check: { check: () => void } | undefined
    const field = exprField({
      value: isExpr ? key.expr : key,
      placeholder: 'column, or SQL expression',
      attrs: 'data-by-col',
      onChange: () => { check?.check(); syncFn() },
      drawer: () => ({
        columns: columns(),
        buildPreviewConfig: draft => fieldPreviewConfig(c, [{ to: '_expr_preview', expr: draft }]),
      }),
    })
    r.appendChild(field)
    r.insertAdjacentHTML('beforeend', `<span class="arrow" title="An expression key needs a name">as</span>
      <input data-by-as placeholder="name (for an expression)" value="${esc(isExpr ? key.to : '')}" style="flex:0 0 32%">
      <button class="mini danger ghost" data-bydel aria-label="Remove this group key"><i data-lucide="x" style="width:12px;height:12px"></i></button>`)
    const inp = field.querySelector<HTMLInputElement>('[data-by-col]')!
    const asInp = r.querySelector<HTMLInputElement>('[data-by-as]')!
    // Only a key without a name is a plain column to look up; with one, it's an expression.
    check = attachMembershipCheck(inp, () => {
      const card = c.closest('.pipeline-card')
      return card && !asInp.value.trim() ? collectColumnsBeforeStep(card, c).map(col => col.name) : []
    })
    asInp.addEventListener('input', () => { check?.check(); syncFn() })
    r.querySelector('[data-bydel]')!.addEventListener('click', () => { r.remove(); syncFn() })
    byBox.appendChild(r)
    renderIcons(r)
  }

  const ctx: AggregateListContext = {
    card: c, kind: kind === 'combine_lines' ? 'combine_lines' : 'dissolve', byKeys: () => readByKeys(c),
    syncFn,
  }
  const addAggregate = (agg: Partial<Aggregate> = {}) => addAggregateRow(ctx, agg)

  // The "reaching this step" list (schema.ts renderAggregatePool) calls these.
  const keep = (names: string[]) => {
    if (!names.length) { showToast('Every column reaching this step is already kept', 'info'); return }
    mutate(names.length > 1 ? 'keep all columns' : 'keep column')
    names.forEach(from => addAggregate({ from, how: 'any' }))
    ctx.syncFn()
  }
  const actions = c as HTMLElement & { _aggKeep?: (name: string) => void; _aggGroup?: (name: string) => void }
  actions._aggGroup = name => {
    const lower = name.toLowerCase()
    if (readByKeys(c).some(k => typeof k === 'string' && k.toLowerCase() === lower)) { showToast(`Already grouped by ${name}`, 'info'); return }
    mutate('group by column')
    // A key is kept as it is: a row keeping the same column goes.
    c.querySelectorAll<HTMLElement>('[data-agg-row][data-mode="easy"]').forEach(r => {
      if (r.querySelector<HTMLInputElement>('[data-agg-from]')!.value.trim().toLowerCase() === lower) r.remove()
    })
    addByKey(name)
    ctx.syncFn()
  }
  actions._aggKeep = name => {
    if (!droppedColumns(c).some(n => n.toLowerCase() === name.toLowerCase())) { showToast(`${name} is already kept`, 'info'); return }
    keep([name])
  }
  c.querySelector('[data-keepall]')?.addEventListener('click', e => { e.stopPropagation(); keep(droppedColumns(c)) })
  c.querySelector('[data-addcount]')?.addEventListener('click', e => {
    e.stopPropagation()
    mutate('add count')
    addAggregate({ to: 'count', how: 'count' })
    ctx.syncFn()
  })

  c.querySelector('[data-addby]')?.addEventListener('click', e => { e.stopPropagation(); addByKey(); syncFn() })
  c.querySelector('[data-addagg]')?.addEventListener('click', e => { e.stopPropagation(); addAggregate({ how: 'any' }); ctx.syncFn() })
  return { addByKey, addAggregate }
}

/** A dissolve or combine_lines card's group keys: a plain column name, or `{to, expr}` once it has a name. */
export function readByKeys(c: Element): (string | ExprColumn)[] {
  return [...c.querySelectorAll('[data-by-row]')].flatMap(r => {
    const expr = exprValue(r.querySelector<HTMLInputElement>('[data-by-col]')!).trim()
    const to = r.querySelector<HTMLInputElement>('[data-by-as]')!.value.trim()
    if (!expr) return []
    return [to ? { to, expr } : expr]
  })
}

/** A dissolve or combine_lines card's aggregates (rows still being filled in left out). */
export const readAggregates = readAggregateRows

/** An attributes card's set columns and drops. */
export function readAttributes(c: Element): { columns: MapItem[]; drop: string[] } {
  const columns = [...c.querySelectorAll('.st-columns > .map-item')]
    .map(w => (w as MapRowElement)._readMapping?.() ?? null)
    .filter((x): x is MapItem => x !== null)
  const drop = [...c.querySelectorAll<HTMLInputElement>('[data-drop-col]')]
    .map(i => i.value.trim()).filter(Boolean)
  return { columns, drop }
}

// attributes: mapping rows that set columns, and columns to drop
function wireAttributeLists(c: HTMLElement, syncFn: () => void) {
  const listEl = c.querySelector<HTMLElement>('.st-columns')
  const emptyEl = c.querySelector<HTMLElement>('[data-columns-empty]')
  const dropBox = c.querySelector<HTMLElement>('[data-drop-cols]')
  const updateEmpty = () => { if (emptyEl && listEl) emptyEl.hidden = listEl.children.length > 0 }
  const sync = () => { updateEmpty(); syncFn() }

  const addColumn = (m: Partial<MapItem> = {}) => {
    if (!listEl) return null
    const row: MapRowElement = mapRow(m, sync, '0', {
      toCombo: true,
      // The steps before this one, then this step with only the columns above the row, which
      // an expression can use.
      previewConfig: draft => {
        const rows = [...listEl.querySelectorAll<HTMLElement>(':scope > .map-item')]
        const above = rows.slice(0, rows.indexOf(row))
          .map(w => (w as MapRowElement)._readMapping?.() ?? null)
          .filter((x): x is MapItem => x !== null)
        return fieldPreviewConfig(c, [{ to: '_expr_preview', expr: draft }], { type: 'attributes', columns: above })
      },
    })
    listEl.appendChild(row)
    updateEmpty()
    return row
  }

  const addDropCol = (col = '') => {
    if (!dropBox) return
    const r = mkEl('div', { className: 'kv' })
    r.style.marginTop = '6px'
    r.innerHTML = `${comboField('data-drop-col data-from-list="1"', col, [], 'column_name')}
      <button class="mini danger ghost" data-dropdel aria-label="Keep this column"><i data-lucide="x" style="width:12px;height:12px"></i></button>`
    r.querySelector('[data-dropdel]')!.addEventListener('click', () => { r.remove(); syncFn() })
    const inp = r.querySelector<HTMLInputElement>('[data-drop-col]')!
    inp.addEventListener('input', syncFn)
    inp.addEventListener('change', syncFn)
    wireCombos(r)
    attachMembershipCheck(inp, () => {
      const card = c.closest('.pipeline-card')
      return card ? collectColumnsBeforeStep(card, c).map(col => col.name) : []
    })
    dropBox.appendChild(r)
    renderIcons(r)
  }

  c.querySelector('[data-addcolumn]')?.addEventListener('click', e => {
    e.stopPropagation()
    mutate('add attribute column')
    const row = addColumn()
    renderIcons(c)
    sync()
    setTimeout(() => row?.querySelector<HTMLInputElement>('[data-to]')?.focus(), 50)
  })
  c.querySelector('[data-adddrop]')?.addEventListener('click', e => { e.stopPropagation(); addDropCol(); syncFn() })

  // The "available here" list (schema.ts renderAttributesPool) calls these.
  const has = (sel: string, name: string) => [...c.querySelectorAll<HTMLInputElement>(sel)]
    .some(i => i.value.trim().toLowerCase() === name.toLowerCase())
  const actions = c as HTMLElement & { _poolSet?: (name: string) => void; _poolDrop?: (name: string) => void }
  actions._poolSet = name => {
    if (has('.st-columns > .map-item [data-to]', name)) { showToast(`${name} is already set in this step`, 'info'); return }
    mutate('set attribute column')
    addColumn({ to: name, from: name })
    renderIcons(c)
    sync()
  }
  actions._poolDrop = name => {
    if (has('[data-drop-col]', name)) { showToast(`${name} is already dropped`, 'info'); return }
    mutate('drop attribute column')
    addDropCol(name)
    syncFn()
  }
  if (listEl) wireDragReorder(listEl, '.map-item.dragging', undefined, 'reorder attribute columns', sync)
  updateEmpty()
  return { addColumn, addDropCol }
}

function wireStepTitle(c: HTMLElement, kind: Step['type']): void {
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
    } else if (kind === 'attributes') {
      const { columns, drop } = readAttributes(c)
      text = [columns.length ? `+${columns.length}` : '', drop.length ? `−${drop.length}` : ''].filter(Boolean).join(' · ')
    } else if (kind === 'dissolve' || kind === 'combine_lines') {
      const keys = readByKeys(c).map(k => typeof k === 'string' ? k : k.to)
      const aggs = readAggregates(c).length
      text = [keys.length ? `by ${keys.join(', ')}` : '', aggs ? `Σ ${aggs}` : ''].filter(Boolean).join(' · ')
    } else {
      text = ''
    }
    const branchVal = c.querySelector<HTMLInputElement>('[data-k="branch"]')?.value.trim() ?? ''
    if (branchVal) text += `${text ? ' ' : ''}@${branchVal}`
    const rejectsVal = c.querySelector<HTMLInputElement>('[data-k="rejects"]')?.value.trim() ?? ''
    if (rejectsVal) text += `${text ? ' ' : ''}⤷ ${rejectsVal}`
    titleEl.textContent = text
  }
  c.querySelector('[data-k="source"]')?.addEventListener('change', updateTitle)
  c.querySelector('[data-k="source"]')?.addEventListener('input', updateTitle)
  c.querySelector('[data-k="distance"]')?.addEventListener('input', updateTitle)
  c.querySelector('[data-k="where"]')?.addEventListener('input', updateTitle)
  c.querySelector('[data-k="branch"]')?.addEventListener('input', updateTitle)
  c.querySelector('[data-k="branch"]')?.addEventListener('change', updateTitle)
  c.querySelector('[data-k="rejects"]')?.addEventListener('input', updateTitle)
  if (kind === 'snapshot') c.querySelector('[data-k="id"]')?.addEventListener('input', updateTitle)
  // List rows come and go, each with several fields: refresh on any edit or row change.
  if (kind === 'attributes' || kind === 'dissolve' || kind === 'combine_lines') {
    c.addEventListener('input', updateTitle)
    c.addEventListener('change', updateTitle)
    new MutationObserver(updateTitle).observe(c.querySelector('.card-content')!, { childList: true, subtree: true })
  }
  updateTitle()
}

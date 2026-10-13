import { mkEl, esc } from '../dom'
import { META } from '../state'
import { comboField, wireCombos } from '../combo'
import { mutate } from '../history'
import { aggregateColumnStates, collectColumnsBeforeStep, type AvailableColumnDetail } from '../schema'
import { attachMembershipCheck } from '../validation'
import { exprField, exprValue } from '../expr-field'
import { AGGREGATE_SNIPPETS } from '../expr-snippets'
import { fieldPreviewConfig } from '../field-preview'
import { HOW_HINTS, howFits, howLabel, howSql, parseHowSql } from '../aggregates'
import { renderIcons } from '../icons'
import type { Aggregate, AggregateHow, ExprColumn, Step } from '../types'

// ---- dissolve / combine_lines aggregates ----
// Each row keeps one more column through the step: either `how` to combine a column's values,
// picked from a list (no SQL), or a SQL aggregate, switchable with the row's mode button.

export interface AggregateListContext {
  /** The step card. */
  card: HTMLElement
  kind: 'dissolve' | 'combine_lines'
  byKeys: () => (string | ExprColumn)[]
  syncFn: () => void
}

type Mode = 'easy' | 'sql'

/** How many base features a live check runs on. */
export const SAMPLE = 1000

const ICON = (name: string) => `<i data-lucide="${name}" style="width:12px;height:12px"></i>`

function columnsHere(card: HTMLElement): AvailableColumnDetail[] {
  const pl = card.closest('.pipeline-card')
  return pl ? collectColumnsBeforeStep(pl, card) : []
}

/** The step's own config, with `aggregates` in place of the card's. */
function ownStep(ctx: AggregateListContext, aggregates: Aggregate[]): Step {
  const step: Record<string, unknown> = { type: ctx.kind, by: ctx.byKeys(), aggregates }
  const preserve = ctx.card.querySelector<HTMLInputElement>('[data-k="preserve_direction"]')
  if (ctx.kind === 'combine_lines' && preserve && !preserve.checked) step['preserve_direction'] = false
  return step as unknown as Step
}

/** The <option>s of a "how" dropdown, `selected` picked. */
export function howOptionsHtml(selected: string): string {
  const hows = META.aggregate_hows.length ? META.aggregate_hows : Object.keys(HOW_HINTS)
  return hows.map(h => `<option value="${esc(h)}" title="${esc(HOW_HINTS[h] ?? '')}"${h === selected ? ' selected' : ''}>${esc(howLabel(h))}</option>`).join('')
}

/** Adds an aggregate row to the card's list, in the mode its config is written in (or `mode`). */
export function addAggregateRow(ctx: AggregateListContext, agg: Partial<Aggregate> = {}, mode?: Mode): HTMLElement | null {
  const box = ctx.card.querySelector<HTMLElement>('[data-aggs]')
  if (!box) return null
  const row = buildRow(ctx, agg, mode ?? (agg.expr ? 'sql' : 'easy'))
  box.appendChild(row)
  renderIcons(row)
  return row
}

function buildRow(ctx: AggregateListContext, agg: Partial<Aggregate>, mode: Mode): HTMLElement {
  const { card, syncFn } = ctx
  const row = mkEl('div', { className: 'agg-row' })
  row.dataset['aggRow'] = ''
  row.dataset['mode'] = mode
  row.style.marginTop = '6px'
  const kv = mkEl('div', { className: 'kv' })
  row.appendChild(kv)
  const name = agg.to || agg.from || ''
  kv.innerHTML = `<input data-agg-to placeholder="name" value="${esc(name)}" style="flex:0 0 26%" title="The column's name after this step">
    <span class="arrow">=</span>`
  const toInp = kv.querySelector<HTMLInputElement>('[data-agg-to]')!
  const typeMsg = mkEl('div', { className: 'expr-validation-msg' })
  typeMsg.dataset['aggMsg'] = ''

  if (mode === 'easy') {
    kv.insertAdjacentHTML('beforeend', `<select data-agg-how title="How to combine the values" style="flex:0 0 24%">${howOptionsHtml(agg.how ?? 'any')}</select>
      <span class="arrow" data-agg-of>of</span>
      ${comboField('data-agg-from data-from-list="1"', agg.from ?? '', [], 'column')}`)
    const howSel = kv.querySelector<HTMLSelectElement>('[data-agg-how]')!
    const fromInp = kv.querySelector<HTMLInputElement>('[data-agg-from]')!
    const fromCombo = fromInp.closest<HTMLElement>('.combo')!
    const ofEl = kv.querySelector<HTMLElement>('[data-agg-of]')!
    let lastFrom = fromInp.value.trim()
    const membership = attachMembershipCheck(fromInp, () => columnsHere(card).map(c => c.name))
    // A count needs no column: it counts the features.
    const refresh = () => {
      const counting = howSel.value === 'count' && !fromInp.value.trim()
      fromCombo.style.display = ofEl.style.display = counting ? 'none' : ''
      howSel.style.flex = counting ? '1 1 auto' : '0 0 24%'
      const from = fromInp.value.trim()
      const col = columnsHere(card).find(c => c.name.toLowerCase() === from.toLowerCase())
      const type = col?.type ?? ''
      for (const opt of howSel.options) opt.disabled = !!from && !howFits(opt.value, type, META.numeric_aggregate_hows)
      typeMsg.textContent = ''
      typeMsg.className = 'expr-validation-msg'
      if (from && !howFits(howSel.value, type, META.numeric_aggregate_hows)) {
        typeMsg.className = 'expr-validation-msg bad'
        typeMsg.textContent = `${from} is ${type}: ${howLabel(howSel.value)} only works on numbers. Try most common, one value or all of them.`
      }
    }
    fromInp.addEventListener('input', () => {
      // The name follows the column until it's given one of its own.
      const from = fromInp.value.trim()
      if (!toInp.value.trim() || toInp.value.trim() === lastFrom) toInp.value = from
      lastFrom = from
      refresh(); syncFn()
    })
    fromInp.addEventListener('change', () => fromInp.dispatchEvent(new Event('input')))
    howSel.addEventListener('change', () => {
      if (howSel.value === 'count' && !toInp.value.trim()) toInp.value = 'count'
      refresh(); syncFn()
    })
    wireCombos(kv)
    refresh()
    membership.check()
    // Column types arrive after a loaded pipeline's rows are built: the column list's refresh
    // (schema.ts renderAggregatePool) checks the row again.
    ;(row as HTMLElement & { _recheck?: () => void })._recheck = () => { refresh(); membership.check() }
  } else {
    kv.appendChild(exprField({
      value: agg.expr ?? '',
      placeholder: 'sum(population), count(*), …',
      attrs: 'data-agg-expr',
      onChange: syncFn,
      drawer: () => ({
        columns: columnsHere(card),
        // The steps before this one, then this step with only the draft as its aggregate:
        // an aggregate is only valid over the group.
        buildPreviewConfig: draft => fieldPreviewConfig(card, [], ownStep(ctx, [{ to: '_expr_preview', expr: draft }])),
        snippets: [AGGREGATE_SNIPPETS],
      }),
    }))
  }

  kv.insertAdjacentHTML('beforeend', `
    <button class="mini ghost" data-agg-mode title="${mode === 'easy' ? 'Write this as SQL instead' : 'Pick how from a list instead'}" aria-label="${mode === 'easy' ? 'Write this aggregate as SQL' : 'Pick this aggregate from a list'}">${ICON(mode === 'easy' ? 'code' : 'sliders')}</button>
    <button class="mini danger ghost" data-aggdel aria-label="Stop keeping this column">${ICON('x')}</button>`)
  row.append(typeMsg)

  toInp.addEventListener('input', syncFn)
  kv.querySelector('[data-aggdel]')!.addEventListener('click', () => { row.remove(); syncFn() })
  kv.querySelector('[data-agg-mode]')!.addEventListener('click', e => {
    e.stopPropagation()
    const current = readRow(row)
    let next: Partial<Aggregate>
    if (mode === 'easy') {
      const how = current?.how
      next = { to: toInp.value.trim(), expr: how ? howSql(how, current?.from ?? '', META.aggregate_sql) : '' }
    } else {
      const expr = exprValue(row.querySelector<HTMLInputElement>('[data-agg-expr]')!).trim()
      const parsed = parseHowSql(expr, META.aggregate_sql)
      // SQL that isn't one of the list's is lost by switching: let it be undone.
      if (expr && !parsed) mutate('switch aggregate to a list', { undoToast: 'Replaced the SQL with a pick from the list' })
      next = { to: toInp.value.trim(), how: parsed?.how ?? 'any', from: parsed?.from ?? '' }
    }
    const replacement = buildRow(ctx, next, mode === 'easy' ? 'sql' : 'easy')
    row.replaceWith(replacement)
    renderIcons(replacement)
    syncFn()
  })
  return row
}

/** One row's aggregate, or null while it's incomplete. */
function readRow(r: Element): Aggregate | null {
  const to = r.querySelector<HTMLInputElement>('[data-agg-to]')!.value.trim()
  if ((r as HTMLElement).dataset['mode'] === 'sql') {
    const expr = exprValue(r.querySelector<HTMLInputElement>('[data-agg-expr]')!).trim()
    return to && expr ? { to, expr } : null
  }
  const how = r.querySelector<HTMLSelectElement>('[data-agg-how]')!.value as AggregateHow
  const from = r.querySelector<HTMLInputElement>('[data-agg-from]')!.value.trim()
  if (!how || (!from && how !== 'count') || !(to || from)) return null
  // `to` is only written when it isn't the column's own name, which is the default.
  return { ...(to && to !== from ? { to } : {}), ...(from ? { from } : {}), how } as Aggregate
}

/** A dissolve or combine_lines card's aggregates (rows still being filled in left out). */
export function readAggregateRows(c: Element): Aggregate[] {
  return [...c.querySelectorAll('[data-agg-row]')].map(readRow).filter((a): a is Aggregate => a !== null)
}

/** The columns reaching the step that it would drop: neither a key nor kept. */
export function droppedColumns(card: HTMLElement): string[] {
  const names = columnsHere(card).map(c => c.name)
  const { keys, kept } = aggregateColumnStates(card, names)
  return names.filter(n => n.toLowerCase() !== 'geom' && !n.startsWith('__') && !keys.has(n.toLowerCase()) && !kept.has(n.toLowerCase()))
}

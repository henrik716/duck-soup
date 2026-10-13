import { mkEl, esc } from './dom'
import { comboField, setComboOptions, wireCombos } from './combo'
import { renderIcons } from './icons'
import { showToast } from './toast'
import { NUMERIC_TYPE } from './aggregates'
import {
  OP_LABELS, VALUELESS_OPS, buildCondition, parseCondition,
  type Clause, type Condition, type ConditionOp,
} from './conditions'
import type { AvailableColumnDetail } from './schema'

// ---- condition builder ----
// Rows of column · operator · value over a SQL condition field (a filter's `where`, an output
// layer's `filter`, a derived source's `where`). The field stays the source of truth: the rows
// write their SQL into it and fire its `input` event, so saving and the field's live check work
// as they did. The <> button shows the SQL; switching back only works for SQL the rows can
// show (parseCondition), so nothing written by hand is ever thrown away.

export interface ConditionBuilder {
  /** Re-reads the field (after it's been filled in from a loaded config) and picks the mode. */
  load: () => void
}

const ICON = (name: string) => `<i data-lucide="${name}" style="width:12px;height:12px"></i>`

export function attachConditionBuilder(
  input: HTMLInputElement, columns: () => AvailableColumnDetail[], what = 'rows',
): ConditionBuilder {
  const box = mkEl('div', { className: 'cond-builder' })
  box.innerHTML = `<div class="cond-head">
      <span data-cond-easy-head>keep the ${esc(what)} where <select data-cond-join title="Whether every condition has to hold, or one is enough">
        <option value="and">all</option><option value="or">any</option></select> of these hold</span>
      <span data-cond-sql-head>SQL</span>
      <span class="spacer"></span>
      <button type="button" class="mini ghost" data-cond-mode></button>
    </div>
    <div class="cond-rows"></div>
    <button type="button" class="mini ghost" data-cond-add style="margin-top:4px">${ICON('plus')} condition</button>`
  input.insertAdjacentElement('beforebegin', box)
  const rowsEl = box.querySelector<HTMLElement>('.cond-rows')!
  const joinSel = box.querySelector<HTMLSelectElement>('[data-cond-join]')!
  const modeBtn = box.querySelector<HTMLButtonElement>('[data-cond-mode]')!
  let easy = true

  const textColumns = () => new Set(columns()
    .filter(c => c.type && !NUMERIC_TYPE.test(c.type)).map(c => c.name.toLowerCase()))

  const read = (): Condition => ({
    join: joinSel.value === 'or' ? 'or' : 'and',
    clauses: [...rowsEl.querySelectorAll<HTMLElement>('.cond-row')].map(r => ({
      column: r.querySelector<HTMLInputElement>('[data-cond-col]')!.value.trim(),
      op: r.querySelector<HTMLSelectElement>('[data-cond-op]')!.value as ConditionOp,
      value: r.querySelector<HTMLInputElement>('[data-cond-value]')!.value,
    })),
  })
  // With no conditions yet only "+ condition" shows: "keep the rows where all of …" over
  // nothing reads oddly in an optional field.
  const syncEmpty = () => box.classList.toggle('is-empty', !rowsEl.children.length)
  const write = () => {
    syncEmpty()
    if (!easy) return
    input.value = buildCondition(read(), textColumns())
    input.dispatchEvent(new Event('input', { bubbles: true }))
  }

  const addRow = (clause: Partial<Clause> = {}) => {
    const r = mkEl('div', { className: 'kv cond-row' })
    const op = clause.op ?? 'eq'
    r.innerHTML = `${comboField('data-cond-col', clause.column ?? '', [], 'column', 'No columns known yet')}
      <select data-cond-op style="flex:0 0 24%">${Object.entries(OP_LABELS).map(([v, l]) =>
        `<option value="${v}"${v === op ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>
      <input data-cond-value placeholder="value" value="${esc(clause.value ?? '')}">
      <button type="button" class="mini danger ghost" data-cond-del aria-label="Remove this condition">${ICON('x')}</button>`
    const colInp = r.querySelector<HTMLInputElement>('[data-cond-col]')!
    const opSel = r.querySelector<HTMLSelectElement>('[data-cond-op]')!
    const valInp = r.querySelector<HTMLInputElement>('[data-cond-value]')!
    const syncOp = () => {
      valInp.style.display = VALUELESS_OPS.includes(opSel.value as ConditionOp) ? 'none' : ''
      valInp.placeholder = opSel.value === 'in' || opSel.value === 'not_in' ? 'a, b, c' : 'value'
    }
    // The column list is read when it's opened: columns can arrive after the row is built.
    colInp.addEventListener('focus', () => setComboOptions(colInp, columns().map(c => ({
      value: c.name,
      label: `<span style="font-family:var(--mono);">${esc(c.name)}</span>${c.type ? ` <span data-tag style="font-size:10px;color:var(--muted);">${esc(c.type)}</span>` : ''}`,
    })), 'No columns known yet'))
    colInp.addEventListener('input', write)
    colInp.addEventListener('change', write)
    opSel.addEventListener('change', () => { syncOp(); write() })
    valInp.addEventListener('input', write)
    r.querySelector('[data-cond-del]')!.addEventListener('click', () => { r.remove(); write() })
    syncOp()
    wireCombos(r)
    rowsEl.appendChild(r)
    renderIcons(r)
    syncEmpty()
  }

  const setMode = (toEasy: boolean) => {
    easy = toEasy
    box.classList.toggle('is-sql', !easy)
    input.style.display = easy ? 'none' : ''
    modeBtn.innerHTML = easy ? ICON('code') : ICON('sliders')
    modeBtn.title = easy ? 'Write the condition as SQL' : 'Pick the condition from lists'
    renderIcons(modeBtn)
  }

  const showRows = (cond: Condition) => {
    rowsEl.innerHTML = ''
    joinSel.value = cond.join
    cond.clauses.forEach(addRow)
    syncEmpty()
  }

  modeBtn.addEventListener('click', () => {
    if (easy) { setMode(false); input.focus(); return }
    const cond = parseCondition(input.value)
    if (!cond) {
      showToast("This SQL can't be shown as lists: keep it as SQL, or clear it to start over", 'info')
      return
    }
    showRows(cond)
    setMode(true)
  })
  joinSel.addEventListener('change', write)
  box.querySelector('[data-cond-add]')!.addEventListener('click', () => { addRow(); renderIcons(rowsEl) })

  const load = () => {
    const cond = parseCondition(input.value)
    if (cond) showRows(cond)
    setMode(!!cond)
  }
  load()
  return { load }
}

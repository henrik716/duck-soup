import { mkEl, refreshIcons } from './dom'
import { comboField, wireCombos } from './combo'
import { openExprDrawer } from './expr-drawer'
import type { ExprSnippetCategory } from './expr-snippets'
import type { AvailableColumnDetail } from './schema'
import type { Config } from './types'

// An inline SQL expression input with an edit icon that opens the expression builder: the
// `expr` value of a mapping row, a dissolve step's group key or aggregate.
//
// A single-line <input> silently strips \n from its value (the HTML value-sanitization
// algorithm for text controls), so a multi-line expression can't live in the inline field
// without losing its formatting. The real value is kept per input (read it with exprValue);
// the input only ever shows a newline-free preview of it, and is read-only while the
// expression spans several lines.

const VALUES = new WeakMap<HTMLInputElement, string>()

/** The full (possibly multi-line) expression an exprField input holds. */
export function exprValue(input: HTMLInputElement): string {
  return VALUES.get(input) ?? input.value
}

export interface ExprDrawerContext {
  columns: AvailableColumnDetail[]
  /** A throwaway single-column preview Config for a draft, for the drawer's live check. */
  buildPreviewConfig?: (draft: string) => Config | null
  /** Snippet categories shown before the usual ones (e.g. aggregates). */
  snippets?: ExprSnippetCategory[]
}

export interface ExprFieldOptions {
  value: string
  placeholder: string
  /** Extra attributes for the input, e.g. `data-agg-expr`. */
  attrs?: string
  onChange: (value: string) => void
  /** Looked up when the drawer opens: rows built while hydrating a loaded config aren't in
   *  the DOM yet when they're created, so anything read from it then would be empty. */
  drawer: () => ExprDrawerContext
}

const previewText = (v: string) => v.includes('\n') ? v.replace(/\s+/g, ' ').trim() : v

/** The field's element, a combo whose options are the available columns (`data-from-list`,
 *  refilled by updateDatalistsScoped). */
export function exprField(opts: ExprFieldOptions): HTMLElement {
  const wrapperDiv = mkEl('div')
  wrapperDiv.innerHTML = comboField(`data-from-list="1" ${opts.attrs ?? ''}`, previewText(opts.value), [], opts.placeholder)
  const comboEl = wrapperDiv.firstElementChild as HTMLElement
  const inp = comboEl.querySelector<HTMLInputElement>('input')!
  inp.classList.add('has-badge')
  VALUES.set(inp, opts.value)

  const syncReadonly = () => {
    const multiLine = exprValue(inp).includes('\n')
    inp.readOnly = multiLine
    inp.title = multiLine ? 'Multi-line expression — use the edit icon to change it' : ''
  }
  syncReadonly()

  const editBtn = mkEl('button', { className: 'mini ghost expr-edit-btn', type: 'button' })
  editBtn.title = 'Open SQL Expression Builder'
  editBtn.style.cssText = 'position: absolute; right: 28px; top: 50%; transform: translateY(-50%); padding: 4px; z-index: 5; background: transparent; border: none; height: 18px; width: 18px; min-width: 18px; display: flex; align-items: center; justify-content: center; cursor: pointer;'
  editBtn.innerHTML = '<i data-lucide="edit-3" style="width: 11px; height: 11px; color: var(--accent);"></i>'
  comboEl.appendChild(editBtn)

  editBtn.addEventListener('click', e => {
    e.preventDefault()
    e.stopPropagation()
    const ctx = opts.drawer()
    openExprDrawer(exprValue(inp), ctx.columns, val => {
      VALUES.set(inp, val)
      inp.value = previewText(val)
      syncReadonly()
      opts.onChange(val)
    }, ctx.buildPreviewConfig, ctx.snippets)
  })

  const onEdit = () => {
    if (inp.readOnly) return
    VALUES.set(inp, inp.value)
    opts.onChange(inp.value)
  }
  inp.addEventListener('input', onEdit)
  inp.addEventListener('change', onEdit)

  wireCombos(wrapperDiv)
  refreshIcons()
  return comboEl
}

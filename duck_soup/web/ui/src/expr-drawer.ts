import { createIcons } from 'lucide'
import { mkEl, appIcons, esc } from './dom'
import { highlightExpr } from './expr-highlight'
import { EXPR_SNIPPET_CATEGORIES, EXPR_FUNCTION_NAMES } from './expr-snippets'
import { ExprAutocomplete, type AutocompleteItem } from './expr-autocomplete'
import { attachLiveValidation, renderValidationMsg, type LiveValidationHandle } from './validation'
import type { AvailableColumnDetail } from './schema'
import type { Config } from './types'

function insertAtCursor(textarea: HTMLTextAreaElement, text: string) {
  const start = textarea.selectionStart
  const end = textarea.selectionEnd
  const val = textarea.value
  textarea.value = val.substring(0, start) + text + val.substring(end)
  textarea.selectionStart = textarea.selectionEnd = start + text.length
  textarea.focus()
  textarea.dispatchEvent(new Event('input', { bubbles: true }))
}

// Auto-close these pairs while typing in the expression textarea; a selection gets wrapped
// instead of replaced.
const BRACKET_PAIRS: Record<string, string> = { '(': ')', '[': ']', "'": "'" }

// The drawer is a lazily-created singleton reused across every openExprDrawer() call (one
// mapping row's edit-icon click at a time). These hold the *current* call's data so the
// one-time-wired listeners below (autocomplete items, validation config builder) always see
// the right row's context without needing to be re-wired per open.
let currentAutocompleteItems: AutocompleteItem[] = []
let currentBuildPreviewConfig: ((draftExpr: string) => Config | null) | undefined
let validation: LiveValidationHandle | undefined
let closeAutocomplete: (() => void) | undefined

const PREVIEW_COL = '_expr_preview'

export function openExprDrawer(
  initVal: string,
  cols: AvailableColumnDetail[],
  onSave: (val: string) => void,
  buildPreviewConfig?: (draftExpr: string) => Config | null,
): void {
  let drawer = document.getElementById('exprDrawer') as HTMLElement | null
  if (!drawer) {
    drawer = mkEl('div', { id: 'exprDrawer', className: 'drawer-overlay' })
    drawer.innerHTML = `
      <div class="drawer-card fullscreen">
        <div class="drawer-header">
          <h3><i data-lucide="edit-3" style="width:16px;height:16px;color:var(--accent)"></i> Expression Builder</h3>
          <button class="mini ghost" id="closeExprDrawerBtn" aria-label="Close drawer"><i data-lucide="x" style="width:16px;height:16px"></i></button>
        </div>
        <div class="drawer-body" style="display: grid; grid-template-columns: 1.3fr 1fr; gap: 24px; padding: 24px 32px;">
          <div style="display: flex; flex-direction: column; gap: 8px;">
            <label style="font-size: 11px; color: var(--muted); font-weight: 500; font-family: var(--mono);">SQL EXPRESSION</label>
            <div class="expr-editor-wrap">
              <pre id="exprHighlight" aria-hidden="true"></pre>
              <textarea id="exprTextarea" spellcheck="false" placeholder="e.g. population / 1000"></textarea>
            </div>
            <div id="exprValidationMsg" class="expr-validation-msg"></div>
            <div style="font-size: 10px; color: var(--muted); font-family: var(--display);">Ctrl+Enter to apply · Esc to cancel</div>
          </div>
          <div style="display: flex; flex-direction: column; gap: 16px; overflow-y: auto; padding-right: 4px;">
            <div>
              <label style="font-size: 10px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; display: block; margin-bottom: 6px; font-family: var(--display);">Available Columns</label>
              <div id="exprColList" style="display: flex; flex-direction: column; gap: 4px;"></div>
            </div>
            <div>
              <label style="font-size: 10px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; display: block; margin-bottom: 6px; font-family: var(--display);">SQL Snippets</label>
              <div id="exprFuncList"></div>
            </div>
          </div>
        </div>
        <div style="padding: 16px 24px; border-top:1px solid var(--line); display:flex; justify-content:flex-end; gap:12px;">
          <button type="button" class="ghost mini" id="cancelExprDrawerBtn">Cancel</button>
          <button type="button" class="primary mini" id="saveExprDrawerBtn">Apply Expression</button>
        </div>
      </div>`
    document.body.appendChild(drawer)

    const ta = drawer.querySelector<HTMLTextAreaElement>('#exprTextarea')!
    const pre = drawer.querySelector<HTMLElement>('#exprHighlight')!
    const msgEl = drawer.querySelector<HTMLElement>('#exprValidationMsg')!
    const autocomplete = new ExprAutocomplete(ta, () => currentAutocompleteItems)
    closeAutocomplete = () => autocomplete.close()

    const close = () => { drawer!.classList.remove('show'); autocomplete.close() }
    drawer.querySelector('#closeExprDrawerBtn')!.addEventListener('click', close)
    drawer.querySelector('#cancelExprDrawerBtn')!.addEventListener('click', close)
    drawer.addEventListener('click', e => {
      if (e.target === drawer) close()
    })

    const syncHighlight = () => { pre.innerHTML = highlightExpr(ta.value) }

    // Debounced "does this parse against real sample data" check, reusing /api/preview
    // with a synthetic single-column mapping rather than a dedicated backend endpoint.
    // currentBuildPreviewConfig is reassigned per openExprDrawer() call (this drawer is a
    // singleton reused across rows), so it's read through a wrapper rather than captured.
    validation = attachLiveValidation({
      getValue: () => ta.value,
      buildPreviewConfig: draft => currentBuildPreviewConfig ? currentBuildPreviewConfig(draft) : null,
      render: (state, message) => renderValidationMsg(msgEl, state, message),
      previewCol: PREVIEW_COL,
    })

    ta.addEventListener('input', () => {
      syncHighlight()
      autocomplete.update()
      validation!.schedule()
    })
    ta.addEventListener('scroll', () => { pre.scrollTop = ta.scrollTop; pre.scrollLeft = ta.scrollLeft })
    ta.addEventListener('blur', () => { validation!.runNow() })

    ta.addEventListener('keydown', e => {
      if (autocomplete.handleKeydown(e)) return

      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault()
        drawer!.querySelector<HTMLButtonElement>('#saveExprDrawerBtn')!.click()
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        close()
        return
      }
      // Typing a closing character that's already sitting right at the cursor (because it
      // was auto-inserted below) just steps over it, instead of adding a duplicate — the
      // standard "type-over" behavior real editors use for auto-closed pairs.
      if (Object.values(BRACKET_PAIRS).includes(e.key) && ta.selectionStart === ta.selectionEnd && ta.value[ta.selectionStart] === e.key) {
        e.preventDefault()
        ta.selectionStart = ta.selectionEnd = ta.selectionStart + 1
        // No text actually changed, so no 'input' event fires on its own — but the cursor
        // moved past a word boundary, so the autocomplete popup (keyed off word-under-cursor)
        // needs to re-evaluate too, or it's left showing a stale suggestion for the word we
        // just stepped away from.
        autocomplete.update()
        return
      }
      const closeChar = BRACKET_PAIRS[e.key]
      if (closeChar) {
        e.preventDefault()
        const start = ta.selectionStart
        const end = ta.selectionEnd
        const val = ta.value
        const selected = val.slice(start, end)
        ta.value = val.slice(0, start) + e.key + selected + closeChar + val.slice(end)
        ta.selectionStart = start + 1
        ta.selectionEnd = start + 1 + selected.length
        syncHighlight()
      }
    })
  }

  currentBuildPreviewConfig = buildPreviewConfig

  const ta = drawer.querySelector<HTMLTextAreaElement>('#exprTextarea')!
  const pre = drawer.querySelector<HTMLElement>('#exprHighlight')!
  const msgEl = drawer.querySelector<HTMLElement>('#exprValidationMsg')!
  ta.value = initVal
  pre.innerHTML = highlightExpr(initVal)
  msgEl.textContent = ''
  msgEl.className = 'expr-validation-msg'
  closeAutocomplete?.()

  currentAutocompleteItems = [
    ...cols.map(c => ({ name: c.name, type: c.type || c.origin })),
    ...EXPR_FUNCTION_NAMES.map(f => ({ name: f, type: 'function' })),
  ]

  const colList = drawer.querySelector<HTMLElement>('#exprColList')!
  colList.innerHTML = ''
  cols.forEach(c => {
    const el = mkEl('div', { className: 'expr-badge' })
    const rightLabel = c.origin === 'base' ? `${c.type || 'unknown'} [base]` : c.origin
    el.innerHTML = `<span style="font-family:var(--mono);">${esc(c.name)}</span><span class="expr-badge-type">${esc(rightLabel)}</span>`
    el.title = 'Click to insert'
    el.onclick = () => insertAtCursor(ta, c.name)
    colList.appendChild(el)
  })
  if (cols.length === 0) {
    colList.innerHTML = '<div style="color:var(--muted); font-size: 10px;">No columns available</div>'
  }

  const funcList = drawer.querySelector<HTMLElement>('#exprFuncList')!
  funcList.innerHTML = ''
  EXPR_SNIPPET_CATEGORIES.forEach(cat => {
    const details = mkEl('details', { className: 'expr-snippet-category', open: true })
    details.appendChild(mkEl('summary', { textContent: cat.category }))
    cat.snippets.forEach(s => {
      const el = mkEl('div', { className: 'expr-badge' })
      el.title = 'Click to insert'
      el.innerHTML = `${esc(s.name)}<span class="expr-badge-doc">${esc(s.doc)}</span>`
      el.onclick = () => insertAtCursor(ta, s.code)
      details.appendChild(el)
    })
    funcList.appendChild(details)
  })

  drawer.querySelector<HTMLButtonElement>('#saveExprDrawerBtn')!.onclick = () => {
    onSave(ta.value.trim())
    drawer!.classList.remove('show')
    closeAutocomplete?.()
  }

  drawer.classList.add('show')
  createIcons({ icons: appIcons })
  setTimeout(() => ta.focus(), 0)
  if (initVal.trim() && buildPreviewConfig) validation?.runNow()
}

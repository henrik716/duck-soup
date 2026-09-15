import { createIcons, X, ChevronDown, Plus, Folder, ArrowUp, ArrowDown } from 'lucide'
import { mkEl, appIcons, esc } from '../dom'
import { comboField, wireCombos } from '../combo'
import { openFileExplorer } from '../file-explorer'
import { openOverlay, closeOverlay } from '../overlay'
import { attachMembershipCheck } from '../validation'
import type { CodeCase, CodeList } from '../types'

// ---- codelist helpers ----
function caseRow(cr: Partial<CodeCase> = {}, syncFn: () => void): HTMLElement {
  const r = mkEl('div', { className: 'case-row' })
  const kind = cr.is_blank ? 'is_blank' : cr.match !== undefined ? 'match' : cr.like !== undefined ? 'like' : cr.regex !== undefined ? 'regex' : 'match'
  const pattern = cr.match ?? cr.like ?? cr.regex ?? ''
  r.innerHTML = `
    ${comboField('data-ckind', kind, ['match', 'like', 'regex', { value: 'is_blank', label: 'is blank/null' }])}
    <input data-cpattern placeholder="pattern" value="${esc(pattern)}" style="display:${kind === 'is_blank' ? 'none' : ''}">
    <input data-cvalue placeholder="value" value="${esc(cr.value)}">
    <button class="mini ghost" data-cup aria-label="Move this rule earlier" title="Move up — earlier rules win"><i data-lucide="arrow-up" style="width:11px;height:11px"></i></button>
    <button class="mini ghost" data-cdown aria-label="Move this rule later" title="Move down"><i data-lucide="arrow-down" style="width:11px;height:11px"></i></button>
    <button class="mini danger ghost" data-cdel aria-label="Remove this rule"><i data-lucide="x" style="width:12px;height:12px"></i></button>`
  r.querySelector('[data-cdel]')!.addEventListener('click', () => { r.remove(); syncFn() })

  // Rule order is precedence — the engine compiles these to a CASE where the first match
  // wins — so being unable to reorder them was a real gap, not a nicety.
  r.querySelector('[data-cup]')!.addEventListener('click', () => {
    const p = r.previousElementSibling
    if (p) { r.parentNode!.insertBefore(r, p); syncFn() }
  })
  r.querySelector('[data-cdown]')!.addEventListener('click', () => {
    const n = r.nextElementSibling
    if (n) { r.parentNode!.insertBefore(n, r); syncFn() }
  })
  const kindInput = r.querySelector<HTMLInputElement>('[data-ckind]')!
  const patternInput = r.querySelector<HTMLInputElement>('[data-cpattern]')!
  kindInput.addEventListener('input', () => {
    patternInput.style.display = kindInput.value === 'is_blank' ? 'none' : ''
  })
  r.querySelectorAll('select,input').forEach(i => i.addEventListener('input', syncFn))
  wireCombos(r)
  createIcons({ icons: { X, ChevronDown, ArrowUp, ArrowDown } })
  return r
}

function codelistPanel(cl: Partial<CodeList> = {}, syncFn: () => void, availableColumns: string[] = []): HTMLElement {
  const isFile = !!cl.file
  const panel = mkEl('div', { className: 'codelist-panel' })
  panel.innerHTML = `
    <div class="row">
      <label class="field grow">source column<input data-cl-source value="${esc(cl.source)}" placeholder="raw_category"></label>
      <label class="field" style="flex:0 0 auto"><span>&nbsp;</span>
        <span style="display:flex;align-items:center;gap:6px;color:var(--ink);font-family:system-ui">
          <input type="checkbox" data-cl-ci ${cl.case_insensitive !== false ? 'checked' : ''} style="width:auto;margin:0"> case-insensitive</span></label>
    </div>
    <div class="codelist-toggle">
      <button type="button" data-mode="rules" class="${isFile ? '' : 'active'}">rules</button>
      <button type="button" data-mode="file" class="${isFile ? 'active' : ''}">file lookup</button>
    </div>
    <div data-rules-block style="display:${isFile ? 'none' : 'block'}">
      <div data-cases></div>
      <button class="mini ghost" data-addcase style="margin-top:4px"><i data-lucide="plus" style="width:12px;height:12px"></i> rule</button>
    </div>
    <div data-file-block style="display:${isFile ? 'block' : 'none'}">
      <div class="file-fields">
        <label class="field">csv path
          <div style="display:flex;gap:6px">
            <input data-cl-file value="${esc(cl.file)}" placeholder="codelists/produce.csv" style="flex:1">
            <button type="button" class="mini ghost cl-browse-btn" title="Browse CSV" style="padding:10px;flex-shrink:0"><i data-lucide="folder"></i></button>
          </div>
        </label>
        <label class="field">key column<input data-cl-matchcol value="${esc(cl.file_match_col)}" placeholder="code"></label>
        <label class="field">value column<input data-cl-valuecol value="${esc(cl.file_value_col)}" placeholder="label"></label>
      </div>
      <p class="hint">2-column CSV (or more); matched against the source column.</p>
    </div>
    <label class="field" style="margin-top:8px">default (optional)
      <input data-cl-default value="${esc(cl.default)}" placeholder="Unknown"></label>`

  const casesBox = panel.querySelector<HTMLElement>('[data-cases]')!
  ;(cl.cases || []).forEach(c => casesBox.appendChild(caseRow(c, syncFn)))
  panel.querySelector('[data-addcase]')!.addEventListener('click', () => {
    casesBox.appendChild(caseRow({}, syncFn))
    createIcons({ icons: appIcons })
    syncFn()
  })

  const rulesBtn = panel.querySelector<HTMLButtonElement>('[data-mode="rules"]')!
  const fileBtn = panel.querySelector<HTMLButtonElement>('[data-mode="file"]')!
  const rulesBlock = panel.querySelector<HTMLElement>('[data-rules-block]')!
  const fileBlock = panel.querySelector<HTMLElement>('[data-file-block]')!

  const setMode = (m: 'rules' | 'file') => {
    rulesBtn.classList.toggle('active', m === 'rules')
    fileBtn.classList.toggle('active', m === 'file')
    rulesBlock.style.display = m === 'rules' ? 'block' : 'none'
    fileBlock.style.display = m === 'file' ? 'block' : 'none'
    panel.dataset['mode'] = m
  }
  setMode(isFile ? 'file' : 'rules')
  rulesBtn.onclick = () => { setMode('rules'); syncFn() }
  fileBtn.onclick = () => { setMode('file'); syncFn() }

  panel.querySelector('.cl-browse-btn')!.addEventListener('click', () =>
    openFileExplorer(panel.querySelector<HTMLInputElement>('[data-cl-file]')!))

  panel.querySelectorAll('input,[data-cl-ci]').forEach(i => i.addEventListener('input', syncFn))

  // Bare upstream column name — checking it against the known column set is instant and
  // needs no network round trip (unlike the SQL-expression fields elsewhere).
  attachMembershipCheck(panel.querySelector<HTMLInputElement>('[data-cl-source]')!, () => availableColumns)

  createIcons({ icons: { Plus, Folder } })
  return panel
}

function readCodelistPanel(panel: HTMLElement): CodeList {
  const g = (sel: string) => panel.querySelector<HTMLInputElement>(sel)?.value?.trim() ?? ''
  const cl: CodeList = {
    source: g('[data-cl-source]'),
    case_insensitive: panel.querySelector<HTMLInputElement>('[data-cl-ci]')!.checked,
  }
  const def = g('[data-cl-default]'); if (def) cl.default = def
  if (panel.dataset['mode'] === 'file') {
    cl.file = g('[data-cl-file]')
    cl.file_match_col = g('[data-cl-matchcol]')
    cl.file_value_col = g('[data-cl-valuecol]')
  } else {
    cl.cases = [...panel.querySelectorAll('[data-cases] .case-row')].map(r => {
      const kind = r.querySelector<HTMLInputElement>('[data-ckind]')!.value as 'match' | 'like' | 'regex' | 'is_blank'
      const value = r.querySelector<HTMLInputElement>('[data-cvalue]')!.value.trim()
      if (!value) return null
      if (kind === 'is_blank') return { is_blank: true, value } as CodeCase
      const pattern = r.querySelector<HTMLInputElement>('[data-cpattern]')!.value.trim()
      if (!pattern) return null
      return { [kind]: pattern, value } as CodeCase
    }).filter((x): x is CodeCase => x !== null)
  }
  return cl
}

export function openCodelistDrawer(
  title: string,
  initCodelist: Partial<CodeList>,
  onSave: (cl: CodeList) => void,
  availableColumns: string[] = [],
): void {
  let drawer = document.getElementById('codelistDrawer') as HTMLElement | null
  if (!drawer) {
    drawer = mkEl('div', { id: 'codelistDrawer', className: 'drawer-overlay' })
    drawer.innerHTML = `
      <div class="drawer-card fullscreen">
        <div class="drawer-header">
          <h3><i data-lucide="sliders" style="width:16px;height:16px;color:var(--accent)"></i> Configure Codelist</h3>
          <button class="mini ghost" id="closeDrawerBtn" aria-label="Close drawer"><i data-lucide="x" style="width:16px;height:16px"></i></button>
        </div>
        <div class="drawer-body" id="drawerBody" style="align-items:center;"></div>
        <div style="padding: 16px 24px; border-top:1px solid var(--line); display:flex; justify-content:flex-end; gap:12px;">
          <button type="button" class="ghost mini" id="cancelDrawerBtn">Cancel</button>
          <button type="button" class="primary mini" id="saveDrawerBtn">Apply Rules</button>
        </div>
      </div>`
    document.body.appendChild(drawer)

    const el = drawer
    const close = () => { el.classList.remove('show'); closeOverlay(el) }
    drawer.querySelector('#closeDrawerBtn')!.addEventListener('click', close)
    drawer.querySelector('#cancelDrawerBtn')!.addEventListener('click', close)
    drawer.addEventListener('click', e => {
      if (e.target === el) close()
    })
  }

  const drawerBody = drawer.querySelector<HTMLElement>('#drawerBody')!
  drawerBody.innerHTML = ''

  drawer.querySelector('.drawer-header h3')!.innerHTML =
    `<i data-lucide="sliders" style="width:16px;height:16px;color:var(--accent)"></i> Codelist: <span style="color:var(--ink);font-family:var(--mono);font-size:13px">${title}</span>`

  const panel = codelistPanel(initCodelist, () => {}, availableColumns)
  drawerBody.appendChild(panel)

  const el = drawer
  const dismiss = () => { el.classList.remove('show'); closeOverlay(el) }

  el.querySelector<HTMLButtonElement>('#saveDrawerBtn')!.onclick = () => {
    onSave(readCodelistPanel(panel))
    dismiss()
  }

  el.classList.add('show')
  openOverlay(el, dismiss)
  createIcons({ icons: appIcons })
}

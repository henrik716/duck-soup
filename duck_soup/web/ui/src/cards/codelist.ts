import { createIcons, X, ChevronDown, Plus, Folder } from 'lucide'
import { mkEl, appIcons } from '../dom'
import { comboField, wireCombos } from '../combo'
import { openFileExplorer } from '../file-explorer'
import type { CodeCase, CodeList } from '../types'

// ---- codelist helpers ----
function caseRow(cr: Partial<CodeCase> = {}, syncFn: () => void): HTMLElement {
  const r = mkEl('div', { className: 'case-row' })
  const kind = cr.match !== undefined ? 'match' : cr.like !== undefined ? 'like' : cr.regex !== undefined ? 'regex' : 'match'
  const pattern = cr.match ?? cr.like ?? cr.regex ?? ''
  r.innerHTML = `
    ${comboField('data-ckind', kind, ['match', 'like', 'regex'])}
    <input data-cpattern placeholder="pattern" value="${pattern}">
    <input data-cvalue placeholder="value" value="${cr.value ?? ''}">
    <button class="mini danger ghost" data-cdel><i data-lucide="x" style="width:12px;height:12px"></i></button>`
  r.querySelector('[data-cdel]')!.addEventListener('click', () => { r.remove(); syncFn() })
  r.querySelectorAll('select,input').forEach(i => i.addEventListener('input', syncFn))
  wireCombos(r)
  createIcons({ icons: { X, ChevronDown } })
  return r
}

function codelistPanel(cl: Partial<CodeList> = {}, syncFn: () => void): HTMLElement {
  const isFile = !!cl.file
  const panel = mkEl('div', { className: 'codelist-panel' })
  panel.innerHTML = `
    <div class="row">
      <label class="field grow">source column<input data-cl-source value="${cl.source ?? ''}" placeholder="raw_category"></label>
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
            <input data-cl-file value="${cl.file ?? ''}" placeholder="codelists/produce.csv" style="flex:1">
            <button type="button" class="mini ghost cl-browse-btn" title="Browse CSV" style="padding:10px;flex-shrink:0"><i data-lucide="folder"></i></button>
          </div>
        </label>
        <label class="field">key column<input data-cl-matchcol value="${cl.file_match_col ?? ''}" placeholder="code"></label>
        <label class="field">value column<input data-cl-valuecol value="${cl.file_value_col ?? ''}" placeholder="label"></label>
      </div>
      <p class="hint">2-column CSV (or more); matched against the source column.</p>
    </div>
    <label class="field" style="margin-top:8px">default (optional)
      <input data-cl-default value="${cl.default ?? ''}" placeholder="Unknown"></label>`

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
      const kind = r.querySelector<HTMLInputElement>('[data-ckind]')!.value as 'match' | 'like' | 'regex'
      const pattern = r.querySelector<HTMLInputElement>('[data-cpattern]')!.value.trim()
      const value = r.querySelector<HTMLInputElement>('[data-cvalue]')!.value.trim()
      if (!pattern || !value) return null
      return { [kind]: pattern, value } as CodeCase
    }).filter((x): x is CodeCase => x !== null)
  }
  return cl
}

export function openCodelistDrawer(title: string, initCodelist: Partial<CodeList>, onSave: (cl: CodeList) => void): void {
  let drawer = document.getElementById('codelistDrawer') as HTMLElement | null
  if (!drawer) {
    drawer = mkEl('div', { id: 'codelistDrawer', className: 'drawer-overlay' })
    drawer.innerHTML = `
      <div class="drawer-card">
        <div class="drawer-header">
          <h3><i data-lucide="sliders" style="width:16px;height:16px;color:var(--accent)"></i> Configure Codelist</h3>
          <button class="mini ghost" id="closeDrawerBtn" aria-label="Close drawer"><i data-lucide="x" style="width:16px;height:16px"></i></button>
        </div>
        <div class="drawer-body" id="drawerBody"></div>
        <div style="padding: 16px 24px; border-top:1px solid var(--line); display:flex; justify-content:flex-end; gap:12px;">
          <button type="button" class="ghost mini" id="cancelDrawerBtn">Cancel</button>
          <button type="button" class="primary mini" id="saveDrawerBtn">Apply Rules</button>
        </div>
      </div>`
    document.body.appendChild(drawer)

    const close = () => drawer!.classList.remove('show')
    drawer.querySelector('#closeDrawerBtn')!.addEventListener('click', close)
    drawer.querySelector('#cancelDrawerBtn')!.addEventListener('click', close)
    drawer.addEventListener('click', e => {
      if (e.target === drawer) close()
    })
  }

  const drawerBody = drawer.querySelector<HTMLElement>('#drawerBody')!
  drawerBody.innerHTML = ''

  drawer.querySelector('.drawer-header h3')!.innerHTML =
    `<i data-lucide="sliders" style="width:16px;height:16px;color:var(--accent)"></i> Codelist: <span style="color:var(--ink);font-family:var(--mono);font-size:13px">${title}</span>`

  const panel = codelistPanel(initCodelist, () => {})
  drawerBody.appendChild(panel)

  drawer.querySelector<HTMLButtonElement>('#saveDrawerBtn')!.onclick = () => {
    const cl = readCodelistPanel(panel)
    onSave(cl)
    drawer!.classList.remove('show')
  }

  drawer.classList.add('show')
  createIcons({ icons: appIcons })
}

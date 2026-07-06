import { createIcons } from 'lucide'
import { mkEl, appIcons } from './dom'

function insertAtCursor(textarea: HTMLTextAreaElement, text: string) {
  const start = textarea.selectionStart
  const end = textarea.selectionEnd
  const val = textarea.value
  textarea.value = val.substring(0, start) + text + val.substring(end)
  textarea.selectionStart = textarea.selectionEnd = start + text.length
  textarea.focus()
}

export function openExprDrawer(initVal: string, cols: string[], onSave: (val: string) => void): void {
  let drawer = document.getElementById('exprDrawer') as HTMLElement | null
  if (!drawer) {
    drawer = mkEl('div', { id: 'exprDrawer', className: 'drawer-overlay' })
    drawer.innerHTML = `
      <div class="drawer-card" style="width: 580px; max-width: 95vw;">
        <div class="drawer-header">
          <h3><i data-lucide="edit-3" style="width:16px;height:16px;color:var(--accent)"></i> Expression Builder</h3>
          <button class="mini ghost" id="closeExprDrawerBtn" aria-label="Close drawer"><i data-lucide="x" style="width:16px;height:16px"></i></button>
        </div>
        <div class="drawer-body" style="display: grid; grid-template-columns: 1.3fr 1fr; gap: 16px; padding: 16px 24px; height: 380px;">
          <div style="display: flex; flex-direction: column; gap: 8px;">
            <label style="font-size: 11px; color: var(--muted); font-weight: 500; font-family: var(--mono);">SQL EXPRESSION</label>
            <textarea id="exprTextarea" style="flex: 1; font-family: var(--mono); resize: none; padding: 10px; background: var(--void); color: var(--ink); border: 1px solid var(--line); border-radius: var(--radius-sm); font-size: 13px;" placeholder="e.g. population / 1000"></textarea>
          </div>
          <div style="display: flex; flex-direction: column; gap: 16px; overflow-y: auto; padding-right: 4px;">
            <div>
              <label style="font-size: 10px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; display: block; margin-bottom: 6px; font-family: var(--display);">Available Columns</label>
              <div id="exprColList" style="display: flex; flex-direction: column; gap: 4px;"></div>
            </div>
            <div>
              <label style="font-size: 10px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; display: block; margin-bottom: 6px; font-family: var(--display);">SQL Snippets</label>
              <div id="exprFuncList" style="display: flex; flex-direction: column; gap: 4px;"></div>
            </div>
          </div>
        </div>
        <div style="padding: 16px 24px; border-top:1px solid var(--line); display:flex; justify-content:flex-end; gap:12px;">
          <button type="button" class="ghost mini" id="cancelExprDrawerBtn">Cancel</button>
          <button type="button" class="primary mini" id="saveExprDrawerBtn">Apply Expression</button>
        </div>
      </div>`
    document.body.appendChild(drawer)

    const close = () => drawer!.classList.remove('show')
    drawer.querySelector('#closeExprDrawerBtn')!.addEventListener('click', close)
    drawer.querySelector('#cancelExprDrawerBtn')!.addEventListener('click', close)
    drawer.addEventListener('click', e => {
      if (e.target === drawer) close()
    })
  }

  const ta = drawer.querySelector<HTMLTextAreaElement>('#exprTextarea')!
  ta.value = initVal

  const colList = drawer.querySelector<HTMLElement>('#exprColList')!
  colList.innerHTML = ''
  cols.forEach(c => {
    const el = mkEl('div', { className: 'available-field-badge' })
    el.style.cssText = 'padding: 4px 6px; background: rgba(255,255,255,0.02); border: 1px solid var(--line); border-radius: var(--radius-sm); font-size: 10.5px; font-family: var(--mono); cursor: pointer; user-select: none;'
    el.innerText = c
    el.ondblclick = () => insertAtCursor(ta, c)
    colList.appendChild(el)
  })

  if (cols.length === 0) {
    colList.innerHTML = '<div style="color:var(--muted); font-size: 10px;">No columns available</div>'
  }

  const snippets = [
    { name: 'COALESCE(col, val)', code: 'COALESCE(column, 0)' },
    { name: 'CASE WHEN', code: 'CASE WHEN condition THEN true_val ELSE false_val END' },
    { name: 'CONCAT(a, b)', code: "CONCAT(col1, ' ', col2)" },
    { name: 'ROUND(val, dec)', code: 'ROUND(column, 2)' },
    { name: 'NULLIF(a, b)', code: "NULLIF(column, '')" },
    { name: 'LOWER(str)', code: 'LOWER(column)' },
    { name: 'UPPER(str)', code: 'UPPER(column)' }
  ]

  const funcList = drawer.querySelector<HTMLElement>('#exprFuncList')!
  funcList.innerHTML = ''
  snippets.forEach(s => {
    const el = mkEl('div', { className: 'available-field-badge' })
    el.style.cssText = 'padding: 4px 6px; background: rgba(255,255,255,0.02); border: 1px solid var(--line); border-radius: var(--radius-sm); font-size: 10.5px; font-family: var(--mono); cursor: pointer; user-select: none;'
    el.innerText = s.name
    el.ondblclick = () => insertAtCursor(ta, s.code)
    funcList.appendChild(el)
  })

  drawer.querySelector<HTMLButtonElement>('#saveExprDrawerBtn')!.onclick = () => {
    onSave(ta.value)
    drawer!.classList.remove('show')
  }

  drawer.classList.add('show')
  createIcons({ icons: appIcons })
}

import { createIcons } from 'lucide'
import { mkEl, appIcons } from './dom'
import { openOverlay, closeOverlay } from './overlay'
import { parseYaml } from './api'
import type { Config } from './types'

// Paste-to-import: the only existing way YAML gets into the visual editor is a file already
// saved under pipelines/ (via /api/pipelines/{name}). This gives a config from anywhere else
// (git, a colleague, an email) a way in without touching the server's filesystem first.
export function openPasteYamlModal(onImport: (cfg: Config) => void): void {
  let modal = document.getElementById('pasteYamlModal') as HTMLElement | null
  if (!modal) {
    modal = mkEl('div', { id: 'pasteYamlModal', className: 'modal-overlay' })
    modal.innerHTML = `
      <div class="modal-card" style="width: 640px; max-width: 90vw;">
        <div class="modal-header">
          <h3><i data-lucide="file-code" style="width:16px;height:16px;color:var(--accent)"></i> Import Pipeline YAML</h3>
          <button class="mini ghost" id="closePasteYamlBtn" aria-label="Close modal"><i data-lucide="x" style="width:16px;height:16px"></i></button>
        </div>
        <div class="modal-body" style="display:flex; flex-direction:column; gap:8px;">
          <p class="hint">Paste a full pipeline config (the same shape as a saved <span style="font-family:var(--mono)">pipelines/*.yaml</span> file) — it replaces what's currently open in the editor.</p>
          <textarea id="pasteYamlTextarea" spellcheck="false" rows="16"
            style="width:100%; resize:vertical; font-family:var(--mono); font-size:12px; background:var(--panel2); color:var(--ink); border:1px solid var(--line); border-radius:var(--radius-sm); padding:10px;"
            placeholder="name: my_pipeline&#10;working_crs: EPSG:25833&#10;sources:&#10;  - id: places&#10;    ..."></textarea>
          <div id="pasteYamlMsg" class="expr-validation-msg"></div>
        </div>
        <div style="padding: 16px 24px; border-top:1px solid var(--line); display:flex; justify-content:flex-end; gap:12px;">
          <button type="button" class="ghost mini" id="cancelPasteYamlBtn">Cancel</button>
          <button type="button" class="primary mini" id="importPasteYamlBtn">Import</button>
        </div>
      </div>`
    document.body.appendChild(modal)

    modal.querySelector('#closePasteYamlBtn')!.addEventListener('click', () => close())
    modal.querySelector('#cancelPasteYamlBtn')!.addEventListener('click', () => close())
    modal.addEventListener('click', e => { if (e.target === modal) close() })
  }

  const el = modal
  const close = () => { el.classList.remove('show'); closeOverlay(el) }

  const ta = modal.querySelector<HTMLTextAreaElement>('#pasteYamlTextarea')!
  const msgEl = modal.querySelector<HTMLElement>('#pasteYamlMsg')!
  ta.value = ''
  msgEl.textContent = ''
  msgEl.className = 'expr-validation-msg'

  modal.querySelector<HTMLButtonElement>('#importPasteYamlBtn')!.onclick = async () => {
    const text = ta.value.trim()
    if (!text) { msgEl.textContent = 'Paste some YAML first'; msgEl.className = 'expr-validation-msg bad'; return }
    msgEl.textContent = 'Checking…'
    msgEl.className = 'expr-validation-msg pending'
    try {
      const d = await parseYaml(text)
      if (d.ok && d.config) {
        onImport(d.config)
        close()
      } else {
        msgEl.textContent = d.error || 'Could not parse this as a pipeline config'
        msgEl.className = 'expr-validation-msg bad'
      }
    } catch (e) {
      msgEl.textContent = String(e instanceof Error ? e.message : e)
      msgEl.className = 'expr-validation-msg bad'
    }
  }

  el.classList.add('show')
  openOverlay(el, close)
  createIcons({ icons: appIcons })
}

import { createIcons, Info, ChevronDown } from 'lucide'
import { mkEl, esc } from './dom'
import { comboField, wireCombos } from './combo'
import type { DatasetMetadata } from './types'

export const METADATA_FIELDS = [
  'name', 'abstract', 'origin', 'update_frequency',
  'geometric_quality', 'attribute_quality', 'access_method_source', 'gdpr',
] as const

const METADATA_OPTIONS: Partial<Record<typeof METADATA_FIELDS[number], string[]>> = {
  update_frequency: ['Continuous', 'Daily', 'Weekly', 'Monthly', 'Annual', 'Irregular', 'As needed', 'Not planned'],
  geometric_quality: ['Good', 'Medium', 'Poor'],
  attribute_quality: ['Good', 'Medium', 'Poor'],
  gdpr: ['Non-personal', 'Personal', 'Sensitive'],
}

function metaInput(key: typeof METADATA_FIELDS[number], value: string, placeholder: string): string {
  const options = METADATA_OPTIONS[key]
  if (!options) return `<input data-mk="${key}" placeholder="${esc(placeholder)}" value="${esc(value)}">`
  return comboField(`data-mk="${key}"`, value, options, placeholder)
}

export function buildMetadataSection(
  meta: Partial<DatasetMetadata> = {},
  syncFn: () => void,
): HTMLElement {
  const section = mkEl('div', { id: 'cfg-metadata' })
  section.className = 'card cfg-metadata-card'
  section.style.cssText = 'margin-bottom:20px;padding:0;'

  section.innerHTML = `
    <div class="cfg-metadata-header" data-metadata-toggle>
      <div class="cfg-metadata-title">
        <span class="cfg-metadata-icon"><i data-lucide="info" style="width:15px;height:15px"></i></span>
        <div>
          <div class="cfg-metadata-label">Dataset Metadata</div>
          <div class="cfg-metadata-sub">Catalogue details shipped alongside the output</div>
        </div>
      </div>
      <div class="cfg-metadata-right">
        <span class="cfg-metadata-progress" data-meta-progress></span>
        <button type="button" class="chevron-btn meta-toggle-btn" aria-expanded="false" aria-controls="cfg-metadata-body" aria-label="Expand dataset metadata">
          <i data-lucide="chevron-down" class="meta-chevron" style="width:16px;height:16px"></i>
        </button>
      </div>
    </div>
    <div class="cfg-metadata-collapse collapsed" id="cfg-metadata-body">
      <div class="cfg-metadata-collapse-inner">
        <div class="cfg-metadata-body">
          <label class="field grow">name
            <input data-mk="name" placeholder="Norwegian Embassies" value="${esc(meta.name)}">
          </label>
          <label class="field" style="grid-column:1 / -1;">abstract
            <textarea data-mk="abstract" rows="2" placeholder="Dataset description / abstract"
                      style="resize:vertical;font-family:inherit;">${esc(meta.abstract)}</textarea>
          </label>
          <label class="field grow">origin
            <input data-mk="origin" placeholder="Ministry of Foreign Affairs" value="${esc(meta.origin)}">
          </label>
          <label class="field grow">access method / source
            <input data-mk="access_method_source" placeholder="GeoPackage file, WFS, …" value="${esc(meta.access_method_source)}">
          </label>
          <label class="field grow">update frequency
            ${metaInput('update_frequency', meta.update_frequency ?? '', 'Annual')}
          </label>
          <label class="field grow">GDPR
            ${metaInput('gdpr', meta.gdpr ?? '', 'Non-personal / Personal / Sensitive')}
          </label>
          <label class="field grow">geometric quality
            ${metaInput('geometric_quality', meta.geometric_quality ?? '', 'Good / Medium / Poor')}
          </label>
          <label class="field grow">attribute quality
            ${metaInput('attribute_quality', meta.attribute_quality ?? '', 'Good / Medium / Poor')}
          </label>
        </div>
      </div>
    </div>`

  const toggleEl = section.querySelector<HTMLElement>('[data-metadata-toggle]')!
  const collapse = section.querySelector<HTMLElement>('.cfg-metadata-collapse')!
  const progressEl = section.querySelector<HTMLElement>('[data-meta-progress]')!

  const updateProgress = () => {
    const filled = METADATA_FIELDS.filter(k =>
      section.querySelector<HTMLInputElement | HTMLTextAreaElement>(`[data-mk="${k}"]`)?.value.trim()).length
    progressEl.textContent = `${filled} / ${METADATA_FIELDS.length} filled`
    progressEl.classList.toggle('complete', filled === METADATA_FIELDS.length)
  }

  const toggleBtn = section.querySelector<HTMLButtonElement>('.meta-toggle-btn')!
  const toggleCollapse = () => {
    const collapsed = collapse.classList.toggle('collapsed')
    toggleBtn.setAttribute('aria-expanded', String(!collapsed))
    toggleBtn.setAttribute('aria-label', collapsed ? 'Expand dataset metadata' : 'Collapse dataset metadata')
  }
  toggleEl.addEventListener('click', e => {
    if ((e.target as Element).closest('button,input,select,textarea')) return
    toggleCollapse()
  })
  toggleBtn.addEventListener('click', e => { e.stopPropagation(); toggleCollapse() })

  section.querySelectorAll('[data-mk]').forEach(el =>
    el.addEventListener('input', () => { updateProgress(); syncFn() }))

  wireCombos(section)
  updateProgress()
  createIcons({ icons: { Info, ChevronDown } })
  return section
}

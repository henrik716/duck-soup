import { createIcons, Loader, UploadCloud } from 'lucide'
import { uploadFile } from '../api'
import { mkEl, val, refreshIcons, getDragAfterElement } from '../dom'
import { EXT_FORMAT } from '../state'
import { comboField, wireCombos, ensureComboOption } from '../combo'
import {
  refreshBaseOptionsScoped, updateDatalistsScoped,
  collectAvailableColumnsScoped, collectAllSourceIdsScoped,
} from '../schema'
import { showToast } from '../toast'
import { openStepGalleryModal } from '../step-gallery'
import { sourceCard } from './source-card'
import { derivedSourceCard } from './derived-source-card'
import { stepCard } from './step-card'
import { mapRow, type MapRowElement } from './map-row'
import { outputLayerCard } from './output-layer-card'
import type { DerivedSource, MapItem, OutputLayer, PipelineDef, Source, Step } from '../types'

// ---- pipeline card ----
let _plCounter = 0

export function pipelineCard(pdef: Partial<PipelineDef> = {}, syncFn: () => void): HTMLElement {
  const plId = String(_plCounter++)
  const card = mkEl('div', { className: 'pipeline-card' })
  card.setAttribute('data-pl-id', plId)

  card.innerHTML = `
    <div class="pipeline-card-header">
      <i data-lucide="layers" style="width:14px;height:14px;color:var(--accent);flex-shrink:0"></i>
      <input class="pl-name" placeholder="pipeline name" value="${pdef.name ?? ''}">
      <button class="mini danger ghost pl-remove" title="Remove pipeline"><i data-lucide="trash-2" style="width:12px;height:12px"></i></button>
      <i data-lucide="chevron-down" class="chevron" style="flex-shrink:0"></i>
    </div>
    <div class="pipeline-card-body">
      <div class="pl-datalists"></div>

      <!-- Sources -->
      <div class="pl-block collapsed" data-sec="sources">
        <div class="pl-block-header">
          <div class="pl-block-left">
            <i data-lucide="database" class="pl-block-icon" style="width:13px;height:13px;color:var(--accent)"></i>
            <span class="pl-block-title">sources</span>
            <span class="pl-block-summary"></span>
          </div>
          <div class="pl-block-actions">
            <button class="addbtn pl-add-source" style="width:auto;padding:3px 9px;margin:0;font-size:11px"><i data-lucide="plus" style="width:11px;height:11px"></i> add</button>
            <i data-lucide="chevron-down" class="pl-chevron"></i>
          </div>
        </div>
        <div class="pl-block-content">
          <div class="sources-dropzone">
            <i data-lucide="upload-cloud" style="width:24px;height:24px;margin-bottom:4px"></i>
            <span class="title">Drag & drop spatial files here</span>
            <span class="subtitle">Or click to browse from your computer</span>
          </div>
          <div class="templates-grid">
            <button type="button" class="template-btn" data-fmt="gpkg"><i data-lucide="database"></i> GPKG</button>
            <button type="button" class="template-btn" data-fmt="geojson"><i data-lucide="globe"></i> GeoJSON</button>
            <button type="button" class="template-btn" data-fmt="fgdb"><i data-lucide="folder-archive"></i> FileGDB</button>
            <button type="button" class="template-btn" data-fmt="parquet"><i data-lucide="server"></i> Parquet</button>
            <button type="button" class="template-btn" data-fmt="shp"><i data-lucide="map"></i> Shapefile</button>
            <button type="button" class="template-btn" data-fmt="wfs"><i data-lucide="network"></i> WFS</button>
            <button type="button" class="template-btn" data-fmt="arcgis_rest"><i data-lucide="map-pinned"></i> ArcGIS REST</button>
            <button type="button" class="template-btn" data-fmt="geojson" data-uri="https://example.com/api/collections/{collection}/items"><i data-lucide="link"></i> OGC API</button>
            <button type="button" class="template-btn" data-fmt="xlsx"><i data-lucide="file-spreadsheet"></i> Excel</button>
            <button type="button" class="template-btn" data-fmt="csv"><i data-lucide="file-text"></i> CSV</button>
          </div>
          <div class="pl-sources"></div>
        </div>
      </div>

      <!-- Derived sources -->
      <div class="pl-block collapsed" data-sec="derived_sources">
        <div class="pl-block-header">
          <div class="pl-block-left">
            <i data-lucide="git-branch" class="pl-block-icon" style="width:13px;height:13px;color:var(--accent)"></i>
            <span class="pl-block-title">derived sources</span>
            <span class="pl-block-summary"></span>
          </div>
          <div class="pl-block-actions">
            <button class="addbtn pl-add-derived" style="width:auto;padding:3px 9px;margin:0;font-size:11px"><i data-lucide="plus" style="width:11px;height:11px"></i> add</button>
            <i data-lucide="chevron-down" class="pl-chevron"></i>
          </div>
        </div>
        <div class="pl-block-content">
          <div class="pl-derived-sources"></div>
          <p class="hint" style="margin-top:6px">Optional — a filtered/buffered view of an existing source, usable anywhere a step needs a "source" (fork part of a source, transform it, then join it back into the base)</p>
        </div>
      </div>

      <!-- Base -->
      <div class="pl-block collapsed" data-sec="base">
        <div class="pl-block-header">
          <div class="pl-block-left">
            <i data-lucide="play" class="pl-block-icon" style="width:13px;height:13px;color:var(--accent)"></i>
            <span class="pl-block-title">base source</span>
            <span class="pl-block-summary"></span>
          </div>
          <div class="pl-block-actions">
            <button class="mini ghost pl-preview-base" style="width:auto;padding:3px 9px;margin:0;font-size:11px" title="Preview base source"><i data-lucide="eye" style="width:11px;height:11px"></i></button>
            <i data-lucide="chevron-down" class="pl-chevron"></i>
          </div>
        </div>
        <div class="pl-block-content">
          <div style="max-width:280px">${comboField('class="pl-base"', pdef.base ?? '', [])}</div>
          <label class="field grow" style="margin-top:10px">
            working CRS
            <input class="pl-working-crs" placeholder="defaults to base source's CRS" value="${pdef.working_crs ?? ''}">
          </label>
          <p class="hint" style="margin-top:6px"><i data-lucide="info" style="width:11px;height:11px;margin-right:4px;vertical-align:middle"></i>Internal CRS used for every join/step (spatial joins, buffer, clip, etc.) and for area/length calcs — pick a projected (metric) CRS if you use those. Every source is reprojected into this CRS before processing, then reprojected again to the output layer's CRS. Leave blank to use the base source's declared CRS.</p>
        </div>
      </div>

      <!-- Steps -->
      <div class="pl-block collapsed" data-sec="steps">
        <div class="pl-block-header">
          <div class="pl-block-left">
            <i data-lucide="git-merge" class="pl-block-icon" style="width:13px;height:13px;color:var(--spatial)"></i>
            <span class="pl-block-title">steps</span>
            <span class="pl-block-summary"></span>
          </div>
          <div class="pl-block-actions">
            <button class="addbtn pl-add-step" style="width:auto;padding:3px 9px;margin:0;font-size:11px"><i data-lucide="plus" style="width:11px;height:11px"></i> add step</button>
            <i data-lucide="chevron-down" class="pl-chevron"></i>
          </div>
        </div>
        <div class="pl-block-content">
          <div class="pl-steps"></div>
          <p class="hint" style="margin-top:6px">Optional — spatial or attribute joins to enrich base features</p>
        </div>
      </div>

      <!-- Mapping -->
      <div class="pl-block collapsed" data-sec="mapping">
        <div class="pl-block-header">
          <div class="pl-block-left">
            <i data-lucide="columns-2" class="pl-block-icon" style="width:13px;height:13px;color:var(--ok)"></i>
            <span class="pl-block-title">mapping</span>
            <span class="pl-block-summary"></span>
          </div>
          <div class="pl-block-actions">
            <button class="addbtn pl-add-map" style="width:auto;padding:3px 9px;margin:0;font-size:11px"><i data-lucide="plus" style="width:11px;height:11px"></i> column</button>
            <button class="mini ghost pl-auto-map" style="border:1px dashed var(--line);color:var(--muted);padding:3px 9px;" title="Auto-map all available columns"><i data-lucide="sparkles" style="width:11px;height:11px"></i></button>
            <i data-lucide="chevron-down" class="pl-chevron"></i>
          </div>
        </div>
        <div class="pl-block-content">
          <div class="auto-map-banner-container"></div>
          <div class="mapping-container-layout" style="display: flex; gap: 20px; align-items: stretch; min-height: 200px;">
            <!-- Left Side: Available Fields Pool -->
            <div class="pl-available-pool" style="width: 250px; flex-shrink: 0; background: var(--panel2); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 12px; display: flex; flex-direction: column; gap: 8px;">
              <div style="font-size: 10px; font-family: var(--mono); text-transform: uppercase; color: var(--muted); border-bottom: 1px solid var(--line); padding-bottom: 6px; font-weight: 600; display: flex; align-items: center; gap: 4px;">
                <i data-lucide="database" style="width: 12px; height: 12px;"></i> Available Source Fields
              </div>
              <div class="pl-available-fields-list" style="flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; max-height: 350px; padding-right: 4px;">
                <!-- Filled dynamically by updateDatalistsScoped -->
              </div>
            </div>

            <!-- Right Side: Mapped Fields Grid -->
            <div style="flex: 1; display: flex; flex-direction: column;">
              <div class="map-grid pl-map-header" style="margin-bottom:4px;padding:0 0 4px;border-bottom:1px solid var(--line)">
                <span></span><span class="head">output column</span><span class="head">kind</span>
                <span class="head">value</span><span class="head">cast</span><span></span>
              </div>
              <div class="pl-mapping" style="flex: 1; min-height: 100px;"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- Output layers -->
      <div class="pl-block collapsed" data-sec="output">
        <div class="pl-block-header">
          <div class="pl-block-left">
            <i data-lucide="package" class="pl-block-icon" style="width:13px;height:13px;color:var(--ok)"></i>
            <span class="pl-block-title">output layers</span>
            <span class="pl-block-summary"></span>
          </div>
          <div class="pl-block-actions">
            <button class="addbtn pl-add-layer" style="width:auto;padding:3px 9px;margin:0;font-size:11px"><i data-lucide="plus" style="width:11px;height:11px"></i> add layer</button>
            <i data-lucide="chevron-down" class="pl-chevron"></i>
          </div>
        </div>
        <div class="pl-block-content">
          <div class="pl-output-layers"></div>
          <p class="hint" style="margin-top:6px">Each layer writes the same upstream chain, optionally narrowed by its own filter — e.g. one layer for matched rows, another for unmatched.</p>
        </div>
      </div>
    </div>`

  // Scoped sync: refresh base options + datalists, then call global sync
  const scopedSync = () => {
    refreshBaseOptionsScoped(card)
    updateDatalistsScoped(card)
    syncFn()
  }
  ;(card as any)._scopedSync = scopedSync

  const expandBlock = (secName: string) => {
    card.querySelectorAll<HTMLElement>('.pl-block').forEach(b => {
      if (b.getAttribute('data-sec') === secName) {
        b.classList.remove('collapsed')
      } else {
        b.classList.add('collapsed')
      }
    })
  }

  const sourcesEl = card.querySelector<HTMLElement>('.pl-sources')!
  const derivedEl = card.querySelector<HTMLElement>('.pl-derived-sources')!
  const stepsEl = card.querySelector<HTMLElement>('.pl-steps')!
  const mappingEl = card.querySelector<HTMLElement>('.pl-mapping')!
  const layersEl = card.querySelector<HTMLElement>('.pl-output-layers')!

  // ---- auto-map banner ----
  const updateAutoMapBanner = () => {
    const bannerContainer = card.querySelector<HTMLElement>('.auto-map-banner-container')
    if (!bannerContainer) return

    const available = collectAvailableColumnsScoped(card)
    const mapped = new Set([...mappingEl.querySelectorAll('.map-item')]
      .map(w => w.querySelector<HTMLInputElement>('[data-to]')?.value.trim() || '').filter(Boolean))

    const unmapped = [...available].filter(c => !mapped.has(c))
    if (unmapped.length > 0) {
      bannerContainer.innerHTML = `
        <div class="auto-map-banner">
          <span>💡 <strong>${unmapped.length} schema columns</strong> are not mapped yet.</span>
          <button type="button" class="mini pl-banner-auto-map">Auto-map all</button>
        </div>`

      bannerContainer.querySelector('.pl-banner-auto-map')!.addEventListener('click', e => {
        e.stopPropagation()
        let added = 0
        available.forEach(col => {
          if (!mapped.has(col)) { mappingEl.appendChild(mapRow({ to: col, from: col }, fullSync, plId)); added++ }
        })
        if (added > 0) {
          refreshIcons()
          fullSync()
        }
      })
    } else {
      bannerContainer.innerHTML = ''
    }
  }

  // ---- sub-section collapse ----
  const updateSummaries = () => {
    const srcCount = sourcesEl.querySelectorAll('.card').length
    const derivedCount = derivedEl.querySelectorAll('.card').length
    const stepCount = stepsEl.querySelectorAll('.card').length
    const mapCount = mappingEl.querySelectorAll('.map-item').length
    const baseVal = card.querySelector<HTMLInputElement>('.pl-base')!.value
    const layerCards = [...layersEl.querySelectorAll<HTMLElement>('.card')]
    const layerNames = layerCards.map(c => c.querySelector<HTMLInputElement>('[data-k="layer"]')?.value.trim()).filter(Boolean)

    const setSummary = (sec: string, text: string) => {
      const el = card.querySelector<HTMLElement>(`.pl-block[data-sec="${sec}"] .pl-block-summary`)
      if (el) el.textContent = text
    }
    setSummary('sources', srcCount ? `${srcCount} source${srcCount !== 1 ? 's' : ''}` : '')
    setSummary('derived_sources', derivedCount ? `${derivedCount} derived` : '')
    setSummary('base', baseVal || '')
    setSummary('steps', stepCount ? `${stepCount} step${stepCount !== 1 ? 's' : ''}` : 'none')
    setSummary('mapping', mapCount ? `${mapCount} column${mapCount !== 1 ? 's' : ''}` : '')
    setSummary('output', layerNames.length ? layerNames.join(', ') : '')

    updateAutoMapBanner()
  }
  const fullSync = () => { scopedSync(); updateSummaries() }

  card.querySelectorAll<HTMLElement>('.pl-block').forEach(block => {
    const header = block.querySelector<HTMLElement>('.pl-block-header')!
    header.addEventListener('click', e => {
      if ((e.target as Element).closest('button,input,select')) return
      const isCollapsed = block.classList.contains('collapsed')
      if (isCollapsed) {
        expandBlock(block.getAttribute('data-sec') || '')
      } else {
        block.classList.add('collapsed')
      }
    })
  })

  // Wire base source preview button
  card.querySelector('.pl-preview-base')!.addEventListener('click', e => {
    e.stopPropagation()
    const plCards = Array.from(document.querySelectorAll('.pipeline-card'))
    const pipelineIdx = plCards.indexOf(card)
    card.dispatchEvent(new CustomEvent('preview-step', {
      bubbles: true,
      detail: { stepIdx: 0, pipelineIdx }
    }))
  })

  // ---- add buttons ----
  card.querySelector('.pl-add-source')!.addEventListener('click', e => {
    e.stopPropagation()
    // expand sources section if collapsed
    expandBlock('sources')
    const newCard = sourceCard({}, fullSync)
    newCard.classList.remove('collapsed')
    sourcesEl.querySelectorAll(':scope > .card').forEach(sibling => {
      if (sibling !== newCard) sibling.classList.add('collapsed')
    })
    sourcesEl.appendChild(newCard)
    refreshIcons()
    fullSync()
  })

  card.querySelector('.pl-add-derived')!.addEventListener('click', e => {
    e.stopPropagation()
    expandBlock('derived_sources')
    const newCard = derivedSourceCard({}, fullSync, collectAllSourceIdsScoped(card))
    newCard.classList.remove('collapsed')
    derivedEl.querySelectorAll(':scope > .card').forEach(sibling => {
      if (sibling !== newCard) sibling.classList.add('collapsed')
    })
    derivedEl.appendChild(newCard)
    refreshIcons()
    fullSync()
  })

  // Template buttons
  card.querySelectorAll<HTMLButtonElement>('.template-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation()
      const fmt = btn.dataset['fmt'] as Source['format']
      const uri = btn.dataset['uri'] || ''
      expandBlock('sources')
      const newCard = sourceCard({ format: fmt, uri }, fullSync)
      newCard.classList.remove('collapsed')
      sourcesEl.querySelectorAll(':scope > .card').forEach(sibling => {
        if (sibling !== newCard) sibling.classList.add('collapsed')
      })
      sourcesEl.appendChild(newCard)
      setTimeout(() => newCard.querySelector<HTMLInputElement>('[data-k="uri"]')?.focus(), 50)
      refreshIcons()
      fullSync()
    })
  })

  // Drag and Drop Zone
  const dropzone = card.querySelector<HTMLElement>('.sources-dropzone')!
  const fileInp = mkEl('input', { type: 'file', multiple: true }) as HTMLInputElement
  fileInp.style.display = 'none'
  dropzone.appendChild(fileInp)

  dropzone.addEventListener('click', e => {
    if (e.target === fileInp) return
    fileInp.click()
  })

  const uploadFiles = async (files: FileList) => {
    const loaderIcon = dropzone.querySelector('i')
    const originalText = dropzone.querySelector('.title')?.textContent

    if (loaderIcon) {
      loaderIcon.setAttribute('data-lucide', 'loader')
      loaderIcon.classList.add('spin-animation')
      createIcons({ icons: { Loader } })
    }
    const titleEl = dropzone.querySelector('.title')

    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      if (titleEl) titleEl.textContent = `Uploading ${file.name}...`

      try {
        const res = await uploadFile(file)
        if (res.ok && res.path) {
          const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
          const fmt = EXT_FORMAT[ext] || 'geojson'
          const idBase = file.name.substring(0, file.name.lastIndexOf('.')) || file.name
          const id = idBase.toLowerCase().replace(/[^a-z0-9_]/g, '_')

          const newSrc = { id, format: fmt as any, uri: res.path }

          card.querySelector('.pl-block[data-sec="sources"]')?.classList.remove('collapsed')
          const newCard = sourceCard(newSrc, fullSync)
          newCard.classList.remove('collapsed')
          sourcesEl.querySelectorAll(':scope > .card').forEach(sibling => {
            if (sibling !== newCard) sibling.classList.add('collapsed')
          })
          sourcesEl.appendChild(newCard)
          refreshIcons()
          fullSync()
          showToast(`Uploaded and added: ${file.name}`, 'ok')
        } else {
          showToast(`Upload failed: ${res.error || 'unknown error'}`, 'bad')
        }
      } catch (err) {
        showToast(`Upload failed: ${(err as Error).message}`, 'bad')
      }
    }

    if (loaderIcon) {
      loaderIcon.setAttribute('data-lucide', 'upload-cloud')
      loaderIcon.classList.remove('spin-animation')
      createIcons({ icons: { UploadCloud } })
    }
    if (titleEl && originalText) titleEl.textContent = originalText
  }

  fileInp.addEventListener('change', () => {
    if (fileInp.files && fileInp.files.length > 0) {
      uploadFiles(fileInp.files)
    }
  })

  dropzone.addEventListener('dragover', e => {
    e.preventDefault()
    e.stopPropagation()
    dropzone.classList.add('drag-active')
  })

  dropzone.addEventListener('dragleave', e => {
    e.preventDefault()
    e.stopPropagation()
    dropzone.classList.remove('drag-active')
  })

  dropzone.addEventListener('drop', e => {
    e.preventDefault()
    e.stopPropagation()
    dropzone.classList.remove('drag-active')
    if (e.dataTransfer?.files && e.dataTransfer.files.length > 0) {
      uploadFiles(e.dataTransfer.files)
    }
  })
  // Step picker gallery modal
  const addStepBtn = card.querySelector<HTMLButtonElement>('.pl-add-step')!
  addStepBtn.addEventListener('click', e => {
    e.stopPropagation()
    openStepGalleryModal(k => {
      expandBlock('steps')
      const newCard = stepCard(k, {}, fullSync, collectAllSourceIdsScoped(card))
      newCard.classList.remove('collapsed')
      stepsEl.querySelectorAll(':scope > .card').forEach(sibling => {
        if (sibling !== newCard) sibling.classList.add('collapsed')
      })
      stepsEl.appendChild(newCard)
      refreshIcons()
      fullSync()
    })
  })
  card.querySelector('.pl-add-map')!.addEventListener('click', e => {
    e.stopPropagation()
    expandBlock('mapping')
    mappingEl.appendChild(mapRow({}, scopedSync, plId))
    refreshIcons()
    scopedSync()
  })
  card.querySelector('.pl-auto-map')!.addEventListener('click', e => {
    e.stopPropagation()
    expandBlock('mapping')
    const available = collectAvailableColumnsScoped(card)
    const mapped = new Set([...mappingEl.querySelectorAll('.map-item')]
      .map(w => w.querySelector<HTMLInputElement>('[data-to]')?.value.trim() || '').filter(Boolean))
    let added = 0
    available.forEach(col => {
      if (!mapped.has(col)) { mappingEl.appendChild(mapRow({ to: col, from: col }, scopedSync, plId)); added++ }
    })
    if (added > 0) {
      refreshIcons()
      syncFn()
    }
  })

  card.querySelector('.pl-add-layer')!.addEventListener('click', e => {
    e.stopPropagation()
    expandBlock('output')
    const newCard = outputLayerCard({}, fullSync)
    newCard.classList.remove('collapsed')
    layersEl.querySelectorAll(':scope > .card').forEach(sibling => {
      if (sibling !== newCard) sibling.classList.add('collapsed')
    })
    layersEl.appendChild(newCard)
    refreshIcons()
    fullSync()
  })

  // ---- mapping drag-reorder ----
  mappingEl.addEventListener('dragover', e => {
    e.preventDefault()
    const dragging = mappingEl.querySelector<HTMLElement>('.map-item.dragging')
    if (!dragging) return
    const after = getDragAfterElement(mappingEl, e.clientY)
    if (after === null) mappingEl.appendChild(dragging)
    else if (after !== dragging.nextElementSibling) mappingEl.insertBefore(dragging, after)
  })
  mappingEl.addEventListener('drop', () => syncFn())

  // ---- remove pipeline ----
  card.querySelector('.pl-remove')!.addEventListener('click', e => {
    e.stopPropagation()
    card.remove(); syncFn()
  })

  // ---- top-level collapse ----
  card.querySelector('.pipeline-card-header')!.addEventListener('click', e => {
    if ((e.target as Element).closest('input,button,select')) return
    card.classList.toggle('collapsed')
  })

  // ---- input changes ----
  card.querySelector<HTMLInputElement>('.pl-name')!.addEventListener('input', syncFn)
  card.querySelector<HTMLInputElement>('.pl-working-crs')!.addEventListener('input', syncFn)
  wireCombos(card)
  card.querySelector<HTMLInputElement>('.pl-base')!.addEventListener('change', () => { scopedSync(); updateSummaries() })
  card.querySelector<HTMLInputElement>('.pl-base')!.addEventListener('input', () => { scopedSync(); updateSummaries() })



  // ---- hydrate (always stays collapsed — summaries show content at a glance) ----
  ;(pdef.sources || []).forEach(s => sourcesEl.appendChild(sourceCard(s, fullSync)))
  ;(pdef.derived_sources || []).forEach(ds => derivedEl.appendChild(derivedSourceCard(ds, fullSync, collectAllSourceIdsScoped(card))))
  ;(pdef.steps || []).forEach(st => stepsEl.appendChild(stepCard(st.type, st, fullSync, collectAllSourceIdsScoped(card))))
  ;(pdef.mapping || []).forEach(m => mappingEl.appendChild(mapRow(m, fullSync, plId)))
  ;(pdef.layers && pdef.layers.length ? pdef.layers : [{ layer: '', crs: 'EPSG:25833' }]).forEach(ol => layersEl.appendChild(outputLayerCard(ol, fullSync)))

  refreshBaseOptionsScoped(card)
  const baseEl = card.querySelector<HTMLInputElement>('.pl-base')!
  if (pdef.base) { ensureComboOption(baseEl, pdef.base); baseEl.value = pdef.base }

  updateSummaries()
  // createIcons is called by the caller after the card is in the DOM
  return card
}

// ---- collect ----
export function collectPipelineDef(card: HTMLElement): PipelineDef {
  const name = card.querySelector<HTMLInputElement>('.pl-name')!.value.trim() || 'pipeline'
  const sources = [...card.querySelectorAll('.pl-sources > .card')].map(c => {
    const s: Partial<Source> = { id: val(c, 'id'), format: val(c, 'format') as Source['format'], uri: val(c, 'uri') }
    if (val(c, 'layer')) s.layer = val(c, 'layer')
    if (val(c, 'crs')) s.crs = val(c, 'crs')
    const mv = c.querySelector<HTMLInputElement>('[data-k="make_valid"]')
    if (mv && !mv.checked) s.make_valid = false
    return s as Source
  }).filter(s => s.id && s.uri)

  const derived_sources = [...card.querySelectorAll('.pl-derived-sources > .card')].map(c => {
    const ds: Partial<DerivedSource> = { id: val(c, 'id'), from: val(c, 'from') }
    const where = val(c, 'where'); if (where) ds.where = where
    const buffer = val(c, 'buffer'); if (buffer) ds.buffer = parseFloat(buffer)
    const mv = c.querySelector<HTMLInputElement>('[data-k="make_valid"]')
    if (mv && !mv.checked) ds.make_valid = false
    return ds as DerivedSource
  }).filter(ds => ds.id && ds.from)

  const base = card.querySelector<HTMLInputElement>('.pl-base')!.value

  const steps = [...card.querySelectorAll<HTMLElement>('.pl-steps > .card')].map(c => {
    const t = c.dataset['type'] as Step['type']
    const st: Record<string, unknown> = { type: t }
    const branchVal = val(c, 'branch')
    if (branchVal) st['branch'] = branchVal
    if (['spatial_join', 'attribute_join', 'nearest_neighbor', 'clip', 'erase', 'intersect_overlay', 'merge'].includes(t)) {
      st['source'] = val(c, 'source')
    }
    if (['spatial_join', 'clip', 'erase'].includes(t)) {
      st['predicate'] = val(c, 'predicate') || 'intersects'
    }
    if (t === 'spatial_join') {
      st['match'] = val(c, 'match') || 'first'
    }
    if (t === 'filter') {
      st['where'] = val(c, 'where')
    }
    if (t === 'snapshot') {
      st['id'] = val(c, 'id')
    }
    if (t === 'attribute_join') {
      st['left'] = val(c, 'left')
      st['right'] = val(c, 'right')
    }
    if (t === 'nearest_neighbor') {
      const maxDist = val(c, 'max_distance')
      if (maxDist) st['max_distance'] = parseFloat(maxDist)
      const distField = val(c, 'distance_field').trim()
      if (distField) st['distance_field'] = distField
    }
    if (t === 'buffer') {
      st['distance'] = parseFloat(val(c, 'distance')) || 0
    }
    if (t === 'dissolve') {
      st['by'] = [...c.querySelectorAll<HTMLInputElement>('[data-by-col]')]
        .map(i => i.value.trim()).filter(Boolean)
    }
    if (['spatial_join', 'attribute_join', 'nearest_neighbor', 'intersect_overlay'].includes(t)) {
      const f: Record<string, string> = {}
      c.querySelectorAll('[data-fields] .kv').forEach(r => {
        const o = r.querySelector<HTMLInputElement>('[data-fo]')!.value.trim()
        const col = r.querySelector<HTMLInputElement>('[data-fc]')!.value.trim()
        if (o && col) f[o] = col
      })
      st['fields'] = f
    }
    return st as unknown as Step
  })

  const mapping = [...card.querySelectorAll('.pl-mapping > .map-item')]
    .map(w => (w as MapRowElement)._readMapping?.() ?? null)
    .filter((x): x is MapItem => x !== null)

  const layers = [...card.querySelectorAll<HTMLElement>('.pl-output-layers > .card')].map(c => {
    const ol: OutputLayer = {
      layer: (c.querySelector<HTMLInputElement>('[data-k="layer"]')?.value.trim() || 'output'),
      crs: (c.querySelector<HTMLInputElement>('[data-k="crs"]')?.value.trim() || 'EPSG:25833'),
    }
    const filterVal = c.querySelector<HTMLInputElement>('[data-k="filter"]')?.value.trim()
    if (filterVal) ol.filter = filterVal
    return ol
  })
  const workingCrs = card.querySelector<HTMLInputElement>('.pl-working-crs')!.value.trim()

  return {
    name, sources, ...(derived_sources.length ? { derived_sources } : {}),
    base, steps, mapping, layers, ...(workingCrs ? { working_crs: workingCrs } : {}),
  }
}

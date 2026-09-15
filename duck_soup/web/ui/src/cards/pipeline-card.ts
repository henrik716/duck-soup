import { createIcons, Loader, UploadCloud } from 'lucide'
import { uploadFile } from '../api'
import { mkEl, val, refreshIcons, getDragAfterElement, esc, qs, wireCollapse } from '../dom'
import { mutate } from '../history'
import { EXT_FORMAT } from '../state'
import { wireCombos } from '../combo'
import {
  refreshBaseOptionsScoped, updateDatalistsScoped,
  collectAvailableColumnsScoped, collectAllSourceIdsScoped, resolveSchemaScoped,
} from '../schema'
import { showToast } from '../toast'
import { attachCrsFormatCheck } from '../validation'
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
      <input class="pl-name" placeholder="pipeline name" value="${esc(pdef.name)}">
      <button class="mini danger ghost pl-remove" title="Remove pipeline" aria-label="Remove this entire pipeline"><i data-lucide="trash-2" style="width:12px;height:12px"></i></button>
      <i data-lucide="chevron-down" class="chevron" style="flex-shrink:0"></i>
    </div>
    <div class="pipeline-card-body">
      <div class="pl-datalists"></div>

      <div class="pl-tabs" role="tablist" aria-label="Pipeline sections">
        <button class="pl-tab-btn" type="button" role="tab" data-sec="sources" id="pl-tab-sources-${plId}" aria-controls="pl-panel-sources-${plId}">
          <i data-lucide="database" class="pl-tab-icon" style="width:13px;height:13px;color:var(--accent)"></i>
          <span class="pl-tab-title">sources</span>
          <span class="pl-tab-summary"></span>
        </button>
        <button class="pl-tab-btn" type="button" role="tab" data-sec="derived_sources" id="pl-tab-derived_sources-${plId}" aria-controls="pl-panel-derived_sources-${plId}">
          <i data-lucide="git-branch" class="pl-tab-icon" style="width:13px;height:13px;color:var(--accent)"></i>
          <span class="pl-tab-title">derived sources</span>
          <span class="pl-tab-summary"></span>
        </button>
        <button class="pl-tab-btn" type="button" role="tab" data-sec="steps" id="pl-tab-steps-${plId}" aria-controls="pl-panel-steps-${plId}">
          <i data-lucide="git-merge" class="pl-tab-icon" style="width:13px;height:13px;color:var(--spatial)"></i>
          <span class="pl-tab-title">steps</span>
          <span class="pl-tab-summary"></span>
        </button>
        <button class="pl-tab-btn" type="button" role="tab" data-sec="mapping" id="pl-tab-mapping-${plId}" aria-controls="pl-panel-mapping-${plId}">
          <i data-lucide="columns-2" class="pl-tab-icon" style="width:13px;height:13px;color:var(--ok)"></i>
          <span class="pl-tab-title">mapping</span>
          <span class="pl-tab-summary"></span>
        </button>
        <button class="pl-tab-btn" type="button" role="tab" data-sec="output" id="pl-tab-output-${plId}" aria-controls="pl-panel-output-${plId}">
          <i data-lucide="package" class="pl-tab-icon" style="width:13px;height:13px;color:var(--ok)"></i>
          <span class="pl-tab-title">output layers</span>
          <span class="pl-tab-summary"></span>
        </button>
      </div>

      <div class="pl-panels">
        <!-- Sources -->
        <div class="pl-panel" data-sec="sources" id="pl-panel-sources-${plId}" role="tabpanel" aria-labelledby="pl-tab-sources-${plId}">
          <div class="pl-panel-actions">
            <button class="mini ghost pl-preview-base" style="width:auto;padding:3px 9px;margin:0;font-size:11px" title="Preview base source" aria-label="Preview the base source before any steps"><i data-lucide="eye" style="width:11px;height:11px"></i></button>
            <button class="addbtn pl-add-source" style="width:auto;padding:3px 9px;margin:0;font-size:11px"><i data-lucide="plus" style="width:11px;height:11px"></i> add</button>
          </div>
          <input type="hidden" class="pl-base" value="${esc(pdef.base)}">
          <label class="field" style="max-width:280px;margin-bottom:10px">
            working CRS
            <input class="pl-working-crs" placeholder="defaults to base source's CRS" value="${esc(pdef.working_crs)}">
          </label>
          <p class="hint" style="margin-bottom:10px"><i data-lucide="info" style="width:11px;height:11px;margin-right:4px;vertical-align:middle"></i>Internal CRS used for every join/step (spatial joins, buffer, clip, etc.) and for area/length calcs — pick a projected (metric) CRS if you use those. Every source is reprojected into this CRS before processing, then reprojected again to the output layer's CRS. Leave blank to use the base source's declared CRS.</p>
          <p class="hint" style="margin-bottom:10px">Click the <i data-lucide="star" style="width:11px;height:11px;margin:0 2px;vertical-align:middle"></i> on a source below to make it the base — its features flow through the whole pipeline.</p>
          <div class="sources-dropzone" role="button" tabindex="0" aria-label="Add source files — drag and drop, or activate to browse your computer">
            <i data-lucide="upload-cloud" style="width:24px;height:24px;margin-bottom:4px"></i>
            <span class="title">Drag &amp; drop spatial files here</span>
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

        <!-- Derived sources -->
        <div class="pl-panel" data-sec="derived_sources" id="pl-panel-derived_sources-${plId}" role="tabpanel" aria-labelledby="pl-tab-derived_sources-${plId}">
          <div class="pl-panel-actions">
            <button class="addbtn pl-add-derived" style="width:auto;padding:3px 9px;margin:0;font-size:11px"><i data-lucide="plus" style="width:11px;height:11px"></i> add</button>
          </div>
          <div class="pl-derived-sources"></div>
          <div class="section-empty-hint" data-empty="derived_sources" hidden>
            <span class="hint-icon">🌿</span>
            <p>No derived sources. Optional — a <strong>filtered or buffered view</strong> of an existing source, usable anywhere a step needs a source.</p>
          </div>
          <p class="hint" style="margin-top:6px">Fork part of a source, transform it, then join it back into the base.</p>
        </div>

        <!-- Steps -->
        <div class="pl-panel" data-sec="steps" id="pl-panel-steps-${plId}" role="tabpanel" aria-labelledby="pl-tab-steps-${plId}">
          <div class="pl-panel-actions">
            <button class="addbtn pl-add-step" style="width:auto;padding:3px 9px;margin:0;font-size:11px"><i data-lucide="plus" style="width:11px;height:11px"></i> add step</button>
          </div>
          <div class="pl-steps"></div>
          <div class="section-empty-hint" data-empty="steps" hidden>
            <span class="hint-icon">🔗</span>
            <p>No steps yet — add one below to enrich, filter or reshape the base features.</p>
          </div>
          <button class="addbtn pl-add-step-bottom"><i data-lucide="plus" style="width:12px;height:12px"></i> add step</button>
        </div>

        <!-- Mapping -->
        <div class="pl-panel" data-sec="mapping" id="pl-panel-mapping-${plId}" role="tabpanel" aria-labelledby="pl-tab-mapping-${plId}">
          <div class="pl-panel-actions">
            <button class="addbtn pl-add-map" style="width:auto;padding:3px 9px;margin:0;font-size:11px"><i data-lucide="plus" style="width:11px;height:11px"></i> column</button>
            <button class="mini ghost pl-auto-map" style="border:1px dashed var(--line);color:var(--muted);padding:3px 9px;" title="Auto-map all available columns" aria-label="Map every available source column"><i data-lucide="sparkles" style="width:11px;height:11px"></i></button>
          </div>
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
                <span></span><span class="head">output column</span><span class="head">value source</span>
                <span class="head">value</span><span class="head">cast</span><span></span>
              </div>
              <div class="pl-mapping" style="flex: 1; min-height: 100px;"></div>
              <div class="section-empty-hint" data-empty="mapping" hidden>
                <span class="hint-icon">🧭</span>
                <p>No output columns yet — add one below, click a field in the pool on the left, or hit <strong>✨ auto-map</strong> above to map every available column at once.<br>With no mapping at all, every upstream column is written as-is.</p>
              </div>
              <button class="addbtn pl-add-map-bottom"><i data-lucide="plus" style="width:12px;height:12px"></i> column</button>
            </div>
          </div>
        </div>

        <!-- Output layers -->
        <div class="pl-panel" data-sec="output" id="pl-panel-output-${plId}" role="tabpanel" aria-labelledby="pl-tab-output-${plId}">
          <div class="pl-panel-actions">
            <button class="addbtn pl-add-layer" style="width:auto;padding:3px 9px;margin:0;font-size:11px"><i data-lucide="plus" style="width:11px;height:11px"></i> add layer</button>
          </div>
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

  // Sections are an exclusive tab bar — one visible at a time. (An earlier version kept them
  // as independently-collapsible blocks specifically so you could see e.g. a source and the
  // step consuming it simultaneously; switched to tabs on request, trading that away for a
  // shorter card per section.)
  const activateSection = (secName: string) => {
    card.querySelectorAll<HTMLButtonElement>('.pl-tab-btn').forEach(btn => {
      const active = btn.dataset['sec'] === secName
      btn.classList.toggle('active', active)
      btn.setAttribute('aria-selected', String(active))
      btn.tabIndex = active ? 0 : -1
    })
    card.querySelectorAll<HTMLElement>('.pl-panel').forEach(p => {
      p.classList.toggle('active', p.dataset['sec'] === secName)
    })
    persistActiveSection(secName)
  }
  ;(card as any)._activateSection = activateSection

  // Which section is showing is a working-layout preference, not config — remember it so it
  // survives a reload and an undo (which rebuilds this DOM wholesale).
  const blockStateKey = () => `ducksoup.blocks.${qs<HTMLInputElement>('#cfg_name')?.value.trim() || 'default'}`

  const persistActiveSection = (sec: string) => {
    try { localStorage.setItem(blockStateKey(), sec) } catch { /* private mode */ }
  }

  const KNOWN_SECTIONS = ['sources', 'derived_sources', 'steps', 'mapping', 'output']
  const restoreActiveSection = (): string => {
    // Older builds stored this key as a JSON array of open sections, not a single id — fall
    // back to the default rather than "activating" that raw string and matching no tab.
    let stored: string | null = null
    try { stored = localStorage.getItem(blockStateKey()) } catch { /* private mode */ }
    return stored && KNOWN_SECTIONS.includes(stored) ? stored : 'sources'
  }

  const sourcesEl = card.querySelector<HTMLElement>('.pl-sources')!
  const derivedEl = card.querySelector<HTMLElement>('.pl-derived-sources')!
  const stepsEl = card.querySelector<HTMLElement>('.pl-steps')!
  const mappingEl = card.querySelector<HTMLElement>('.pl-mapping')!
  const layersEl = card.querySelector<HTMLElement>('.pl-output-layers')!

  // ---- auto-map ----
  // One click can append dozens of rows that would otherwise have to be deleted one at a
  // time, so it snapshots first and offers a single-action undo.
  const autoMapAll = () => {
    const available = collectAvailableColumnsScoped(card)
    const mapped = new Set([...mappingEl.querySelectorAll('.map-item')]
      .map(w => w.querySelector<HTMLInputElement>('[data-to]')?.value.trim() || '').filter(Boolean))
    const unmapped = [...available].filter(col => !mapped.has(col))
    if (unmapped.length === 0) return

    mutate('auto-map columns', {
      undoToast: `Mapped ${unmapped.length} column${unmapped.length !== 1 ? 's' : ''}`,
    })
    unmapped.forEach(col => mappingEl.appendChild(mapRow({ to: col, from: col }, fullSync, plId)))
    refreshIcons()
    fullSync()
  }

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
        autoMapAll()
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
      const el = card.querySelector<HTMLElement>(`.pl-tab-btn[data-sec="${sec}"] .pl-tab-summary`)
      if (el) el.textContent = text
    }
    const srcSummary = srcCount ? `${srcCount} source${srcCount !== 1 ? 's' : ''}` : ''
    setSummary('sources', baseVal ? `${srcSummary} · base: ${baseVal}` : srcSummary)
    setSummary('derived_sources', derivedCount ? `${derivedCount} derived` : '')
    setSummary('steps', stepCount ? `${stepCount} step${stepCount !== 1 ? 's' : ''}` : 'none')
    setSummary('mapping', mapCount ? `${mapCount} column${mapCount !== 1 ? 's' : ''}` : '')
    setSummary('output', layerNames.length ? layerNames.join(', ') : '')

    // Empty sections get a real empty state instead of a blank box.
    const setEmpty = (name: string, empty: boolean) => {
      const el = card.querySelector<HTMLElement>(`.section-empty-hint[data-empty="${name}"]`)
      if (el) el.hidden = !empty
    }
    setEmpty('derived_sources', derivedCount === 0)
    setEmpty('steps', stepCount === 0)
    setEmpty('mapping', mapCount === 0)

    updateAutoMapBanner()
  }
  const fullSync = () => { scopedSync(); updateSummaries() }

  card.querySelectorAll<HTMLButtonElement>('.pl-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => activateSection(btn.dataset['sec'] ?? 'sources'))
  })
  // Roving tabindex: arrow keys move focus between tabs, matching the sidebar's own tab strip.
  card.querySelector<HTMLElement>('.pl-tabs')!.addEventListener('keydown', e => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    const tabs = [...card.querySelectorAll<HTMLButtonElement>('.pl-tab-btn')]
    const i = tabs.indexOf(document.activeElement as HTMLButtonElement)
    if (i < 0) return
    e.preventDefault()
    const next = tabs[(i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length]
    next.focus()
    activateSection(next.dataset['sec'] ?? 'sources')
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

  // A source card can't reach the pipeline-level `.pl-base` field directly (it's rendered
  // generically, with no pipeline scope) — it dispatches a bubbling event and this listens.
  sourcesEl.addEventListener('set-base', e => {
    const id = (e as CustomEvent<{ id: string }>).detail.id
    const baseEl = card.querySelector<HTMLInputElement>('.pl-base')!
    if (baseEl.value === id) return
    mutate('set base source')
    baseEl.value = id
    scopedSync()
    updateSummaries()
  })

  // ---- add buttons ----
  card.querySelector('.pl-add-source')!.addEventListener('click', e => {
    e.stopPropagation()
    mutate('add source')
    // expand sources section if collapsed
    activateSection('sources')
    const newCard = sourceCard({}, fullSync)
    newCard.classList.remove('collapsed')
    sourcesEl.appendChild(newCard)
    refreshIcons()
    fullSync()
  })

  card.querySelector('.pl-add-derived')!.addEventListener('click', e => {
    e.stopPropagation()
    mutate('add derived source')
    activateSection('derived_sources')
    const newCard = derivedSourceCard({}, fullSync, collectAllSourceIdsScoped(card))
    newCard.classList.remove('collapsed')
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
      mutate('add source')
      activateSection('sources')
      const newCard = sourceCard({ format: fmt, uri }, fullSync)
      newCard.classList.remove('collapsed')
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
  dropzone.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInp.click() }
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

          mutate('add uploaded source')
          activateSection('sources')
          const newCard = sourceCard(newSrc, fullSync)
          newCard.classList.remove('collapsed')
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
      mutate('add step')
      activateSection('steps')
      const newCard = stepCard(k, {}, fullSync, collectAllSourceIdsScoped(card))
      newCard.classList.remove('collapsed')
      stepsEl.appendChild(newCard)
      refreshIcons()
      fullSync()
      // The new card lands at the bottom of a list that's often taller than the viewport, so
      // adding one could look like nothing happened. Focus scrolls it into view for free.
      // Deferred because the gallery modal restores focus to its trigger as it closes.
      setTimeout(() => {
        const first = newCard.querySelector<HTMLInputElement>('.card-content input')
        if (first) first.focus()
        else newCard.scrollIntoView({ block: 'nearest' })
      }, 50)
    })
  })
  // A step card can't resolve a source id to its schema without importing schema.ts, which
  // would close an import cycle back through this module — so it asks here instead. Like
  // autoMapAll, the whole batch is one snapshot so a single undo reverses it.
  stepsEl.addEventListener('pull-all-fields', e => {
    const stepEl = e.target as HTMLElement & { _addField?: (out?: string, col?: string) => void }
    if (!stepEl._addField) return
    const srcId = (stepEl.querySelector<HTMLInputElement>('[data-k="source"]')?.value ?? '').trim()
    if (!srcId) { showToast('Pick a source for this step first', 'info'); return }

    const cols = resolveSchemaScoped(card, srcId)
    if (cols.length === 0) { showToast(`No inspected schema for "${srcId}"`, 'info'); return }

    const pulled = new Set([...stepEl.querySelectorAll<HTMLInputElement>('[data-fields] .kv [data-fc]')]
      .map(i => i.value.trim()).filter(Boolean))
    const missing = cols.map(c => c.name).filter(name => !pulled.has(name))
    if (missing.length === 0) { showToast('Every column is already pulled', 'info'); return }

    mutate('pull all fields', {
      undoToast: `Pulled ${missing.length} field${missing.length !== 1 ? 's' : ''}`,
    })
    missing.forEach(name => stepEl._addField!('', name))
    refreshIcons()
    fullSync()
  })

  card.querySelector('.pl-add-map')!.addEventListener('click', e => {
    e.stopPropagation()
    mutate('add mapping column')
    activateSection('mapping')
    const newRow = mapRow({}, scopedSync, plId)
    mappingEl.appendChild(newRow)
    refreshIcons()
    scopedSync()
    // Same reason as add-step: the row appends to the bottom, so without this, clicking add
    // from the collapsed-section header looks like it did nothing.
    setTimeout(() => newRow.querySelector<HTMLInputElement>('[data-to]')?.focus(), 50)
  })
  card.querySelector('.pl-auto-map')!.addEventListener('click', e => {
    e.stopPropagation()
    activateSection('mapping')
    autoMapAll()
  })

  card.querySelector('.pl-add-layer')!.addEventListener('click', e => {
    e.stopPropagation()
    mutate('add output layer')
    activateSection('output')
    const newCard = outputLayerCard({}, fullSync)
    newCard.classList.remove('collapsed')
    layersEl.appendChild(newCard)
    refreshIcons()
    fullSync()
  })

  // ---- step drag-reorder ----
  let stepDragSnapshotTaken = false
  stepsEl.addEventListener('dragover', e => {
    e.preventDefault()
    const dragging = stepsEl.querySelector<HTMLElement>('.card.dragging')
    if (!dragging) return
    if (!stepDragSnapshotTaken) { mutate('reorder step'); stepDragSnapshotTaken = true }

    const after = getDragAfterElement(stepsEl, e.clientY, '.card')
    stepsEl.querySelectorAll('.drop-target-above, .drop-target-below')
      .forEach(el => el.classList.remove('drop-target-above', 'drop-target-below'))
    if (after) after.classList.add('drop-target-above')
    else stepsEl.lastElementChild?.classList.add('drop-target-below')

    if (after === null) stepsEl.appendChild(dragging)
    else if (after !== dragging.nextElementSibling) stepsEl.insertBefore(dragging, after)
  })
  stepsEl.addEventListener('drop', () => {
    stepDragSnapshotTaken = false
    stepsEl.querySelectorAll('.drop-target-above, .drop-target-below')
      .forEach(el => el.classList.remove('drop-target-above', 'drop-target-below'))
    syncFn()
  })

  // ---- mapping drag-reorder ----
  let dragSnapshotTaken = false
  mappingEl.addEventListener('dragover', e => {
    e.preventDefault()
    const dragging = mappingEl.querySelector<HTMLElement>('.map-item.dragging')
    if (!dragging) return
    if (!dragSnapshotTaken) { mutate('reorder mapping'); dragSnapshotTaken = true }

    const after = getDragAfterElement(mappingEl, e.clientY)
    // A visible insertion line — previously the only feedback was the dragged row fading.
    mappingEl.querySelectorAll('.drop-target-above, .drop-target-below')
      .forEach(el => el.classList.remove('drop-target-above', 'drop-target-below'))
    if (after) after.classList.add('drop-target-above')
    else mappingEl.lastElementChild?.classList.add('drop-target-below')

    if (after === null) mappingEl.appendChild(dragging)
    else if (after !== dragging.nextElementSibling) mappingEl.insertBefore(dragging, after)
  })
  mappingEl.addEventListener('drop', () => {
    dragSnapshotTaken = false
    mappingEl.querySelectorAll('.drop-target-above, .drop-target-below')
      .forEach(el => el.classList.remove('drop-target-above', 'drop-target-below'))
    syncFn()
  })

  // ---- remove pipeline ----
  // The only delete in the app that takes a whole subtree with it — sources, steps, mapping
  // and layers all go at once — so this one gets a confirm rather than an undo toast.
  card.querySelector('.pl-remove')!.addEventListener('click', e => {
    e.stopPropagation()
    const name = card.querySelector<HTMLInputElement>('.pl-name')?.value.trim() || 'this pipeline'
    const counts = [
      `${sourcesEl.querySelectorAll('.card').length} source(s)`,
      `${stepsEl.querySelectorAll('.card').length} step(s)`,
      `${mappingEl.querySelectorAll('.map-item').length} mapped column(s)`,
    ].join(', ')
    if (!window.confirm(`Remove "${name}"?\n\nThis also removes its ${counts}.`)) return
    mutate('remove pipeline', { undoToast: `Removed pipeline "${name}"` })
    card.remove(); syncFn()
  })

  // ---- top-level collapse ----
  wireCollapse(card, {
    headerSel: '.pipeline-card-header',
    chevronSel: '.pipeline-card-header .chevron',
    bodySel: '.pipeline-card-body',
  })

  // ---- input changes ----
  card.querySelector<HTMLInputElement>('.pl-name')!.addEventListener('input', syncFn)
  card.querySelector<HTMLInputElement>('.pl-working-crs')!.addEventListener('input', syncFn)
  attachCrsFormatCheck(card.querySelector<HTMLInputElement>('.pl-working-crs')!)
  wireCombos(card)

  // ---- hydrate (always stays collapsed — summaries show content at a glance) ----
  ;(pdef.sources || []).forEach(s => sourcesEl.appendChild(sourceCard(s, fullSync)))
  ;(pdef.derived_sources || []).forEach(ds => derivedEl.appendChild(derivedSourceCard(ds, fullSync, collectAllSourceIdsScoped(card))))
  ;(pdef.steps || []).forEach(st => stepsEl.appendChild(stepCard(st.type, st, fullSync, collectAllSourceIdsScoped(card))))
  ;(pdef.mapping || []).forEach(m => mappingEl.appendChild(mapRow(m, fullSync, plId)))
  ;(pdef.layers && pdef.layers.length ? pdef.layers : [{ layer: '', crs: 'EPSG:25833' }]).forEach(ol => layersEl.appendChild(outputLayerCard(ol, fullSync)))

  refreshBaseOptionsScoped(card)

  // The header add buttons stay — they're reachable while the section is collapsed, and they
  // keep the six section headers uniform. But a new row appends to the bottom of a list that
  // can be several screens long, and that's exactly where you are when you want another one,
  // so each list also gets an add button where the row actually lands. Both delegate to the
  // single real handler rather than duplicating it.
  card.querySelector('.pl-add-step-bottom')?.addEventListener('click', e => {
    e.stopPropagation()
    ;(card.querySelector('.pl-add-step') as HTMLButtonElement | null)?.click()
  })
  card.querySelector('.pl-add-map-bottom')?.addEventListener('click', e => {
    e.stopPropagation()
    ;(card.querySelector('.pl-add-map') as HTMLButtonElement | null)?.click()
  })

  activateSection(restoreActiveSection())
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

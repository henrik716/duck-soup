import './styles.css'
import {
  createIcons,
  FolderOpen, X, ArrowRight, PlusCircle, Folder, Plus, Table, FileCode,
  Terminal, CheckSquare, Save, Play, Download, Copy, Trash2, Loader,
  Database, ChevronDown, Info, AlertTriangle, AlertCircle, CheckCircle,
  ArrowLeft, MapPin, Link, ArrowUp, ArrowDown, GripVertical, Maximize2,
  Crosshair, Scissors, Eraser, Layers, GitMerge, Radar, UploadCloud,
  Filter as FilterIcon, Combine, GitBranch, Camera,
  Columns2, Sparkles, Network, MapPinned, FileText
} from 'lucide'

const appIcons = {
  FolderOpen, X, ArrowRight, PlusCircle, Folder, Plus, Table, FileCode,
  Terminal, CheckSquare, Save, Play, Download, Copy, Trash2, Loader,
  Database, ChevronDown, Info, AlertTriangle, AlertCircle, CheckCircle,
  ArrowLeft, MapPin, Link, ArrowUp, ArrowDown, GripVertical, Maximize2,
  Crosshair, Scissors, Eraser, Layers, GitMerge, Radar, UploadCloud,
  Filter: FilterIcon, Combine, GitBranch, Camera,
  Columns2, Sparkles, Network, MapPinned, FileText
}
import {
  fetchMeta, fetchPipelineNames, fetchPipeline, savePipeline,
  validateConfig, previewConfig, runConfig,
} from './api'
import { initMap, updateMap } from './map'
import { updateLineageDiagram } from './lineage'
import { qs, mkEl } from './dom'
import { META } from './state'
import { pipelineCard } from './cards/pipeline-card'
import { buildMetadataSection } from './metadata'
import { collectConfig, hydrate } from './config-io'
import { showToast } from './toast'
import { closeFileExplorer, openFileExplorer } from './file-explorer'
import { updateTable, showTableError } from './table'
import type { Config, PreviewRow } from './types'

// ---- tab switch ----
function switchTab(tabId: string): void {
  const mode = tabId.replace('-tab', '')
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.toggle('active', btn.id === `tab-btn-${mode}`))
  document.querySelectorAll('.tab-content').forEach(content => content.classList.toggle('active', content.id === tabId))
}

let rawYaml = ''
let syncTimer = 0
let validateSeq = 0
let previewSeq = 0

// Global active preview state
let activePreview: { type: 'output' | 'source' | 'step'; id?: string; stepIdx?: number; pipelineIdx?: number } = { type: 'output' }

function highlightPreviewingStep(): void {
  // Clear any existing highlighted card classes
  clearStepHighlights()
  
  if (activePreview.type === 'step' && activePreview.stepIdx !== undefined) {
    const plCards = Array.from(document.querySelectorAll('.pipeline-card'))
    const plCard = plCards[activePreview.pipelineIdx ?? 0]
    if (!plCard) return
    
    if (activePreview.stepIdx === 0) {
      const baseBlock = plCard.querySelector('.pl-block[data-sec="base"]') as HTMLElement | null
      if (baseBlock) {
        baseBlock.style.borderColor = 'var(--accent)'
        baseBlock.style.boxShadow = '0 0 10px rgba(139, 108, 255, 0.4)'
      }
    } else {
      const stepCards = Array.from(plCard.querySelectorAll('.pl-steps > .card'))
      const stepCard = stepCards[activePreview.stepIdx - 1] as HTMLElement | null
      if (stepCard) {
        stepCard.style.borderColor = 'var(--accent)'
        stepCard.style.boxShadow = '0 0 10px rgba(139, 108, 255, 0.4)'
      }
    }
  }
}

function clearStepHighlights(): void {
  document.querySelectorAll<HTMLElement>('.pl-block[data-sec="base"]').forEach(b => {
    b.style.borderColor = ''
    b.style.boxShadow = ''
  })
  document.querySelectorAll<HTMLElement>('.pl-steps > .card').forEach(c => {
    c.style.borderColor = ''
    c.style.boxShadow = ''
  })
}

function updatePreviewBanner(): void {
  let banner = document.getElementById('preview-banner') as HTMLDivElement | null
  const tab = document.getElementById('table-tab')
  if (!tab) return
  
  if (activePreview.type === 'step') {
    if (!banner) {
      banner = document.createElement('div')
      banner.id = 'preview-banner'
      banner.style.cssText = 'background:var(--accent-soft);border:1px solid var(--accent-soft-border);border-radius:var(--radius-sm);padding:8px 12px;margin-bottom:10px;display:flex;align-items:center;justify-content:space-between;font-family:var(--display);font-size:12px;flex-shrink:0;'
      tab.insertBefore(banner, tab.firstChild)
    }
    const stepLabel = activePreview.stepIdx === 0 ? 'Base Source' : `Step ${activePreview.stepIdx}`
    banner.innerHTML = `
      <span>🔍 Showing intermediate preview for <strong>${stepLabel}</strong>. Output columns mapping is bypassed.</span>
      <button type="button" class="mini primary" id="reset-preview-btn" style="padding:4px 8px;font-size:11px;height:24px;line-height:1;">Reset preview</button>
    `
    document.getElementById('reset-preview-btn')?.addEventListener('click', () => {
      activePreview = { type: 'output' }
      updatePreviewBanner()
      clearStepHighlights()
      runPreview()
    })
    createIcons({ icons: appIcons })
  } else {
    if (banner) {
      banner.remove()
    }
  }
}

function updateMapLayerSelect(): void {
  const sel = document.getElementById('map-layer-select') as HTMLSelectElement | null
  if (!sel) return
  const cfg = collectConfig()
  const prev = sel.value
  sel.innerHTML = '<option value="">pipeline output</option>'
  cfg.pipelines.forEach(pdef => {
    pdef.sources.forEach(src => {
      if (!src.id) return
      const label = cfg.pipelines.length > 1 ? `${pdef.name} / ${src.id}` : src.id
      const opt = document.createElement('option')
      opt.value = src.id
      opt.textContent = label
      sel.appendChild(opt)
    })
  })
  if ([...sel.options].some(o => o.value === prev)) sel.value = prev
}

// ---- sync: runs after every form change ----
function sync(): void {
  updateMapLayerSelect()
  const cfg = collectConfig()
  updateLineageDiagram(cfg)
  clearTimeout(syncTimer)
  syncTimer = window.setTimeout(validate, 250)
}

// ---- validate ----
async function validate(): Promise<void> {
  const cfg = collectConfig()
  const seq = ++validateSeq
  setStatus('idle', 'validating…')
  try {
    const d = await validateConfig(cfg)
    if (seq !== validateSeq) return // a newer edit superseded this request
    if (d.ok && d.yaml) {
      rawYaml = d.yaml
      const lines = d.yaml.split('\n')
      const numbered = lines.map((line, idx) => {
        if (idx === lines.length - 1 && line === '') return ''
        const num = String(idx + 1).padStart(3, ' ')
        return `<span style="color:var(--muted);user-select:none;margin-right:12px;border-right:1px solid var(--line);padding-right:8px;">${num}</span>${line}`
      }).join('\n')
      qs<HTMLElement>('#yaml')!.innerHTML = numbered
      setStatus('ok', 'valid')
      runPreview()
    } else {
      setStatus('bad', (d.error || 'invalid').split('\n')[0])
    }
  } catch {
    setStatus('bad', 'server error')
  }
}

function setStatus(kind: 'idle' | 'ok' | 'bad', msg: string): void {
  const s = qs<HTMLElement>('#status')!
  s.className = `status ${kind}`
  s.textContent = msg
}

// ---- preview ----
async function runPreview(): Promise<void> {
  const cfg = collectConfig()
  if (!cfg.pipelines.length) return
  const seq = ++previewSeq

  let preview_until_step: number | undefined = undefined
  const pipelineIdx = activePreview.type === 'step' ? (activePreview.pipelineIdx ?? 0) : 0

  if (activePreview.type === 'step' && activePreview.stepIdx !== undefined) {
    preview_until_step = activePreview.stepIdx
  }

  highlightPreviewingStep()
  updatePreviewBanner()

  let activeStep: any = null
  if (activePreview.type === 'step' && activePreview.stepIdx !== undefined && activePreview.stepIdx > 0) {
    const pdef = cfg.pipelines[pipelineIdx]
    if (pdef && pdef.steps) {
      activeStep = pdef.steps[activePreview.stepIdx - 1]
    }
  }

  try {
    const d = await previewConfig(cfg, pipelineIdx, 50, preview_until_step)
    if (seq !== previewSeq) return // a newer edit superseded this request
    if (d.ok && d.rows) {
      updateMap(d.rows, activeStep)
      updateTable(d.rows as Record<string, unknown>[])
    } else {
      setStatus('bad', d.error || 'preview failed')
      showTableError(d.error || 'unknown error')
    }
  } catch (e) {
    console.error('Preview failed:', e)
    setStatus('bad', 'preview request failed')
    showTableError(String(e))
  }
}

async function previewMapSource(sourceId: string): Promise<void> {
  const cfg = collectConfig()
  // find which pipeline has this source
  for (const pdef of cfg.pipelines) {
    const src = pdef.sources.find(s => s.id === sourceId)
    if (!src) continue
    const fakeCfg: Config = {
      name: 'preview',
      output: 'preview.gpkg',
      pipelines: [{
        name: 'preview',
        sources: [src],
        base: sourceId,
        steps: [],
        mapping: [],
        layers: [{ layer: 'preview', crs: src.crs || 'EPSG:4326' }],
      }],
    }
    const seq = ++previewSeq
    try {
      const d = await previewConfig(fakeCfg, 0)
      if (seq !== previewSeq) return // a newer edit superseded this request
      if (d.ok && d.rows) {
        updateMap(d.rows as PreviewRow[])
        updateTable(d.rows as Record<string, unknown>[])
      } else {
        setStatus('bad', d.error || 'preview failed')
        showTableError(d.error || 'unknown error')
      }
    } catch (e) {
      console.error('Source preview failed:', e)
      setStatus('bad', 'preview request failed')
      showTableError(String(e))
    }
    return
  }
}

// ---- save ----
async function save(): Promise<void> {
  const cfg = collectConfig()
  const name = qs<HTMLInputElement>('#saveName')?.value.trim() || cfg.name
  const r = await savePipeline(name, cfg)
  if (r.ok) {
    setStatus('ok', `saved ${name}.yaml`)
    showToast(`Saved: ${name}`, 'ok')
    await loadList()
    const sel = qs<HTMLSelectElement>('#loadSelect')!
    sel.value = name
  } else {
    const d = await r.json()
    setStatus('bad', d.detail || 'save failed')
    showToast(d.detail || 'Save failed', 'bad')
  }
}

// ---- run ----
async function run(): Promise<void> {
  const cfg = collectConfig()
  const log = qs<HTMLElement>('#log')!
  log.innerHTML = ''
  setStatus('idle', 'running…')

  const runBtn = qs<HTMLButtonElement>('#runBtn')!
  const originalHtml = runBtn.innerHTML
  runBtn.disabled = true
  runBtn.innerHTML = `<i data-lucide="loader" class="spin-animation" style="width:14px;height:14px"></i> <span>running…</span>`
  createIcons({ icons: appIcons })

  const add = (t: string, cls = '') => {
    let html = t
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')

    // Highlight timings [0.45s]
    html = html.replace(/(\[\d+(?:\.\d+)?s\])/g, '<span class="log-time">$1</span>')

    // Highlight steps (step 1: spatial_join)
    html = html.replace(/(step \d+: \w+)/g, '<span class="log-keyword-step">$1</span>')

    // Highlight sources (source 'ambassader')
    html = html.replace(/(source '[^']+')/g, '<span class="log-keyword-src">$1</span>')

    // Highlight output path writing
    html = html.replace(/(writing \S+|→ wrote \S+)/g, '<span class="log-keyword-path">$1</span>')

    const d = mkEl('div', { className: `log-line ${cls}` })
    d.innerHTML = html
    log.appendChild(d)
    log.scrollTop = log.scrollHeight
  }

  add(`starting ${cfg.name} (${cfg.pipelines.length} pipeline${cfg.pipelines.length !== 1 ? 's' : ''}) …`)
  try {
    const d = await runConfig(cfg)
    ;(d.log || []).forEach(l => add(l))
    if (d.ok) {
      add(`→ wrote ${d.output}`, 'ok')
      setStatus('ok', 'run complete')
      showToast('Run completed!', 'ok')
    } else {
      add(`ERROR: ${d.error}`, 'bad')
      if (d.trace) add(d.trace.split('\n').slice(-4).join('\n'), 'bad')
      setStatus('bad', 'run failed')
      showToast('Run failed. Check logs.', 'bad')
    }
  } catch (e) {
    add(`request failed: ${e}`, 'bad')
    setStatus('bad', 'run failed')
    showToast('Network error during run', 'bad')
  } finally {
    runBtn.disabled = false
    runBtn.innerHTML = originalHtml
    createIcons({ icons: appIcons })
  }
}

// ---- load pipeline list ----
async function loadList(): Promise<void> {
  const names = await fetchPipelineNames()
  const sel = qs<HTMLSelectElement>('#loadSelect')!
  sel.innerHTML = '<option value="">— config —</option>' + names.map(n => `<option>${n}</option>`).join('')
}

async function loadPipelineConfig(name: string): Promise<void> {
  if (!name) return
  const d = await fetchPipeline(name)
  activePreview = { type: 'output' }
  updatePreviewBanner()
  clearStepHighlights()
  hydrate(d.config, sync)
  const saveNameEl = qs<HTMLInputElement>('#saveName')
  if (saveNameEl) saveNameEl.value = name
}

// ---- init ----
async function init(): Promise<void> {
  const meta = await fetchMeta()
  Object.assign(META, meta)

  initMap()

  document.getElementById('map-layer-select')?.addEventListener('change', e => {
    const v = (e.target as HTMLSelectElement).value
    if (v) previewMapSource(v); else runPreview()
  })

  // tab buttons
  document.querySelectorAll<HTMLButtonElement>('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset['tab']
      if (tab) switchTab(tab)
    })
  })

  // mount metadata section
  const metaMount = qs<HTMLElement>('#cfg-metadata-mount')
  if (metaMount) metaMount.appendChild(buildMetadataSection({}, sync))

  // cfg-level inputs
  qs('#cfg_name')?.addEventListener('input', sync)
  qs('#cfg_output')?.addEventListener('input', sync)

  // browse button for cfg output
  qs('#cfg_output_browse')?.addEventListener('click', () => {
    const inp = qs<HTMLInputElement>('#cfg_output')
    if (inp) openFileExplorer(inp)
  })

  // add pipeline button
  qs('#addPipeline')?.addEventListener('click', () => {
    const container = document.querySelector<HTMLElement>('#pipelines')!
    container.appendChild(pipelineCard({}, sync))
    createIcons({ icons: appIcons })
    sync()
  })

  qs('#copyYamlBtn')?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(rawYaml || qs<HTMLElement>('#yaml')!.textContent || '')
      showToast('Copied YAML to clipboard', 'ok')
    } catch { showToast('Failed to copy YAML', 'bad') }
  })

  qs('#clearLogBtn')?.addEventListener('click', () => {
    qs<HTMLElement>('#log')!.innerHTML = '<span class="log-line">log cleared</span>'
    showToast('Logs cleared', 'info')
  })

  qs('#closeFileModalBtn')?.addEventListener('click', closeFileExplorer)
  qs('#fileModal')?.addEventListener('click', e => {
    if (e.target === qs('#fileModal')) closeFileExplorer()
  })

  qs('#validateBtn')?.addEventListener('click', validate)
  qs('#saveBtn')?.addEventListener('click', save)
  qs('#runBtn')?.addEventListener('click', run)
  qs('#loadSelect')?.addEventListener('change', e => loadPipelineConfig((e.target as HTMLSelectElement).value))
  qs('#newBtn')?.addEventListener('click', () => {
    activePreview = { type: 'output' }
    updatePreviewBanner()
    clearStepHighlights()
    hydrate({
      name: 'new_config',
      output: 'output/data.gpkg',
      pipelines: [{ name: 'pipeline_1', sources: [], base: '', steps: [], mapping: [], layers: [{ layer: 'layer1', crs: 'EPSG:25833' }] }],
    }, sync)
  })

  // Wire step-preview custom event listener
  document.addEventListener('preview-step', (e: Event) => {
    const ce = e as CustomEvent<{ stepIdx: number; pipelineIdx: number }>
    activePreview = {
      type: 'step',
      stepIdx: ce.detail.stepIdx,
      pipelineIdx: ce.detail.pipelineIdx
    }
    runPreview()
  })

  await loadList()
  const names = [...qs<HTMLSelectElement>('#loadSelect')!.options].map(o => o.value).filter(Boolean)
  if (names.includes('embassies')) {
    qs<HTMLSelectElement>('#loadSelect')!.value = 'embassies'
    await loadPipelineConfig('embassies')
  } else if (names.length > 0) {
    const firstConfig = names[0]
    qs<HTMLSelectElement>('#loadSelect')!.value = firstConfig
    await loadPipelineConfig(firstConfig)
  } else {
    hydrate({
      name: 'new_config',
      output: 'output/data.gpkg',
      pipelines: [{ name: 'pipeline_1', sources: [], base: '', steps: [], mapping: [], layers: [{ layer: 'layer1', crs: 'EPSG:25833' }] }],
    }, sync)
  }

  createIcons({ icons: appIcons })
}

init()

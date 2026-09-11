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
import { initMap, updateMap, getMapBounds, onViewChange } from './map'
import { updateLineageDiagram } from './lineage'
import { qs, mkEl, esc } from './dom'
import { META } from './state'
import { pipelineCard } from './cards/pipeline-card'
import { buildMetadataSection } from './metadata'
import { collectConfig, hydrate } from './config-io'
import { showToast } from './toast'
import { closeFileExplorer, openFileExplorer } from './file-explorer'
import { updateTable, showTableError } from './table'
import { setPreviewBusy, resetPreviewBusy } from './busy'
import { initHistory, wireHistoryShortcuts, pushSnapshot, clearHistory } from './history'
import type { Config, PreviewRow } from './types'

const LAST_CONFIG_KEY = 'ducksoup.lastConfig'

const BLANK_CONFIG: Config = {
  name: 'new_config',
  output: 'output/data.gpkg',
  pipelines: [{ name: 'pipeline_1', sources: [], base: '', steps: [], mapping: [], layers: [{ layer: 'layer1', crs: 'EPSG:25833' }] }],
}

// ---- tabs ----
function switchTab(tabId: string): void {
  document.querySelectorAll<HTMLButtonElement>('.tab-btn').forEach(btn => {
    const active = btn.dataset['tab'] === tabId
    btn.classList.toggle('active', active)
    btn.setAttribute('aria-selected', String(active))
    btn.tabIndex = active ? 0 : -1
  })
  document.querySelectorAll('.tab-content').forEach(content => content.classList.toggle('active', content.id === tabId))
}

function activeTab(): string {
  return document.querySelector<HTMLButtonElement>('.tab-btn.active')?.dataset['tab'] ?? 'table-tab'
}

let rawYaml = ''
let syncTimer = 0
let validateSeq = 0
let previewSeq = 0
let previewLimit = 1000
let bboxMode = false
let bboxMoveTimer = 0
let lastErrorDetail = ''
let lastSavedJson = ''
let loadedConfigName = ''

// Global active preview state. This is the single source of truth for what the table and
// map are showing; the map toolbar's "view" dropdown writes into it rather than
// short-circuiting the preview path, which is what used to make the target unpredictable.
let activePreview: { type: 'output' | 'source' | 'step'; id?: string; stepIdx?: number; pipelineIdx?: number } = { type: 'output' }

// ---- dirty tracking ----
function currentJson(): string {
  try { return JSON.stringify(collectConfig()) } catch { return '' }
}

function isDirty(): boolean {
  return lastSavedJson !== '' && currentJson() !== lastSavedJson
}

function markSaved(): void {
  lastSavedJson = currentJson()
  updateDirtyIndicator()
}

function updateDirtyIndicator(): void {
  const btn = qs<HTMLButtonElement>('#saveBtn')
  const dirty = isDirty()
  btn?.classList.toggle('dirty', dirty)
  if (btn) btn.title = dirty ? 'Save config — you have unsaved changes' : 'Save config'
}

/** Ask before throwing away unsaved work. Returns true if it's safe to proceed. */
function confirmDiscard(action: string): boolean {
  if (!isDirty()) return true
  return window.confirm(`You have unsaved changes. ${action} and lose them?`)
}

// ---- preview highlighting ----
function highlightPreviewingStep(): void {
  clearStepHighlights()
  if (activePreview.type !== 'step' || activePreview.stepIdx === undefined) return

  const plCards = Array.from(document.querySelectorAll('.pipeline-card'))
  const plCard = plCards[activePreview.pipelineIdx ?? 0]
  if (!plCard) return

  const target = activePreview.stepIdx === 0
    ? plCard.querySelector<HTMLElement>('.pl-block[data-sec="base"]')
    : Array.from(plCard.querySelectorAll<HTMLElement>('.pl-steps > .card'))[activePreview.stepIdx - 1]
  target?.classList.add('is-previewing')
}

function clearStepHighlights(): void {
  document.querySelectorAll<HTMLElement>('.is-previewing').forEach(el => el.classList.remove('is-previewing'))
}

function previewLabel(): string | null {
  const cfg = collectConfig()
  const multi = cfg.pipelines.length > 1
  const plName = cfg.pipelines[activePreview.pipelineIdx ?? 0]?.name

  if (activePreview.type === 'step') {
    const step = activePreview.stepIdx === 0 ? 'Base source' : `Step ${activePreview.stepIdx}`
    return `${step}${multi && plName ? ` of ${plName}` : ''} — intermediate rows, output mapping is bypassed`
  }
  if (activePreview.type === 'source') {
    return `Source <strong>${esc(activePreview.id ?? '')}</strong> — raw features, no steps or mapping applied`
  }
  // Output previews are the default and only worth calling out when it's ambiguous which
  // pipeline's output you're looking at.
  if (multi && plName) return `Output of pipeline <strong>${esc(plName)}</strong>`
  return null
}

function updatePreviewBanner(): void {
  const tab = document.getElementById('table-tab')
  if (!tab) return
  let banner = document.getElementById('preview-banner') as HTMLDivElement | null
  const label = previewLabel()

  if (!label) {
    banner?.remove()
    return
  }
  if (!banner) {
    banner = document.createElement('div')
    banner.id = 'preview-banner'
    banner.style.cssText = 'background:var(--accent-soft);border:1px solid var(--accent-soft-border);border-radius:var(--radius-sm);padding:8px 12px;margin-bottom:10px;display:flex;align-items:center;justify-content:space-between;gap:10px;font-family:var(--display);font-size:12px;flex-shrink:0;'
    tab.insertBefore(banner, tab.firstChild)
  }
  const resettable = activePreview.type !== 'output'
  banner.innerHTML =
    `<span>${label}</span>` +
    (resettable ? `<button type="button" class="mini primary" id="reset-preview-btn" style="padding:4px 8px;font-size:11px;height:24px;line-height:1;flex-shrink:0">Show pipeline output</button>` : '')

  document.getElementById('reset-preview-btn')?.addEventListener('click', () => {
    const sel = qs<HTMLSelectElement>('#map-layer-select')
    if (sel) sel.value = ''
    setActivePreview({ type: 'output', pipelineIdx: activePreview.pipelineIdx })
  })
  createIcons({ icons: appIcons })
}

/** The one way to change what's being previewed. */
function setActivePreview(next: typeof activePreview): void {
  activePreview = next
  highlightPreviewingStep()
  updatePreviewBanner()
  runPreview()
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
  else if (prev && activePreview.type === 'source') {
    // The source being previewed was renamed or removed — fall back to the output.
    activePreview = { type: 'output', pipelineIdx: activePreview.pipelineIdx }
    updatePreviewBanner()
  }
}

// ---- sync: runs after every form change ----
function sync(): void {
  updateMapLayerSelect()
  const cfg = collectConfig()
  updateLineageDiagram(cfg)
  updateDirtyIndicator()
  clearTimeout(syncTimer)
  syncTimer = window.setTimeout(validate, 250)
}

// ---- validate ----
async function validate(): Promise<void> {
  const cfg = collectConfig()
  const seq = ++validateSeq
  setStatus('busy', 'validating…')
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
      setStatus('bad', (d.error || 'invalid').split('\n')[0], d.error || 'invalid')
    }
  } catch {
    setStatus('bad', 'server error', 'The validation request did not reach the server. Is the backend still running?')
  }
}

function setStatus(kind: 'idle' | 'ok' | 'bad' | 'busy', msg: string, detail?: string): void {
  const s = qs<HTMLButtonElement>('#status')!
  s.className = `status ${kind}`
  s.textContent = msg

  const hadProblem = lastErrorDetail !== ''
  lastErrorDetail = kind === 'bad' ? (detail ?? msg) : ''
  s.title = lastErrorDetail
    ? `${lastErrorDetail}\n\n(click to open the problems panel)`
    : 'Validation status'

  renderProblems()

  // Reveal the panel when a problem first appears, but don't yank the user out of
  // whatever tab they're on for every re-validate of an already-broken config.
  if (kind === 'bad' && !hadProblem && activeTab() === 'table-tab') switchTab('problems-tab')
}

// ---- problems panel ----
// Pydantic flattens its ValidationError to a string before it reaches us, but the loc
// paths survive in the text ("pipelines.0.steps.3.source" on its own line, followed by an
// indented message). Recovering them lets a problem link back to the card that caused it.
const LOC_RE = /^([A-Za-z_][\w]*(?:\.[\w]+)+)$/

interface Problem { path: string; msg: string }

function parseProblems(detail: string): Problem[] {
  const lines = detail.split('\n')
  const out: Problem[] = []
  lines.forEach((line, i) => {
    const m = line.trim().match(LOC_RE)
    if (!m) return
    const next = (lines[i + 1] ?? '').trim()
    // Skip the trailing "For further information visit ..." noise.
    const msg = next.startsWith('For further information') ? '' : next.replace(/\s*\[type=.*$/, '')
    out.push({ path: m[1], msg })
  })
  return out
}

const SECTION_FOR_FIELD: Record<string, string> = {
  sources: 'sources',
  derived_sources: 'derived_sources',
  steps: 'steps',
  mapping: 'mapping',
  layers: 'output',
  base: 'base',
  working_crs: 'base',
}

const CARDS_SELECTOR: Record<string, string> = {
  sources: '.pl-sources > .card',
  derived_sources: '.pl-derived-sources > .card',
  steps: '.pl-steps > .card',
  mapping: '.pl-mapping > .map-item',
  output: '.pl-output-layers > .card',
}

function flash(el: HTMLElement): void {
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('highlight-flash')
  setTimeout(() => el.classList.remove('highlight-flash'), 1500)
}

/** Navigate to whatever a Pydantic loc path like `pipelines.0.steps.3.source` points at. */
function gotoProblem(path: string): void {
  const parts = path.split('.')
  if (parts[0] !== 'pipelines') return

  const plCard = Array.from(document.querySelectorAll<HTMLElement>('.pipeline-card'))[Number(parts[1])]
  if (!plCard) return
  plCard.classList.remove('collapsed')

  const sec = SECTION_FOR_FIELD[parts[2] ?? '']
  if (!sec) { flash(plCard); return }

  const block = plCard.querySelector<HTMLElement>(`.pl-block[data-sec="${sec}"]`)
  block?.classList.remove('collapsed')

  const idx = Number(parts[3])
  const selector = CARDS_SELECTOR[sec]
  if (block && selector && Number.isInteger(idx)) {
    const card = Array.from(plCard.querySelectorAll<HTMLElement>(selector))[idx]
    if (card) {
      card.classList.remove('collapsed')
      flash(card)
      return
    }
  }
  if (block) flash(block)
}

function renderProblems(): void {
  const detailEl = qs<HTMLElement>('#problemsDetail')
  const locEl = qs<HTMLElement>('#problemsLocators')
  const badge = qs<HTMLElement>('#problemsBadge')
  if (!detailEl || !locEl) return

  if (!lastErrorDetail) {
    detailEl.textContent = 'No problems — the config is valid.'
    locEl.innerHTML = ''
    if (badge) badge.hidden = true
    return
  }

  detailEl.textContent = lastErrorDetail
  const problems = parseProblems(lastErrorDetail)

  if (badge) {
    badge.hidden = problems.length === 0
    badge.textContent = String(problems.length)
  }

  locEl.innerHTML = problems.map(p =>
    `<button type="button" class="problem-locator" data-path="${esc(p.path)}">` +
    `<span class="loc-path">${esc(p.path)}</span>` +
    (p.msg ? `<span class="loc-msg">${esc(p.msg)}</span>` : '') +
    `</button>`
  ).join('')

  locEl.querySelectorAll<HTMLButtonElement>('.problem-locator').forEach(btn => {
    btn.addEventListener('click', () => gotoProblem(btn.dataset['path'] ?? ''))
  })
}

// ---- preview ----
async function runPreview(): Promise<void> {
  const cfg = collectConfig()
  if (!cfg.pipelines.length) return

  if (activePreview.type === 'source' && activePreview.id) {
    return previewSource(activePreview.id, cfg)
  }

  const seq = ++previewSeq
  const pipelineIdx = Math.min(activePreview.pipelineIdx ?? 0, cfg.pipelines.length - 1)
  const preview_until_step = activePreview.type === 'step' ? activePreview.stepIdx : undefined

  // The buffer/nearest-neighbour radius overlay needs the step being previewed.
  let activeStep: unknown = null
  if (activePreview.type === 'step' && (activePreview.stepIdx ?? 0) > 0) {
    activeStep = cfg.pipelines[pipelineIdx]?.steps?.[(activePreview.stepIdx as number) - 1] ?? null
  }

  const bbox = bboxMode ? getMapBounds() ?? undefined : undefined

  setPreviewBusy(true)
  try {
    const d = await previewConfig(cfg, pipelineIdx, previewLimit, preview_until_step, bbox)
    if (seq !== previewSeq) return // a newer edit superseded this request
    if (d.ok && d.rows) {
      updateMap(d.rows, activeStep, { fitBounds: !bboxMode })
      updateTable(d.rows as Record<string, unknown>[], previewLimit)
    } else {
      setStatus('bad', d.error?.split('\n')[0] || 'preview failed', d.error || 'unknown error')
      showTableError(d.error || 'unknown error')
    }
  } catch (e) {
    console.error('Preview failed:', e)
    setStatus('bad', 'preview request failed', String(e))
    showTableError(String(e))
  } finally {
    setPreviewBusy(false)
  }
}

/** Preview a single source on its own, with no steps or mapping applied. */
async function previewSource(sourceId: string, cfg: Config): Promise<void> {
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
    const bbox = bboxMode ? getMapBounds() ?? undefined : undefined

    setPreviewBusy(true)
    try {
      const d = await previewConfig(fakeCfg, 0, previewLimit, undefined, bbox)
      if (seq !== previewSeq) return
      if (d.ok && d.rows) {
        updateMap(d.rows as PreviewRow[], undefined, { fitBounds: !bboxMode })
        updateTable(d.rows as Record<string, unknown>[], previewLimit)
      } else {
        setStatus('bad', d.error?.split('\n')[0] || 'preview failed', d.error || 'unknown error')
        showTableError(d.error || 'unknown error')
      }
    } catch (e) {
      console.error('Source preview failed:', e)
      setStatus('bad', 'preview request failed', String(e))
      showTableError(String(e))
    } finally {
      setPreviewBusy(false)
    }
    return
  }
}

// ---- save ----
async function save(): Promise<void> {
  const cfg = collectConfig()
  const name = qs<HTMLInputElement>('#saveName')?.value.trim() || cfg.name

  // Saving over a *different* existing config silently replaced it. Re-saving the config
  // you currently have open is the normal case and shouldn't prompt.
  const existing = [...(qs<HTMLSelectElement>('#loadSelect')?.options ?? [])].map(o => o.value).filter(Boolean)
  if (name !== loadedConfigName && existing.includes(name)) {
    if (!window.confirm(`A config named "${name}" already exists. Overwrite it?`)) return
  }

  const r = await savePipeline(name, cfg)
  if (r.ok) {
    loadedConfigName = name
    try { localStorage.setItem(LAST_CONFIG_KEY, name) } catch { /* private mode */ }
    setStatus('ok', `saved ${name}.yaml`)
    showToast(`Saved: ${name}`, 'ok')
    await loadList()
    const sel = qs<HTMLSelectElement>('#loadSelect')!
    sel.value = name
    markSaved()
  } else {
    const d = await r.json()
    setStatus('bad', d.detail?.split('\n')[0] || 'save failed', d.detail || 'save failed')
    showToast(d.detail || 'Save failed', 'bad')
  }
}

// ---- run ----
async function run(): Promise<void> {
  const cfg = collectConfig()
  const log = qs<HTMLElement>('#log')!
  setStatus('busy', 'running…')

  // A run you can't see is a run you can't debug. Previously a failure only toasted
  // "check logs" without ever showing them.
  switchTab('log-tab')

  const runBtn = qs<HTMLButtonElement>('#runBtn')!
  const originalHtml = runBtn.innerHTML
  runBtn.disabled = true

  const startedAt = Date.now()
  const renderBtn = () => {
    const secs = Math.floor((Date.now() - startedAt) / 1000)
    runBtn.innerHTML = `<i data-lucide="loader" class="spin-animation" style="width:14px;height:14px"></i> <span>running… ${secs}s</span>`
    createIcons({ icons: appIcons })
  }
  renderBtn()
  const ticker = window.setInterval(renderBtn, 1000)

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

  // Append rather than clear, so two runs can be compared.
  if (log.textContent?.trim() && log.textContent.trim() !== 'no run yet') {
    add('────────────────────────────')
  } else {
    log.innerHTML = ''
  }
  const stamp = new Date().toLocaleTimeString()
  add(`[${stamp}] starting ${cfg.name} (${cfg.pipelines.length} pipeline${cfg.pipelines.length !== 1 ? 's' : ''}) …`)

  try {
    const d = await runConfig(cfg)
    ;(d.log || []).forEach(l => add(l))
    const elapsed = ((Date.now() - startedAt) / 1000).toFixed(1)
    if (d.ok) {
      add(`→ wrote ${d.output} [${elapsed}s]`, 'ok')
      setStatus('ok', 'run complete')
      showToast('Run completed!', 'ok')
    } else {
      add(`ERROR: ${d.error}`, 'bad')
      if (d.trace) add(d.trace.split('\n').slice(-4).join('\n'), 'bad')
      setStatus('bad', 'run failed', d.error || 'run failed')
      showToast('Run failed — see the Run Logs tab.', 'bad')
    }
  } catch (e) {
    add(`request failed: ${e}`, 'bad')
    setStatus('bad', 'run failed', String(e))
    showToast('Network error during run', 'bad')
  } finally {
    clearInterval(ticker)
    runBtn.disabled = false
    runBtn.innerHTML = originalHtml
    createIcons({ icons: appIcons })
  }
}

// ---- load pipeline list ----
async function loadList(): Promise<void> {
  const names = await fetchPipelineNames()
  const sel = qs<HTMLSelectElement>('#loadSelect')!
  sel.innerHTML = '<option value="">— config —</option>' + names.map(n => `<option>${esc(n)}</option>`).join('')
}

async function loadPipelineConfig(name: string): Promise<void> {
  if (!name) return
  let d
  try {
    d = await fetchPipeline(name)
  } catch (e) {
    setStatus('bad', 'load failed', String(e instanceof Error ? e.message : e))
    showToast(String(e instanceof Error ? e.message : e), 'bad')
    return
  }
  activePreview = { type: 'output' }
  clearStepHighlights()
  resetPreviewBusy()
  clearHistory()
  hydrate(d.config, sync)
  updatePreviewBanner()

  loadedConfigName = name
  try { localStorage.setItem(LAST_CONFIG_KEY, name) } catch { /* private mode */ }
  const saveNameEl = qs<HTMLInputElement>('#saveName')
  if (saveNameEl) saveNameEl.value = name
  markSaved()

  if (d.warning) {
    showToast(`Loaded '${name}' but it has a validation problem: ${d.warning}`, 'bad')
  }
}

function newConfig(): void {
  activePreview = { type: 'output' }
  clearStepHighlights()
  resetPreviewBusy()
  clearHistory()
  hydrate(structuredClone(BLANK_CONFIG), sync)
  updatePreviewBanner()
  loadedConfigName = ''
  const saveNameEl = qs<HTMLInputElement>('#saveName')
  if (saveNameEl) saveNameEl.value = ''
  markSaved()
}

// ---- init ----
async function init(): Promise<void> {
  setStatus('busy', 'loading…')

  const meta = await fetchMeta()
  Object.assign(META, meta)

  initMap()
  initHistory(sync)
  wireHistoryShortcuts()

  // Track which pipeline card the user is actually working in, so the output preview
  // follows edits to pipeline 2+ instead of always showing pipeline 1's output. Left alone
  // during an explicit source/step preview, whose pipelineIdx was chosen deliberately.
  document.addEventListener('focusin', e => {
    if (activePreview.type !== 'output') return
    const card = (e.target as HTMLElement).closest?.('.pipeline-card')
    if (!card) return
    const idx = Array.from(document.querySelectorAll('.pipeline-card')).indexOf(card)
    if (idx >= 0 && activePreview.pipelineIdx !== idx) {
      activePreview.pipelineIdx = idx
      updatePreviewBanner()
    }
  })

  document.getElementById('map-layer-select')?.addEventListener('change', e => {
    const id = (e.target as HTMLSelectElement).value
    setActivePreview(id
      ? { type: 'source', id, pipelineIdx: activePreview.pipelineIdx }
      : { type: 'output', pipelineIdx: activePreview.pipelineIdx })
  })

  const limitInput = qs<HTMLInputElement>('#previewLimit')
  limitInput?.addEventListener('change', () => {
    const n = Math.max(1, Math.min(20000, Math.round(Number(limitInput.value)) || 1000))
    limitInput.value = String(n)
    previewLimit = n
    runPreview()
  })

  const bboxBtn = qs<HTMLButtonElement>('#bboxToggleBtn')
  const setBboxBtnVisual = () => {
    if (!bboxBtn) return
    bboxBtn.style.background = bboxMode ? 'var(--accent)' : ''
    bboxBtn.style.color = bboxMode ? '#0c0822' : ''
    bboxBtn.style.fontWeight = bboxMode ? '600' : ''
    bboxBtn.setAttribute('aria-pressed', String(bboxMode))
  }
  bboxBtn?.addEventListener('click', () => {
    bboxMode = !bboxMode
    setBboxBtnVisual()
    runPreview()
  })

  // While "in view" is on, re-preview (debounced) whenever the user finishes panning/zooming.
  onViewChange(() => {
    if (!bboxMode) return
    clearTimeout(bboxMoveTimer)
    bboxMoveTimer = window.setTimeout(runPreview, 300)
  })

  // tab buttons — click plus roving-tabindex arrow navigation
  const tabBtns = [...document.querySelectorAll<HTMLButtonElement>('.tab-btn')]
  tabBtns.forEach((btn, i) => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset['tab']
      if (tab) switchTab(tab)
    })
    btn.addEventListener('keydown', e => {
      const map: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1 }
      let next = -1
      if (e.key in map) next = (i + map[e.key] + tabBtns.length) % tabBtns.length
      else if (e.key === 'Home') next = 0
      else if (e.key === 'End') next = tabBtns.length - 1
      if (next < 0) return
      e.preventDefault()
      const target = tabBtns[next]
      switchTab(target.dataset['tab'] ?? '')
      target.focus()
    })
  })

  qs('#status')?.addEventListener('click', () => switchTab('problems-tab'))

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
    pushSnapshot('add pipeline')
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

  const loadSel = qs<HTMLSelectElement>('#loadSelect')
  loadSel?.addEventListener('change', e => {
    const name = (e.target as HTMLSelectElement).value
    if (!confirmDiscard(`Load "${name}"`)) {
      // Put the dropdown back where it was rather than leaving it lying about what's open.
      if (loadSel) loadSel.value = loadedConfigName
      return
    }
    loadPipelineConfig(name)
  })

  qs('#newBtn')?.addEventListener('click', () => {
    if (!confirmDiscard('Start a new config')) return
    if (loadSel) loadSel.value = ''
    newConfig()
  })

  window.addEventListener('beforeunload', e => {
    if (!isDirty()) return
    e.preventDefault()
    e.returnValue = ''
  })

  // Wire step-preview custom event listener
  document.addEventListener('preview-step', (e: Event) => {
    const ce = e as CustomEvent<{ stepIdx: number; pipelineIdx: number }>
    const sel = qs<HTMLSelectElement>('#map-layer-select')
    if (sel) sel.value = ''
    setActivePreview({ type: 'step', stepIdx: ce.detail.stepIdx, pipelineIdx: ce.detail.pipelineIdx })
  })

  await loadList()
  const names = [...qs<HTMLSelectElement>('#loadSelect')!.options].map(o => o.value).filter(Boolean)
  let remembered = ''
  try { remembered = localStorage.getItem(LAST_CONFIG_KEY) ?? '' } catch { /* private mode */ }

  const initial = names.includes(remembered) ? remembered : names[0]
  if (initial) {
    qs<HTMLSelectElement>('#loadSelect')!.value = initial
    await loadPipelineConfig(initial)
  } else {
    hydrate(structuredClone(BLANK_CONFIG), sync)
    markSaved()
    showToast('No saved configs yet — add a source to get started.', 'info')
  }

  createIcons({ icons: appIcons })
}

init()

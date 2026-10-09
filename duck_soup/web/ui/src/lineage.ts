import { createIcons, Database, Play, MapPin, Link, Layers, Package, FileSpreadsheet, Globe, Map, Server, GitMerge, Maximize2, Crosshair, Scissors, Eraser, Split, Filter, Camera, DatabaseZap, Folder, CornerDownRight, Plus } from 'lucide'
import type { Config, CountsResponse, Step } from './types'
import { esc } from './dom'
import { openInFlowEditor, flowEditorTarget } from './flow-editor'
import { openStepGalleryModal } from './step-gallery'

// The lineage diagram, titled "ducks in a row · pipeline flow" in the editor: an interactive flow drawn from the config — sources, the base, each
// step (with its rejects as a side branch), the output layers and the shared output file.
// It's also an editor: clicking a node opens its card in a side panel (flow-editor.ts), the
// "+" on a connection inserts a step there, step nodes can be dragged (or moved with
// Alt+←/→) to reorder, and after counting rows each connection shows how many flow along it.
//
// Every node carries a `data-node` id — `p<i>::src::<id>`, `p<i>::base`, `p<i>::step::<n>`,
// `p<i>::rejects::<n>`, `p<i>::layer::<n>` (n 0-based) or `gpkg` — and every connection the
// ids of its two ends, which is all the hover highlighting and the counts need.

let lineageResizeObserver: ResizeObserver | null = null
let dragStartX = 0
let dragStartY = 0
let dragScrollLeft = 0
let dragScrollTop = 0
let isMouseDown = false
let dragMoved = false
// Set when a step node was dragged, so the click that ends the drag doesn't also open it.
let suppressClick = false
// ---- zoom ----
// The diagram is scaled with CSS `zoom` on its outer element, not a transform: the scroll
// area then follows the scaled size by itself. Everything that measures nodes on screen
// (getBoundingClientRect: connections, labels, the drag-to-reorder marker) divides by
// `currentZoom` to get back to the diagram's own pixels.
//
// 'fit' scales the whole flow to fit the panel (never above 100%, never below ZOOM_MIN, where
// labels stop being readable; past that it scrolls), and keeps fitting as the config or the
// panel changes. A number is a zoom the user picked. Either is remembered per config.
const ZOOM_MIN = 0.5
const ZOOM_MAX = 1.5
const ZOOM_STEP = 0.1
let zoomMode: 'fit' | number = 'fit'
let zoomConfig: string | null = null
let currentZoom = 1
let currentOuter: HTMLElement | null = null

const clampZoom = (z: number) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z))
const zoomKey = (name: string) => `ducksoup.lineageZoom.${name || 'default'}`

/** Picks up the zoom remembered for config `name`, when the open config changes. */
function loadZoomFor(name: string): void {
  if (zoomConfig === name) return
  zoomConfig = name
  let stored: string | null = null
  try { stored = localStorage.getItem(zoomKey(name)) } catch { /* private mode */ }
  const n = Number(stored)
  zoomMode = stored && stored !== 'fit' && Number.isFinite(n) ? clampZoom(n) : 'fit'
}

function saveZoom(): void {
  try { localStorage.setItem(zoomKey(zoomConfig ?? ''), String(zoomMode)) } catch { /* private mode */ }
}

/** The zoom at which the whole flow fits the panel (its width, and the panel's max height). */
function fitZoom(lineage: HTMLElement, outer: HTMLElement): number {
  outer.style.setProperty('zoom', '1')
  const width = outer.offsetWidth
  const height = outer.offsetHeight
  const style = getComputedStyle(lineage)
  const maxHeight = parseFloat(style.maxHeight) || Infinity
  // The space the editing panel reserves doesn't count: opening it shouldn't shrink the flow
  // (to 50%, often), just let it scroll beside the panel.
  const reserved = parseFloat(style.marginRight) || 0
  // clientWidth includes the side padding that keeps the first node off the clipping edge.
  const padX = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0)
  const availW = lineage.clientWidth - padX + reserved - 4
  const availH = maxHeight - 24
  if (!width || !height || availW <= 0) return 1
  return clampZoom(Math.min(1, availW / width, availH / height))
}

/** Applies the zoom mode to the drawn diagram; true if the scale changed. */
function applyZoom(): boolean {
  const lineage = document.getElementById('lineage-diagram')
  if (!lineage || !currentOuter) return false
  const z = zoomMode === 'fit' ? fitZoom(lineage, currentOuter) : zoomMode
  const changed = Math.abs(z - currentZoom) > 0.001
  currentZoom = z
  currentOuter.style.setProperty('zoom', String(z))
  const label = document.getElementById('zoomResetBtn')
  if (label) label.textContent = `${Math.round(z * 100)}%`
  document.getElementById('zoomFitBtn')?.setAttribute('aria-pressed', String(zoomMode === 'fit'))
  document.getElementById('zoomOutBtn')?.toggleAttribute('disabled', z <= ZOOM_MIN + 0.001)
  document.getElementById('zoomInBtn')?.toggleAttribute('disabled', z >= ZOOM_MAX - 0.001)
  return changed
}

/**
 * Sets the zoom ('fit' or a scale). A scale keeps the diagram point under (clientX, clientY)
 * in place — the pointer for Ctrl+wheel, the panel's centre for the buttons.
 */
function setZoom(mode: 'fit' | number, clientX?: number, clientY?: number): void {
  const lineage = document.getElementById('lineage-diagram')
  const outer = currentOuter
  if (!lineage || !outer || !lastCfg) return
  const rect = lineage.getBoundingClientRect()
  const ax = clientX ?? rect.left + rect.width / 2
  const ay = clientY ?? rect.top + rect.height / 2
  // The anchor, in the diagram's own pixels.
  const before = outer.getBoundingClientRect()
  const localX = (ax - before.left) / currentZoom
  const localY = (ay - before.top) / currentZoom
  zoomMode = mode === 'fit' ? 'fit' : clampZoom(mode)
  saveZoom()
  applyZoom()
  drawPaths(outer, lastCfg)
  if (mode === 'fit') return
  // Scroll the anchor back under the pointer. Until the diagram reaches its max height it
  // grows instead of scrolling, so whatever the panel can't take up, the builder column does.
  const drift = () => {
    const r = outer.getBoundingClientRect()
    return [r.left + localX * currentZoom - ax, r.top + localY * currentZoom - ay]
  }
  const [dx, dy] = drift()
  lineage.scrollLeft += dx
  lineage.scrollTop += dy
  const builder = document.querySelector<HTMLElement>('main.builder')
  if (builder) builder.scrollTop += drift()[1]
}

/** The header's zoom controls and Ctrl+wheel (or pinch). Wired once: they outlive rebuilds. */
function wireZoom(lineage: HTMLElement): void {
  if (lineage.dataset['zoomWired']) return
  lineage.dataset['zoomWired'] = '1'
  // Plain wheel keeps scrolling the page; only Ctrl+wheel (what a trackpad pinch sends) zooms.
  lineage.addEventListener('wheel', e => {
    if (!e.ctrlKey && !e.metaKey) return
    e.preventDefault()
    setZoom(currentZoom * Math.exp(-e.deltaY * 0.0015), e.clientX, e.clientY)
  }, { passive: false })
  const on = (id: string, fn: () => void) => document.getElementById(id)?.addEventListener('click', e => {
    e.stopPropagation() // not the header's collapse toggle
    fn()
  })
  on('zoomOutBtn', () => setZoom(Math.round((currentZoom - ZOOM_STEP) * 10) / 10))
  on('zoomInBtn', () => setZoom(Math.round((currentZoom + ZOOM_STEP) * 10) / 10))
  on('zoomResetBtn', () => setZoom(1))
  on('zoomFitBtn', () => setZoom('fit'))
}

// The output file name being edited on its node. The diagram is rebuilt on every sync (and
// a few times while sources load), so the edit lives here, not in the input element.
let renaming: { value: string } | null = null

const onBranch = (st: Step) => !!st.branch && st.branch !== 'main'

const SPATIAL_STEPS = new Set(['spatial_join', 'clip', 'erase', 'intersect_overlay', 'line_overlay', 'nearest_neighbor'])

const STEP_ICON: Record<string, string> = {
  spatial_join: 'map-pin', attribute_join: 'git-merge', nearest_neighbor: 'crosshair', buffer: 'maximize-2',
  centroid: 'crosshair', clip: 'scissors', erase: 'eraser', dissolve: 'layers', intersect_overlay: 'crosshair',
  line_overlay: 'split', filter: 'filter', snapshot: 'camera', merge: 'git-merge',
}

// ---- row counts --------------------------------------------------------------

interface StoredCounts { data: CountsResponse; json: string }
const countsByPipeline = new globalThis.Map<number, StoredCounts>()
// Bumped whenever the counts change, so the diagram knows to redraw (see drawnSignature).
let countsVersion = 0

/**
 * Show row counts for pipeline `pIdx` on its connections (null clears them). `json` is that
 * pipeline's definition as counted: once the config no longer matches, the counts are shown
 * as stale rather than silently describing a pipeline that isn't there any more.
 */
export function setLineageCounts(pIdx: number, data: CountsResponse | null, json = ''): void {
  if (data) countsByPipeline.set(pIdx, { data, json })
  else countsByPipeline.delete(pIdx)
  countsVersion++
}

export function clearLineageCounts(): void {
  countsByPipeline.clear()
  countsVersion++
}

/**
 * Everything the diagram draws, as a string. sync() runs on every edit, and while a config
 * loads its source cards report back one by one (filling in layers, columns), so the diagram
 * used to be torn down and rebuilt dozens of times in a few seconds, flickering through it
 * all. Now it's only rebuilt when this changes.
 */
function drawnSignature(cfg: Config): string {
  return JSON.stringify({
    output: cfg.output,
    counts: countsVersion,
    pipelines: (cfg.pipelines || []).map((p, i) => ({
      name: p.name, base: p.base, layers: p.layers,
      sources: (p.sources || []).map(s => [s.id, s.format]),
      derived: (p.derived_sources || []).map(d => [d.id, d.from]),
      steps: p.steps,
      stale: countsByPipeline.has(i) && countsByPipeline.get(i)!.json !== JSON.stringify(p),
    })),
  })
}
let lastSignature = ''

const fmt = (n: number) => n.toLocaleString()

export function getSourceIcon(format: string): { icon: string; color: string } {
  const fmt = (format || '').toLowerCase()
  switch (fmt) {
    case 'xlsx':
    case 'csv':
    case 'json':
      return { icon: 'file-spreadsheet', color: 'var(--fmt-sheet)' }
    case 'wfs':
    case 'arcgis_rest':
      return { icon: 'globe', color: 'var(--fmt-web)' }
    case 'gpkg':
      return { icon: 'package', color: 'var(--accent)' }
    case 'fgdb':
      return { icon: 'database', color: 'var(--accent)' }
    case 'parquet':
      return { icon: 'server', color: 'var(--accent)' }
    case 'postgres':
      return { icon: 'database-zap', color: 'var(--fmt-db)' }
    case 'geojson':
    case 'gml':
    case 'shp':
    case 'flatgeobuf':
      return { icon: 'map', color: 'var(--fmt-vector)' }
    default:
      return { icon: 'database', color: 'var(--muted)' }
  }
}

function makeNode(cls: string, nodeId: string, icon: string, color: string, label: string): HTMLElement {
  const node = document.createElement('div')
  node.className = `lineage-node ${cls}`
  node.dataset['node'] = nodeId
  node.innerHTML = `<i data-lucide="${icon}" style="width:12px;height:12px;color:${color};"></i> <span>${esc(label)}</span>`
  return node
}

function sourceNode(pIdx: number, id: string, format: string, extra = ''): HTMLElement {
  const info = getSourceIcon(format)
  const node = makeNode(`lineage-src-node ${extra}`, `p${pIdx}::src::${id}`, info.icon, info.color, id)
  node.dataset['sourceId'] = id
  node.title = `Source '${id}' — click to edit`
  return node
}

/** Draws the diagram for `cfg`, unless it would look the same as now (`force` redraws). */
export function updateLineageDiagram(cfg: Config, force = false): void {
  const lineage = document.getElementById('lineage-diagram')
  if (!lineage) return
  const signature = drawnSignature(cfg)
  lastCfg = cfg
  if (!force && signature === lastSignature && lineage.firstChild) return
  lastSignature = signature
  lineage.innerHTML = ''

  if (lineageResizeObserver) {
    lineageResizeObserver.disconnect()
    lineageResizeObserver = null
  }

  lastCfg = cfg
  const pipelines = cfg.pipelines || []
  if (pipelines.length === 0) {
    lineage.innerHTML = '<div style="color:var(--muted); font-size:12px;">Add a pipeline to get your ducks in a row</div>'
    return
  }

  const outer = document.createElement('div')
  outer.className = 'lineage-outer'
  // As wide as its content, not stretched to the panel (a flex item would be): "fit" measures
  // it, and the connections' SVG spans it.
  outer.style.cssText = 'display:inline-flex; align-items:center; gap:52px; min-height:60px; position:relative; width:max-content; flex-shrink:0;'

  // Master SVG container for all paths in the lineage
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  svg.setAttribute('class', 'lineage-connections-svg')
  svg.innerHTML = `
    <defs>
      <marker id="arrow-accent" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1.5 L 10 5 L 0 8.5 z" style="fill:var(--accent)"/>
      </marker>
      <marker id="arrow-spatial" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1.5 L 10 5 L 0 8.5 z" style="fill:var(--spatial)"/>
      </marker>
      <marker id="arrow-ok" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1.5 L 10 5 L 0 8.5 z" style="fill:var(--ok)"/>
      </marker>
      <marker id="arrow-warn" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1.5 L 10 5 L 0 8.5 z" style="fill:var(--warn)"/>
      </marker>
    </defs>
  `
  outer.appendChild(svg)

  // Count labels and "+" buttons, positioned over the connections by drawPaths.
  const edgeLayer = document.createElement('div')
  edgeLayer.className = 'lineage-edge-layer'
  outer.appendChild(edgeLayer)

  // ---- left: pipeline rows ----
  const pipelinesCol = document.createElement('div')
  pipelinesCol.style.cssText = 'display:flex; flex-direction:column; gap:6px;'

  pipelines.forEach((pdef, pIdx) => {
    const row = document.createElement('div')
    row.className = 'lineage-row'
    row.dataset['pipelineIndex'] = pIdx.toString()
    const hasRejects = (pdef.steps || []).some(st => 'rejects' in st && st.rejects)
    row.classList.toggle('has-rejects', hasRejects)
    row.classList.toggle('has-branches', (pdef.steps || []).some(onBranch))
    // A step on a branch is raised above the main chain, so its rejects go above it (below,
    // they'd sit on the main chain's line); the row needs room for them.
    row.classList.toggle('has-branch-rejects', (pdef.steps || []).some(st => onBranch(st) && 'rejects' in st && !!st.rejects))
    // Each row's header: its name (when there are several pipelines) and "+ source".
    const head = document.createElement('div')
    head.className = 'lineage-row-head'
    if (pipelines.length > 1) {
      const label = document.createElement('span')
      label.className = 'lineage-row-label'
      label.textContent = pdef.name || `pipeline ${pIdx + 1}`
      head.appendChild(label)
    }
    const addSrc = document.createElement('button')
    addSrc.type = 'button'
    addSrc.className = 'lineage-add-source'
    addSrc.innerHTML = '<i data-lucide="plus" style="width:10px;height:10px"></i> source'
    addSrc.title = `Add a source to ${pdef.name || 'this pipeline'}`
    addSrc.addEventListener('mousedown', e => e.stopPropagation()) // not a pan
    addSrc.addEventListener('click', e => {
      e.stopPropagation()
      const plCard = pipelineCard(pIdx) as (PipelineCardEl & { _addSource?: () => HTMLElement }) | undefined
      const card = plCard?._addSource?.()
      if (card) setTimeout(() => openInFlowEditor(card, 'sources', 'new source'), 0)
    })
    head.appendChild(addSrc)
    row.appendChild(head)

    const formatOf = (id: string): string => {
      const src = (pdef.sources || []).find(s => s.id === id)
      if (src) return src.format
      const ds = (pdef.derived_sources || []).find(d => d.id === id)
      return ds ? formatOf(ds.from) : 'gpkg'
    }

    // A. Unused Sources Column (placed on the far left)
    const usedSourceIds = new Set<string>()
    if (pdef.base) usedSourceIds.add(pdef.base)
    ;(pdef.steps || []).forEach(st => {
      if ('source' in st) {
        usedSourceIds.add(st.source)
        const derivedSrc = (pdef.derived_sources || []).find(ds => ds.id === st.source)
        if (derivedSrc) usedSourceIds.add(derivedSrc.from)
      }
    })
    const unusedSources = (pdef.sources || []).filter(src => src.id && !usedSourceIds.has(src.id))
    if (unusedSources.length > 0) {
      const unusedCol = document.createElement('div')
      unusedCol.className = 'lineage-column is-unused'
      unusedSources.forEach(src => unusedCol.appendChild(sourceNode(pIdx, src.id, src.format)))
      row.appendChild(unusedCol)
    }

    // B. Base Column: the base source itself, where the chain starts. One node, not a
    // source node with a separate "base" node under it, which said the same thing twice.
    const baseCol = document.createElement('div')
    baseCol.className = 'lineage-column'
    const baseSrc = (pdef.sources || []).find(src => src.id === pdef.base)
    const baseInfo = getSourceIcon(baseSrc?.format ?? '')
    const baseNode = makeNode(
      `lineage-base-node is-base${baseSrc ? '' : ' is-missing'}`, `p${pIdx}::base`,
      baseSrc ? baseInfo.icon : 'play', baseSrc ? baseInfo.color : 'var(--muted)', pdef.base || 'no base source',
    )
    baseNode.insertAdjacentHTML('beforeend', '<span class="lineage-base-tag">base</span>')
    baseNode.title = pdef.base ? `Base source '${pdef.base}': its features flow through every step — click to edit` : 'No base source picked yet'
    baseCol.appendChild(baseNode)
    row.appendChild(baseCol)

    // C. Step Columns (step source on top, step node at the bottom, rejects below it)
    ;(pdef.steps || []).forEach((st, idx) => {
      const stepCol = document.createElement('div')
      stepCol.className = 'lineage-column lineage-step-col'
      stepCol.dataset['stepIndex'] = idx.toString()
      if (st.branch && st.branch !== 'main') stepCol.classList.add('is-branch-col')

      if ('source' in st && st.source) {
        const derivedSrc = (pdef.derived_sources || []).find(ds => ds.id === st.source)
        const known = derivedSrc || (pdef.sources || []).some(src => src.id === st.source)
        if (known) {
          // A derived source is drawn under the source it's derived from.
          if (derivedSrc && (pdef.sources || []).some(src => src.id === derivedSrc.from)) {
            stepCol.appendChild(sourceNode(pIdx, derivedSrc.from, formatOf(derivedSrc.from)))
          }
          stepCol.appendChild(sourceNode(pIdx, st.source, formatOf(st.source), derivedSrc ? 'is-derived' : ''))
        }
      }

      const spatial = SPATIAL_STEPS.has(st.type)
      const label = 'source' in st ? st.source : (st.type === 'snapshot' ? st.id : st.type)
      const node = makeNode(
        `lineage-step-node ${spatial ? 'is-spatial' : 'is-attribute'}`, `p${pIdx}::step::${idx}`,
        STEP_ICON[st.type] ?? 'link', spatial ? 'var(--spatial)' : 'var(--accent)', label || st.type,
      )
      node.dataset['stepIndex'] = idx.toString()
      node.title = `Step ${idx + 1}: ${st.type.replace(/_/g, ' ')} — click to edit, drag (or Alt+←/→) to reorder`
      stepCol.appendChild(node)

      if ('rejects' in st && st.rejects) {
        const slot = document.createElement('div')
        slot.className = 'lineage-reject-slot'
        const rej = makeNode('lineage-reject-node', `p${pIdx}::rejects::${idx}`, 'corner-down-right', 'var(--warn)', st.rejects)
        rej.dataset['stepIndex'] = idx.toString()
        rej.title = `Rejects of step ${idx + 1}, written to the output as layer '${st.rejects}' — click to preview them`
        slot.appendChild(rej)
        stepCol.appendChild(slot)
      }
      row.appendChild(stepCol)
    })

    // D. Layer Column: one node per output layer
    const layerCol = document.createElement('div')
    layerCol.className = 'lineage-column lineage-layer-col'
    const layers = pdef.layers?.length ? pdef.layers : [{ layer: 'layer', crs: '' }]
    layers.forEach((l, li) => {
      const layerNode = makeNode('lineage-layer-node is-output', `p${pIdx}::layer::${li}`, 'layers', 'var(--ok)', l.layer || 'layer')
      layerNode.dataset['layerIndex'] = li.toString()
      layerNode.title = `Output layer '${l.layer}'${l.filter ? ` (filter: ${l.filter})` : ''} — click to edit`
      layerCol.appendChild(layerNode)
    })
    row.appendChild(layerCol)

    pipelinesCol.appendChild(row)
  })

  outer.appendChild(pipelinesCol)

  // ---- right: shared output node (GeoPackage, or GeoParquet file/folder) ----
  const gpkgNode = document.createElement('div')
  gpkgNode.className = 'lineage-node lineage-gpkg'
  gpkgNode.dataset['node'] = 'gpkg'
  const rejectCount = pipelines.reduce((n, p) => n + (p.steps || []).filter(st => 'rejects' in st && st.rejects).length, 0)
  const layerCount = pipelines.reduce((n, p) => n + (p.layers?.length ?? 0), 0) + rejectCount
  let outName = cfg.output ? cfg.output.split(/[\\/]/).pop() || cfg.output : 'output.gpkg'
  const isParquet = /\.(geo)?parquet$/i.test(outName)
  // GeoParquet has no layers: several layers become <name>/<layer>.parquet (see engine.py).
  if (isParquet && layerCount > 1) outName = outName.replace(/\.(geo)?parquet$/i, '') + '/'
  gpkgNode.innerHTML = `
    <i data-lucide="${isParquet && layerCount > 1 ? 'folder' : 'package'}" style="width:14px;height:14px;color:var(--ok);"></i>
    <div style="display:flex;flex-direction:column;gap:1px;min-width:0;">
      <span class="lineage-gpkg-name" style="font-size:12px;font-weight:600;">${esc(outName)}</span>
      <span style="font-size:10px;color:var(--muted);">${layerCount} layer${layerCount !== 1 ? 's' : ''}${rejectCount ? ` (${rejectCount} rejects)` : ''}${isParquet ? ' · geoparquet' : ''}</span>
    </div>`
  gpkgNode.title = `${cfg.output || 'output.gpkg'} — click to rename the output file`
  if (renaming) startRename(gpkgNode)
  gpkgNode.style.cssText = `
    display:flex; align-items:center; gap:8px; flex-shrink:0;
    border-color: rgb(var(--ok-rgb) / 0.4); background: rgb(var(--ok-rgb) / 0.06);
    padding: 8px 14px; z-index: 2;`
  outer.appendChild(gpkgNode)

  lineage.appendChild(outer)
  currentOuter = outer

  wirePan(lineage)
  wireZoom(lineage)
  loadZoomFor(cfg.name)
  applyZoom()

  const drawAll = () => drawPaths(outer, cfg)
  drawAll()
  requestAnimationFrame(drawAll)
  // A resized panel (splitter, window, the editing panel's reserved space) changes what fits.
  lineageResizeObserver = new ResizeObserver(() => {
    if (zoomMode === 'fit') applyZoom()
    drawAll()
  })
  lineageResizeObserver.observe(lineage)

  wireNodes(outer, cfg)
  markEditingNode()

  createIcons({ icons: { Database, Play, MapPin, Link, Layers, Package, FileSpreadsheet, Globe, Map, Server, GitMerge, Maximize2, Crosshair, Scissors, Eraser, Split, Filter, Camera, DatabaseZap, Folder, CornerDownRight, Plus } })
}

// Drag-to-scroll (pan). The container element outlives every rebuild (only its children are
// replaced), and the diagram is rebuilt on every keystroke — so the listeners are added once.
function wirePan(lineage: HTMLElement): void {
  lineage.style.cursor = 'grab'
  lineage.style.userSelect = 'none'
  if (lineage.dataset['panWired']) return
  lineage.dataset['panWired'] = '1'

  lineage.addEventListener('mousedown', (e: MouseEvent) => {
    if (e.button !== 0) return
    isMouseDown = true
    dragMoved = false
    dragStartX = e.pageX
    dragStartY = e.pageY
    dragScrollLeft = lineage.scrollLeft
    dragScrollTop = lineage.scrollTop
    lineage.style.cursor = 'grabbing'
  })
  const stop = () => {
    if (!isMouseDown) return
    isMouseDown = false
    lineage.style.cursor = 'grab'
  }
  lineage.addEventListener('mouseleave', stop)
  lineage.addEventListener('mouseup', stop)
  lineage.addEventListener('mousemove', (e: MouseEvent) => {
    if (!isMouseDown) return
    e.preventDefault()
    // Both ways: the diagram scrolls sideways when it's wider than the panel, and up and down
    // when it's taller than the panel's max height (zoomed in, or many pipelines).
    const dx = e.pageX - dragStartX
    const dy = e.pageY - dragStartY
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragMoved = true
    lineage.scrollLeft = dragScrollLeft - dx
    lineage.scrollTop = dragScrollTop - dy
  })

  // The flow editor opening or closing changes which node is marked as being edited.
  document.addEventListener('flow-editor-change', markEditingNode)
}

interface EdgeOpts {
  count?: number
  countTitle?: string
  stale?: boolean // counted before the pipeline's latest changes
  // Where the "+" inserts a step: before step `index` (steps.length appends), on `branch`.
  insert?: { pIdx: number; index: number; branch?: string }
}

function drawPaths(outer: HTMLElement, cfg: Config): void {
  const svg = outer.querySelector('.lineage-connections-svg') as SVGSVGElement | null
  const edgeLayer = outer.querySelector<HTMLElement>('.lineage-edge-layer')
  if (!svg || !edgeLayer) return

  const defs = svg.querySelector('defs')
  svg.innerHTML = ''
  if (defs) svg.appendChild(defs)
  edgeLayer.innerHTML = ''

  const parentRect = outer.getBoundingClientRect()
  if (parentRect.width === 0) return

  // On screen everything is scaled by the zoom; inside `outer` (where the connections and
  // labels are drawn) it isn't, so screen distances are divided by it.
  const z = currentZoom
  const getCoords = (el: HTMLElement) => {
    const r = el.getBoundingClientRect()
    return {
      left: (r.left - parentRect.left) / z,
      right: (r.right - parentRect.left) / z,
      top: (r.top - parentRect.top) / z,
      bottom: (r.bottom - parentRect.top) / z,
      x: (r.left - parentRect.left + r.width / 2) / z,
      y: (r.top - parentRect.top + r.height / 2) / z,
    }
  }

  const gpkgNode = outer.querySelector('.lineage-gpkg') as HTMLElement | null
  const rows = Array.from(outer.querySelectorAll('.lineage-row')) as HTMLElement[]

  const connect = (
    srcEl: HTMLElement,
    targetEl: HTMLElement,
    color: string,
    markerId: string,
    direction: 'horizontal' | 'vertical' | 'up',
    opts: EdgeOpts = {},
  ) => {
    const from = getCoords(srcEl)
    const to = getCoords(targetEl)

    let x1, y1, x2, y2, d
    if (direction === 'vertical') {
      x1 = from.x; y1 = from.bottom
      x2 = to.x; y2 = to.top - 3 // adjusted for arrow marker
      const controlDist = Math.max(10, (y2 - y1) * 0.4)
      d = `M ${x1} ${y1} C ${x1} ${y1 + controlDist}, ${x2} ${y2 - controlDist}, ${x2} ${y2}`
    } else if (direction === 'up') {
      x1 = from.x; y1 = from.top
      x2 = to.x; y2 = to.bottom + 3
      d = `M ${x1} ${y1} L ${x2} ${y2}`
    } else {
      x1 = from.right; y1 = from.y
      x2 = to.left - 3
      y2 = to.y
      const controlDist = Math.max(16, (x2 - x1) * 0.45)
      d = `M ${x1} ${y1} C ${x1 + controlDist} ${y1}, ${x2 - controlDist} ${y2}, ${x2} ${y2}`
    }

    const source = srcEl.dataset['node'] ?? ''
    const target = targetEl.dataset['node'] ?? ''
    for (const [cls, width, opacity] of [['lineage-path-glow', '5', '0.12'], ['lineage-path-main', '1.5', '0.75']]) {
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path')
      path.setAttribute('d', d)
      path.setAttribute('stroke', color)
      path.setAttribute('stroke-width', width)
      path.setAttribute('fill', 'none')
      path.setAttribute('opacity', opacity)
      path.setAttribute('class', cls)
      if (cls === 'lineage-path-main') path.setAttribute('marker-end', `url(#${markerId})`)
      path.dataset['source'] = source
      path.dataset['target'] = target
      svg.appendChild(path)
    }

    if (opts.count === undefined && !opts.insert) return
    const ctl = document.createElement('div')
    ctl.className = `lineage-edge-ctl is-${direction === 'horizontal' ? 'horizontal' : 'vertical'}${opts.stale ? ' is-stale' : ''}`
    // On the curve's midpoint (both shapes are symmetric, so that's the midpoint of its ends),
    // except on a long horizontal connection — the main chain running past a branch to the
    // layers — whose midpoint can land among other nodes: there, just before its target,
    // where the curve has already levelled out at the target's height.
    const long = direction === 'horizontal' && x2 - x1 > 160
    ctl.style.left = `${long ? x2 - 40 : (x1 + x2) / 2}px`
    ctl.style.top = `${long ? y2 : (y1 + y2) / 2}px`
    ctl.dataset['source'] = source
    ctl.dataset['target'] = target
    if (opts.count !== undefined) {
      const badge = document.createElement('span')
      badge.className = 'lineage-count'
      badge.textContent = fmt(opts.count)
      if (opts.countTitle) badge.title = opts.countTitle
      ctl.appendChild(badge)
    }
    if (opts.insert) {
      const { pIdx, index, branch } = opts.insert
      const btn = document.createElement('button')
      btn.type = 'button'
      btn.className = 'lineage-insert'
      btn.innerHTML = '<i data-lucide="plus" style="width:11px;height:11px"></i>'
      const where = index === 0 ? 'before the first step' : `after step ${index}`
      btn.title = `Insert a step here (${where}${branch ? `, on branch '${branch}'` : ''})`
      btn.setAttribute('aria-label', btn.title)
      btn.addEventListener('mousedown', e => e.stopPropagation()) // not a pan
      btn.addEventListener('click', e => {
        e.stopPropagation()
        insertStepAt(pIdx, index, branch)
      })
      ctl.appendChild(btn)
    }
    edgeLayer.appendChild(ctl)
  }

  cfg.pipelines.forEach((pdef, pIdx) => {
    const row = rows.find(r => r.dataset['pipelineIndex'] === pIdx.toString())
    if (!row) return
    const steps = pdef.steps || []

    // Counts, unless there are none for this pipeline or the pipeline changed since.
    const stored = countsByPipeline.get(pIdx)
    const stale = !!stored && stored.json !== JSON.stringify(pdef)
    const c = stored?.data
    const scope = c ? (c.limit == null ? 'all data' : `first ${fmt(c.limit)} base features`) : ''
    const titled = (n: number | undefined, what: string): EdgeOpts =>
      n === undefined ? {} : { count: n, stale, countTitle: `${fmt(n)} ${what} (${scope})${stale ? ' — counted before your latest changes' : ''}` }
    const countOf = (nodeId: string): EdgeOpts => {
      if (!c) return {}
      const [, kind, rest] = nodeId.split('::')
      if (kind === 'base') return titled(c.base, 'base features')
      if (kind === 'step') return titled(c.steps?.[String(Number(rest) + 1)], `rows after step ${Number(rest) + 1}`)
      if (kind === 'src') return titled(c.sources?.[rest], `rows in '${rest}'`)
      return {}
    }

    const node = (id: string) => row.querySelector<HTMLElement>(`[data-node="${CSS.escape(id)}"]`)
    // A source shown in several columns has a node in each: pick the one in `col`.
    const srcNodeIn = (id: string, col: Element | null) =>
      [...row.querySelectorAll<HTMLElement>('.lineage-src-node')].find(n => n.dataset['sourceId'] === id && n.closest('.lineage-column') === col) ?? null

    const baseNode = node(`p${pIdx}::base`)

    // Step sources -> step nodes, and parent -> derived source (vertical, same column)
    steps.forEach((st, idx) => {
      const stepEl = node(`p${pIdx}::step::${idx}`)
      if (!stepEl || !('source' in st)) return
      const col = stepEl.closest('.lineage-column')
      const stepSrcEl = srcNodeIn(st.source, col)
      if (stepSrcEl) {
        const spatial = SPATIAL_STEPS.has(st.type)
        connect(stepSrcEl, stepEl, spatial ? 'var(--spatial)' : 'var(--accent)', spatial ? 'arrow-spatial' : 'arrow-accent', 'vertical', countOf(`p${pIdx}::src::${st.source}`))
      }
      const ds = (pdef.derived_sources || []).find(d => d.id === st.source)
      const parentEl = ds ? srcNodeIn(ds.from, col) : null
      if (ds && parentEl && stepSrcEl) connect(parentEl, stepSrcEl, 'var(--accent)', 'arrow-accent', 'vertical', countOf(`p${pIdx}::src::${ds.from}`))
    })

    // Branch-aware horizontal pipeline flow
    const branchLatest = new globalThis.Map<string, HTMLElement>()
    if (baseNode) branchLatest.set('main', baseNode)

    steps.forEach((st, idx) => {
      const stepEl = node(`p${pIdx}::step::${idx}`)
      if (!stepEl) return
      const branchName = st.branch || 'main'
      const insert = { pIdx, index: idx, branch: st.branch || undefined }
      const input = branchLatest.get(branchName)
      const spatial = SPATIAL_STEPS.has(st.type)

      if (input) {
        const opts = { ...countOf(input.dataset['node'] ?? ''), insert }
        if (st.type === 'snapshot' || st.type === 'merge') connect(input, stepEl, 'var(--accent)', 'arrow-accent', 'horizontal', opts)
        else connect(input, stepEl, spatial ? 'var(--spatial)' : 'var(--accent)', spatial ? 'arrow-spatial' : 'arrow-accent', 'horizontal', opts)
      }
      if (st.type === 'merge') {
        // The merged branch's second input: no "+" (which of the two would it go on?).
        const merged = branchLatest.get(st.source)
        if (merged) connect(merged, stepEl, 'var(--accent)', 'arrow-accent', 'horizontal', countOf(merged.dataset['node'] ?? ''))
      }
      branchLatest.set(branchName, stepEl)
      if (st.type === 'snapshot') branchLatest.set(st.id, stepEl)

      const rejEl = node(`p${pIdx}::rejects::${idx}`)
      if (rejEl) connect(stepEl, rejEl, 'var(--warn)', 'arrow-warn', onBranch(st) ? 'up' : 'vertical', titled(c?.rejects?.[String(idx + 1)], `rows rejected by step ${idx + 1}`))
    })

    const finalMain = branchLatest.get('main')
    const layerNodes = [...row.querySelectorAll<HTMLElement>('.lineage-layer-node')]
    layerNodes.forEach((layerNode, li) => {
      if (finalMain) {
        connect(finalMain, layerNode, 'var(--ok)', 'arrow-ok', 'horizontal', {
          ...titled(c?.layers?.[li], `rows written to layer '${pdef.layers?.[li]?.layer ?? ''}'`),
          // One "+" for appending a step, on the first layer's connection.
          ...(li === 0 ? { insert: { pIdx, index: steps.length } } : {}),
        })
      }
      if (gpkgNode) connect(layerNode, gpkgNode, 'var(--ok)', 'arrow-ok', 'horizontal')
    })
  })

  createIcons({ icons: { Plus } })
}

// ---- interaction -------------------------------------------------------------

type PipelineCardEl = HTMLElement & {
  _activateSection?: (sec: string) => void
  _insertStep?: (kind: Step['type'], index: number, init?: Partial<Step>) => HTMLElement
  _moveStep?: (from: number, to: number) => void
}

const pipelineCard = (pIdx: number) =>
  document.querySelectorAll<HTMLElement>('.pipeline-card')[pIdx] as PipelineCardEl | undefined

function insertStepAt(pIdx: number, index: number, branch?: string): void {
  const plCard = pipelineCard(pIdx)
  if (!plCard?._insertStep) return
  openStepGalleryModal(kind => {
    const card = plCard._insertStep!(kind, index, branch ? { branch } as Partial<Step> : {})
    // Deferred: the gallery modal hands focus back to the "+" as it closes.
    setTimeout(() => openInFlowEditor(card, 'steps', `step ${index + 1} · ${kind.replace(/_/g, ' ')}`), 0)
  })
}

function findSourceCard(plCard: HTMLElement, id: string): { card: HTMLElement; sec: string } | null {
  for (const [sec, sel] of [['sources', '.pl-sources > .card'], ['derived_sources', '.pl-derived-sources > .card']]) {
    const card = [...plCard.querySelectorAll<HTMLElement>(sel)]
      .find(c => c.querySelector<HTMLInputElement>('[data-k="id"]')?.value.trim() === id)
    if (card) return { card, sec }
  }
  return null
}

/**
 * Edit the output path on the output node itself. Enter (or leaving the field) writes it to
 * the config's output field, as if typed there; Escape cancels. A rebuild mid-edit (sources
 * still loading) re-creates the field from `renaming`.
 */
function startRename(node: HTMLElement): void {
  const output = document.getElementById('cfg_output') as HTMLInputElement | null
  const nameEl = node.querySelector<HTMLElement>('.lineage-gpkg-name')
  if (!output || !nameEl) return
  const hadFocus = !!renaming
  renaming ??= { value: output.value }
  const input = document.createElement('input')
  input.className = 'lineage-rename'
  input.value = renaming.value
  input.setAttribute('aria-label', 'Output file path (.gpkg or .parquet)')
  input.size = Math.max(16, Math.min(48, input.value.length + 2))
  nameEl.replaceWith(input)

  let done = false
  const finish = (commit: boolean) => {
    if (done) return
    done = true
    const value = input.value.trim()
    renaming = null
    if (commit && value && value !== output.value) {
      output.value = value
      output.dispatchEvent(new Event('input', { bubbles: true }))
    } else {
      if (lastCfg) updateLineageDiagram({ ...lastCfg, output: output.value }, true)
    }
  }
  input.addEventListener('input', () => { if (renaming) renaming.value = input.value })
  input.addEventListener('keydown', e => {
    e.stopPropagation() // not the node's own Enter/Space handling
    if (e.key === 'Enter') { e.preventDefault(); finish(true) }
    else if (e.key === 'Escape') { e.preventDefault(); finish(false) }
  })
  input.addEventListener('blur', () => { if (input.isConnected) finish(true) })
  for (const ev of ['mousedown', 'click']) input.addEventListener(ev, e => e.stopPropagation())
  setTimeout(() => {
    input.focus()
    if (!hadFocus) input.select()
  }, 0)
}

let lastCfg: Config | null = null

function openNode(node: HTMLElement): void {
  if (node.classList.contains('lineage-gpkg')) {
    startRename(node)
    return
  }
  const row = node.closest<HTMLElement>('.lineage-row')
  const pIdx = Number(row?.dataset['pipelineIndex'])
  const plCard = pipelineCard(pIdx)
  if (!plCard) return

  if (node.classList.contains('lineage-reject-node')) {
    node.dispatchEvent(new CustomEvent('preview-rejects', {
      bubbles: true, detail: { stepIdx: Number(node.dataset['stepIndex']) + 1, pipelineIdx: pIdx },
    }))
    return
  }
  if (node.classList.contains('lineage-step-node')) {
    const idx = Number(node.dataset['stepIndex'])
    const card = plCard.querySelectorAll<HTMLElement>('.pl-steps > .card')[idx]
    if (card) openInFlowEditor(card, 'steps', `step ${idx + 1} · ${(card.dataset['type'] ?? '').replace(/_/g, ' ')}`)
    return
  }
  if (node.classList.contains('lineage-layer-node')) {
    const li = Number(node.dataset['layerIndex'])
    const card = plCard.querySelectorAll<HTMLElement>('.pl-output-layers > .card')[li]
    if (card) openInFlowEditor(card, 'output', `output layer · ${node.textContent?.trim() ?? ''}`)
    return
  }
  const id = node.classList.contains('lineage-base-node')
    ? plCard.querySelector<HTMLInputElement>('.pl-base')?.value ?? ''
    : node.dataset['sourceId'] ?? ''
  const found = id ? findSourceCard(plCard, id) : null
  if (found) {
    openInFlowEditor(found.card, found.sec, `${found.sec === 'derived_sources' ? 'derived source' : 'source'} · ${id}`)
  } else {
    plCard._activateSection?.('sources')
  }
}

/** Marks the node whose card is open in the flow editor. */
function markEditingNode(): void {
  document.querySelectorAll('.lineage-node.is-editing').forEach(n => n.classList.remove('is-editing'))
  const t = flowEditorTarget()
  if (!t) return
  const row = document.querySelector(`.lineage-row[data-pipeline-index="${t.pipelineIdx}"]`)
  if (!row) return
  let id = ''
  if (t.sec === 'steps') id = `p${t.pipelineIdx}::step::${t.index}`
  else if (t.sec === 'output') id = `p${t.pipelineIdx}::layer::${t.index}`
  else {
    const card = document.querySelector('.flow-popped')
    const srcId = card?.querySelector<HTMLInputElement>('[data-k="id"]')?.value.trim()
    if (srcId) row.querySelectorAll<HTMLElement>('.lineage-src-node').forEach(n => {
      if (n.dataset['sourceId'] === srcId) n.classList.add('is-editing')
    })
    return
  }
  row.querySelector(`[data-node="${CSS.escape(id)}"]`)?.classList.add('is-editing')
}

function wireNodes(outer: HTMLElement, cfg: Config): void {
  const nodes = Array.from(outer.querySelectorAll<HTMLElement>('.lineage-node'))
  const paths = () => Array.from(outer.querySelectorAll<SVGPathElement>('.lineage-path-main, .lineage-path-glow'))
  const ctls = () => Array.from(outer.querySelectorAll<HTMLElement>('.lineage-edge-ctl'))

  const clearHighlight = () => {
    nodes.forEach(n => n.classList.remove('dimmed', 'highlighted'))
    paths().forEach(p => p.classList.remove('dimmed', 'highlighted'))
    ctls().forEach(c => c.classList.remove('dimmed'))
  }
  const highlight = (node: HTMLElement) => {
    const id = node.dataset['node']
    const linked = paths().filter(p => p.dataset['source'] === id || p.dataset['target'] === id)
    if (linked.length === 0) return
    const ends = new Set(linked.flatMap(p => [p.dataset['source'], p.dataset['target']]))
    nodes.forEach(n => {
      const on = n === node || ends.has(n.dataset['node'])
      n.classList.toggle('dimmed', !on)
      n.classList.toggle('highlighted', on)
    })
    paths().forEach(p => {
      const on = linked.includes(p)
      p.classList.toggle('dimmed', !on)
      p.classList.toggle('highlighted', on)
    })
    ctls().forEach(c => c.classList.toggle('dimmed', !(c.dataset['source'] === id || c.dataset['target'] === id)))
  }

  nodes.forEach(node => {
    // Plain divs made into real controls, so the diagram works from the keyboard too.
    node.tabIndex = 0
    node.setAttribute('role', 'button')
    node.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); node.click() }
    })
    node.addEventListener('mouseenter', () => highlight(node))
    node.addEventListener('focus', () => highlight(node))
    node.addEventListener('mouseleave', clearHighlight)
    node.addEventListener('blur', clearHighlight)
    node.addEventListener('click', () => {
      if (dragMoved || suppressClick) { suppressClick = false; return }
      openNode(node)
    })
  })

  outer.querySelectorAll<HTMLElement>('.lineage-step-node').forEach(node => wireStepDrag(outer, node, cfg))
}

/**
 * Drag a step node sideways to reorder the steps (within its pipeline), or Alt+←/→ on a
 * focused one. Either way the step cards are reordered, which rebuilds the diagram.
 */
function wireStepDrag(outer: HTMLElement, node: HTMLElement, cfg: Config): void {
  const row = node.closest<HTMLElement>('.lineage-row')!
  const pIdx = Number(row.dataset['pipelineIndex'])
  const from = Number(node.dataset['stepIndex'])
  const nSteps = cfg.pipelines[pIdx]?.steps?.length ?? 0

  const move = (to: number) => {
    const plCard = pipelineCard(pIdx)
    if (!plCard?._moveStep || to < 0 || to >= nSteps || to === from) return
    plCard._moveStep(from, to)
    // The move rebuilt the diagram synchronously: keep focus on the moved node.
    document.querySelector<HTMLElement>(`.lineage-row[data-pipeline-index="${pIdx}"] [data-node="p${pIdx}::step::${to}"]`)?.focus()
  }

  node.addEventListener('keydown', e => {
    if (!e.altKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return
    e.preventDefault()
    move(from + (e.key === 'ArrowLeft' ? -1 : 1))
  })

  node.addEventListener('mousedown', e => {
    if (e.button !== 0 || nSteps < 2) return
    e.stopPropagation() // a step drag, not a pan
    dragMoved = false // left over from an earlier pan, it would swallow this node's click
    const startX = e.clientX
    const cols = [...row.querySelectorAll<HTMLElement>('.lineage-step-col')]
    const others = cols.filter(c => Number(c.dataset['stepIndex']) !== from)
    let dragging = false
    let to = from
    const marker = document.createElement('div')
    marker.className = 'lineage-drop-marker'

    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - startX
      if (!dragging && Math.abs(dx) < 5) return
      if (!dragging) {
        dragging = true
        node.classList.add('is-dragging')
        outer.appendChild(marker)
      }
      node.style.transform = `translateX(${dx / currentZoom}px)`
      // The new position: how many of the other steps lie left of the pointer.
      to = others.filter(c => {
        const r = c.getBoundingClientRect()
        return r.left + r.width / 2 < ev.clientX
      }).length
      const outerRect = outer.getBoundingClientRect()
      const before = others[to]?.getBoundingClientRect()
      const after = others[to - 1]?.getBoundingClientRect()
      const x = before && after ? (after.right + before.left) / 2
        : before ? before.left - 26 : (after ? after.right + 26 : 0)
      const rowRect = row.getBoundingClientRect()
      Object.assign(marker.style, {
        left: `${(x - outerRect.left) / currentZoom}px`,
        top: `${(rowRect.top - outerRect.top) / currentZoom}px`,
        height: `${rowRect.height / currentZoom}px`,
      })
    }
    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      marker.remove()
      if (!dragging) return
      suppressClick = true
      // The click that ends a drag fires right after mouseup (if at all): clear the flag
      // afterwards either way, or it would eat the next real click.
      setTimeout(() => { suppressClick = false }, 0)
      node.classList.remove('is-dragging')
      node.style.transform = ''
      if (to !== from) move(to)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  })
}

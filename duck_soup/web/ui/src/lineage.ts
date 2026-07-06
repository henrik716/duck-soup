import { createIcons, Database, Play, MapPin, Link, Layers, Package, FileSpreadsheet, Globe, Map, Server, GitMerge, Maximize2, Crosshair, Scissors, Eraser, Filter, Camera } from 'lucide'
import type { Config } from './types'

let lineageResizeObserver: ResizeObserver | null = null
let dragStartX = 0
let dragScrollLeft = 0
let isMouseDown = false
let dragMoved = false

function getSourceIcon(format: string): { icon: string; color: string } {
  const fmt = (format || '').toLowerCase()
  switch (fmt) {
    case 'xlsx':
    case 'csv':
      return { icon: 'file-spreadsheet', color: '#ffc107' }
    case 'wfs':
    case 'arcgis_rest':
      return { icon: 'globe', color: '#0dcaf0' }
    case 'gpkg':
      return { icon: 'package', color: '#9d85ff' }
    case 'fgdb':
      return { icon: 'database', color: '#9d85ff' }
    case 'parquet':
      return { icon: 'server', color: '#a07aff' }
    case 'geojson':
    case 'gml':
    case 'shp':
      return { icon: 'map', color: '#20c997' }
    default:
      return { icon: 'database', color: 'var(--muted)' }
  }
}

export function updateLineageDiagram(cfg: Config): void {
  const lineage = document.getElementById('lineage-diagram')
  if (!lineage) return
  lineage.innerHTML = ''

  if (lineageResizeObserver) {
    lineageResizeObserver.disconnect()
    lineageResizeObserver = null
  }

  const pipelines = cfg.pipelines || []
  if (pipelines.length === 0) {
    lineage.innerHTML = '<div style="color:var(--muted); font-size:12px;">Add a pipeline to view lineage flow</div>'
    return
  }

  const outer = document.createElement('div')
  outer.className = 'lineage-outer'
  outer.style.cssText = 'display:inline-flex; align-items:center; gap:40px; min-height:60px; position:relative;'

  // Master SVG container for all paths in the lineage
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  svg.setAttribute('class', 'lineage-connections-svg')
  svg.innerHTML = `
    <defs>
      <marker id="arrow-accent" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1.5 L 10 5 L 0 8.5 z" fill="#9d85ff"/>
      </marker>
      <marker id="arrow-spatial" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1.5 L 10 5 L 0 8.5 z" fill="#00ebd7"/>
      </marker>
      <marker id="arrow-ok" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1.5 L 10 5 L 0 8.5 z" fill="#63ffad"/>
      </marker>
    </defs>
  `
  outer.appendChild(svg)

  // ---- left: pipeline rows ----
  const pipelinesCol = document.createElement('div')
  pipelinesCol.style.cssText = 'display:flex; flex-direction:column; gap:24px;'

  pipelines.forEach((pdef, pIdx) => {
    const row = document.createElement('div')
    row.className = 'lineage-row'
    row.dataset.pipelineIndex = pIdx.toString()
    row.style.cssText = 'display:flex; align-items:flex-end; gap:40px; position:relative; padding:35px 0 10px;'

    // A. Unused Sources Column (placed on the far left)
    const usedSourceIds = new Set<string>()
    if (pdef.base) usedSourceIds.add(pdef.base)
    
    ;(pdef.steps || []).forEach(st => {
      if ('source' in st) {
        const stepSrcId = (st as any).source
        usedSourceIds.add(stepSrcId)
        
        const derivedSrc = (pdef.derived_sources || []).find(ds => ds.id === stepSrcId)
        if (derivedSrc) {
          usedSourceIds.add(derivedSrc.from)
        }
      }
    })
    const unusedSources = (pdef.sources || []).filter(src => src.id && !usedSourceIds.has(src.id))

    if (unusedSources.length > 0) {
      const unusedCol = document.createElement('div')
      unusedCol.className = 'lineage-column is-unused'
      
      unusedSources.forEach(src => {
        const node = document.createElement('div')
        node.className = 'lineage-node lineage-src-node'
        node.setAttribute('data-source-id', src.id)
        const info = getSourceIcon(src.format)
        node.innerHTML = `<i data-lucide="${info.icon}" style="width:12px;height:12px;color:${info.color};"></i> <span>${src.id}</span>`
        unusedCol.appendChild(node)
      })
      row.appendChild(unusedCol)
    }

    // B. Base Column (Base Source on top, Base Node on bottom)
    const baseCol = document.createElement('div')
    baseCol.className = 'lineage-column'

    const baseSrc = (pdef.sources || []).find(src => src.id === pdef.base)
    if (baseSrc) {
      const node = document.createElement('div')
      node.className = 'lineage-node lineage-src-node is-base'
      node.setAttribute('data-source-id', baseSrc.id)
      const info = getSourceIcon(baseSrc.format)
      node.innerHTML = `<i data-lucide="${info.icon}" style="width:12px;height:12px;color:${info.color};"></i> <span>${baseSrc.id}</span>`
      baseCol.appendChild(node)
    }

    const baseNode = document.createElement('div')
    baseNode.className = 'lineage-node lineage-base-node is-base'
    baseNode.setAttribute('data-source-id', pdef.base)
    baseNode.innerHTML = `<i data-lucide="play" style="width:12px;height:12px;color:var(--accent);"></i> <span>${pdef.base || '—'}</span>`
    baseCol.appendChild(baseNode)

    row.appendChild(baseCol)

    // C. Step Columns (Step Source on top, Join Step Node on bottom)
    ;(pdef.steps || []).forEach((st, idx) => {
      const stepCol = document.createElement('div')
      stepCol.className = 'lineage-column'
      if (st.branch && st.branch !== 'main') {
        stepCol.classList.add('is-branch-col')
      }

      if ('source' in st) {
        const stepSrcId = (st as any).source
        const stepSrc = (pdef.sources || []).find(src => src.id === stepSrcId)
        const derivedSrc = (pdef.derived_sources || []).find(ds => ds.id === stepSrcId)
        
        if (stepSrc || derivedSrc) {
          // If it is a derived source, first render its parent source node on top!
          if (derivedSrc) {
            const parentSrc = (pdef.sources || []).find(src => src.id === derivedSrc.from)
            if (parentSrc) {
              const pNode = document.createElement('div')
              pNode.className = 'lineage-node lineage-src-node'
              pNode.setAttribute('data-source-id', parentSrc.id)
              const pInfo = getSourceIcon(parentSrc.format)
              pNode.innerHTML = `<i data-lucide="${pInfo.icon}" style="width:12px;height:12px;color:${pInfo.color};"></i> <span>${parentSrc.id}</span>`
              stepCol.appendChild(pNode)
            }
          }

          // Then render the derived source or normal step source node
          const node = document.createElement('div')
          if (derivedSrc) {
            node.className = 'lineage-node lineage-src-node is-derived'
          } else {
            node.className = 'lineage-node lineage-src-node'
          }
          node.setAttribute('data-source-id', stepSrcId)
          
          let format = 'gpkg'
          if (stepSrc) {
            format = stepSrc.format
          } else if (derivedSrc) {
            const parentSrc = (pdef.sources || []).find(src => src.id === derivedSrc.from)
            if (parentSrc) format = parentSrc.format
          }
          
          const info = getSourceIcon(format)
          node.innerHTML = `<i data-lucide="${info.icon}" style="width:12px;height:12px;color:${info.color};"></i> <span>${stepSrcId}</span>`
          stepCol.appendChild(node)
        }
      }

      // Customize icon and color by step type
      let icon = 'link'
      let color = 'var(--accent)'
      if (st.type === 'spatial_join') { icon = 'map-pin'; color = 'var(--spatial)' }
      else if (st.type === 'attribute_join') { icon = 'git-merge'; color = 'var(--accent)' }
      else if (st.type === 'nearest_neighbor') { icon = 'crosshair'; color = 'var(--spatial)' }
      else if (st.type === 'buffer') { icon = 'maximize-2'; color = 'var(--accent)' }
      else if (st.type === 'centroid') { icon = 'crosshair'; color = 'var(--accent)' }
      else if (st.type === 'clip') { icon = 'scissors'; color = 'var(--spatial)' }
      else if (st.type === 'erase') { icon = 'eraser'; color = 'var(--spatial)' }
      else if (st.type === 'dissolve') { icon = 'layers'; color = 'var(--accent)' }
      else if (st.type === 'intersect_overlay') { icon = 'crosshair'; color = 'var(--spatial)' }
      else if (st.type === 'filter') { icon = 'filter'; color = 'var(--accent)' }
      else if (st.type === 'snapshot') { icon = 'camera'; color = 'var(--accent)' }
      else if (st.type === 'merge') { icon = 'git-merge'; color = 'var(--accent)' }

      const stepSourceId = 'source' in st ? (st as any).source : 'base'
      const label = 'source' in st ? (st as any).source : (st.type === 'snapshot' ? (st as any).id : st.type)

      const node = document.createElement('div')
      node.className = `lineage-node lineage-step-node ${st.type === 'spatial_join' || st.type === 'clip' || st.type === 'erase' || st.type === 'intersect_overlay' || st.type === 'nearest_neighbor' ? 'is-spatial' : 'is-attribute'}`
      node.setAttribute('data-source-id', stepSourceId)
      node.dataset.stepIndex = idx.toString()
      node.innerHTML = `<i data-lucide="${icon}" style="width:12px;height:12px;color:${color};"></i> <span>${label}</span>`
      stepCol.appendChild(node)

      row.appendChild(stepCol)
    })

    // D. Layer Column (only holds the Layer Node at the bottom)
    const layerCol = document.createElement('div')
    layerCol.className = 'lineage-column'

    const layerNode = document.createElement('div')
    layerNode.className = 'lineage-node lineage-layer-node is-output'
    layerNode.innerHTML = `<i data-lucide="layers" style="width:12px;height:12px;color:var(--ok);"></i> <span>${(pdef.layers || []).map(l => l.layer).join(', ') || 'layer'}</span>`
    layerCol.appendChild(layerNode)

    row.appendChild(layerCol)

    pipelinesCol.appendChild(row)
  })

  outer.appendChild(pipelinesCol)

  // ---- right: shared GeoPackage node ----
  const gpkgNode = document.createElement('div')
  gpkgNode.className = 'lineage-node lineage-gpkg'
  const gpkgName = cfg.output ? cfg.output.split('/').pop() || cfg.output : 'output.gpkg'
  gpkgNode.innerHTML = `
    <i data-lucide="package" style="width:14px;height:14px;color:var(--ok);"></i>
    <div style="display:flex;flex-direction:column;gap:1px;">
      <span style="font-size:12px;font-weight:600;">${gpkgName}</span>
      <span style="font-size:10px;color:var(--muted);">${pipelines.length} layer${pipelines.length !== 1 ? 's' : ''}</span>
    </div>`
  gpkgNode.style.cssText = `
    display:flex; align-items:center; gap:8px; flex-shrink:0;
    border-color: rgba(99,255,173,0.4); background: rgba(99,255,173,0.06);
    padding: 8px 14px; z-index: 2;`
  outer.appendChild(gpkgNode)

  lineage.appendChild(outer)

  // Enable drag-to-scroll (pan) on the lineage container
  lineage.style.cursor = 'grab'
  lineage.style.userSelect = 'none'

  lineage.addEventListener('mousedown', (e: MouseEvent) => {
    // Only drag with left click
    if (e.button !== 0) return
    isMouseDown = true
    dragMoved = false
    dragStartX = e.pageX - lineage.offsetLeft
    dragScrollLeft = lineage.scrollLeft
    lineage.style.cursor = 'grabbing'
  })

  lineage.addEventListener('mouseleave', () => {
    if (isMouseDown) {
      isMouseDown = false
      lineage.style.cursor = 'grab'
    }
  })

  lineage.addEventListener('mouseup', () => {
    if (isMouseDown) {
      isMouseDown = false
      lineage.style.cursor = 'grab'
    }
  })

  lineage.addEventListener('mousemove', (e: MouseEvent) => {
    if (!isMouseDown) return
    e.preventDefault()
    const x = e.pageX - lineage.offsetLeft
    const walk = (x - dragStartX) * 1.5 // Scroll multiplier
    if (Math.abs(walk) > 3) {
      dragMoved = true
    }
    lineage.scrollLeft = dragScrollLeft - walk
  })

  const drawAll = () => {
    drawPaths(outer, cfg)
  }

  // Draw initial paths
  drawAll()
  requestAnimationFrame(drawAll)

  // Setup ResizeObserver to draw lines on resize or tab toggles
  lineageResizeObserver = new ResizeObserver(() => {
    drawAll()
  })
  lineageResizeObserver.observe(lineage)

  // Attach hover interactivity to paths & nodes globally
  setupGlobalHoverEffects(outer)

  createIcons({ icons: { Database, Play, MapPin, Link, Layers, Package, FileSpreadsheet, Globe, Map, Server, GitMerge, Maximize2, Crosshair, Scissors, Eraser, Filter, Camera } })
}

function drawPaths(outer: HTMLElement, cfg: Config): void {
  const svg = outer.querySelector('.lineage-connections-svg') as SVGSVGElement | null
  if (!svg) return

  const defs = svg.querySelector('defs')
  svg.innerHTML = ''
  if (defs) svg.appendChild(defs)

  const parentRect = outer.getBoundingClientRect()
  if (parentRect.width === 0) return

  const getCoords = (el: HTMLElement) => {
    const r = el.getBoundingClientRect()
    return {
      left: r.left - parentRect.left,
      right: r.right - parentRect.left,
      top: r.top - parentRect.top,
      bottom: r.bottom - parentRect.top,
      x: r.left - parentRect.left + r.width / 2,
      y: r.top - parentRect.top + r.height / 2,
      width: r.width,
      height: r.height
    }
  }

  const gpkgNode = outer.querySelector('.lineage-gpkg') as HTMLElement | null
  const rows = Array.from(outer.querySelectorAll('.lineage-row')) as HTMLElement[]

  const connect = (
    srcEl: HTMLElement,
    targetEl: HTMLElement,
    color: string,
    markerId: string,
    sourceId: string,
    targetId: string,
    direction: 'horizontal' | 'vertical'
  ) => {
    const from = getCoords(srcEl)
    const to = getCoords(targetEl)

    let x1, y1, x2, y2, d
    if (direction === 'vertical') {
      // Downwards connection
      x1 = from.x
      y1 = from.bottom
      x2 = to.x
      y2 = to.top - 3 // adjusted for arrow marker

      const dy = y2 - y1
      const controlDist = Math.max(10, dy * 0.4)
      d = `M ${x1} ${y1} C ${x1} ${y1 + controlDist}, ${x2} ${y2 - controlDist}, ${x2} ${y2}`
    } else {
      // Horizontal connection
      x1 = from.right
      y1 = from.y
      x2 = to.left - 3 // adjusted for arrow marker
      y2 = to.y

      const dx = x2 - x1
      const controlDist = Math.max(16, dx * 0.45)
      d = `M ${x1} ${y1} C ${x1 + controlDist} ${y1}, ${x2 - controlDist} ${y2}, ${x2} ${y2}`
    }

    // 1. Glow path (for neon glow effect)
    const glowPath = document.createElementNS('http://www.w3.org/2000/svg', 'path')
    glowPath.setAttribute('d', d)
    glowPath.setAttribute('stroke', color)
    glowPath.setAttribute('stroke-width', '5')
    glowPath.setAttribute('fill', 'none')
    glowPath.setAttribute('opacity', '0.12')
    glowPath.setAttribute('class', 'lineage-path-glow')
    glowPath.dataset.source = sourceId
    glowPath.dataset.target = targetId
    svg.appendChild(glowPath)

    // 2. Main path
    const mainPath = document.createElementNS('http://www.w3.org/2000/svg', 'path')
    mainPath.setAttribute('d', d)
    mainPath.setAttribute('stroke', color)
    mainPath.setAttribute('stroke-width', '1.5')
    mainPath.setAttribute('fill', 'none')
    mainPath.setAttribute('opacity', '0.75')
    mainPath.setAttribute('class', 'lineage-path-main')
    mainPath.setAttribute('marker-end', `url(#${markerId})`)
    mainPath.dataset.source = sourceId
    mainPath.dataset.target = targetId
    svg.appendChild(mainPath)
  }

  cfg.pipelines.forEach((pdef, pIdx) => {
    const row = rows.find(r => r.dataset.pipelineIndex === pIdx.toString())
    if (!row) return

    const srcNodes = Array.from(row.querySelectorAll('.lineage-src-node')) as HTMLElement[]
    const baseNode = row.querySelector('.lineage-base-node') as HTMLElement | null
    const stepNodes = Array.from(row.querySelectorAll('.lineage-step-node')) as HTMLElement[]
    stepNodes.sort((a, b) => {
      const idxA = parseInt(a.dataset.stepIndex || '0', 10)
      const idxB = parseInt(b.dataset.stepIndex || '0', 10)
      return idxA - idxB
    })
    const layerNode = row.querySelector('.lineage-layer-node') as HTMLElement | null

    // Connect Base Source -> Base Node (vertical)
    if (baseNode) {
      const baseSrcId = pdef.base
      const baseSrcEl = srcNodes.find(n => n.getAttribute('data-source-id') === baseSrcId && n.closest('.lineage-column') === baseNode.closest('.lineage-column'))
      if (baseSrcEl) {
        connect(baseSrcEl, baseNode, 'var(--accent)', 'arrow-accent', `p${pIdx}::src::${baseSrcId}`, `p${pIdx}::base`, 'vertical')
      }
    }

    // Connect Step Sources -> Step Nodes (vertical)
    ;(pdef.steps || []).forEach((st, idx) => {
      const stepEl = stepNodes[idx]
      if (stepEl && 'source' in st) {
        const stepSrcId = (st as any).source
        const stepSrcEl = srcNodes.find(n => n.getAttribute('data-source-id') === stepSrcId && n.closest('.lineage-column') === stepEl.closest('.lineage-column'))
        if (stepSrcEl) {
          const isSpatial = st.type === 'spatial_join' || st.type === 'clip' || st.type === 'erase' || st.type === 'intersect_overlay' || st.type === 'nearest_neighbor'
          const color = isSpatial ? 'var(--spatial)' : 'var(--accent)'
          const marker = isSpatial ? 'arrow-spatial' : 'arrow-accent'
          connect(stepSrcEl, stepEl, color, marker, `p${pIdx}::src::${stepSrcId}`, `p${pIdx}::step::${idx}`, 'vertical')
        }
      }
    })

    // Connect Parent Sources -> Derived Sources (vertical if in same column, horizontal if different)
    ;(pdef.derived_sources || []).forEach(ds => {
      const parentSrcEl = srcNodes.find(n => n.getAttribute('data-source-id') === ds.from)
      const derivedSrcEl = srcNodes.find(n => n.getAttribute('data-source-id') === ds.id)
      if (parentSrcEl && derivedSrcEl) {
        const inSameCol = parentSrcEl.closest('.lineage-column') === derivedSrcEl.closest('.lineage-column')
        const direction = inSameCol ? 'vertical' : 'horizontal'
        connect(parentSrcEl, derivedSrcEl, 'var(--accent)', 'arrow-accent', `p${pIdx}::src::${ds.from}`, `p${pIdx}::src::${ds.id}`, direction)
      }
    })

    // Branch-aware horizontal pipeline flow
    const branchLatest = new globalThis.Map<string, { el: HTMLElement; id: string }>()
    if (baseNode) {
      branchLatest.set('main', { el: baseNode, id: `p${pIdx}::base` })
    }

    stepNodes.forEach((stepEl, idx) => {
      const st = pdef.steps[idx]
      const branchName = st.branch || 'main'

      if (st.type === 'snapshot') {
        const input = branchLatest.get(branchName)
        if (input) {
          connect(input.el, stepEl, 'var(--accent)', 'arrow-accent', input.id, `p${pIdx}::step::${idx}`, 'horizontal')
        }
        const nodeInfo = { el: stepEl, id: `p${pIdx}::step::${idx}` }
        branchLatest.set(branchName, nodeInfo)
        branchLatest.set(st.id, nodeInfo)

      } else if (st.type === 'merge') {
        const inputMain = branchLatest.get(branchName)
        if (inputMain) {
          connect(inputMain.el, stepEl, 'var(--accent)', 'arrow-accent', inputMain.id, `p${pIdx}::step::${idx}`, 'horizontal')
        }
        const mergeSrcId = st.source
        const mergeBranchInput = branchLatest.get(mergeSrcId)
        if (mergeBranchInput) {
          connect(mergeBranchInput.el, stepEl, 'var(--accent)', 'arrow-accent', mergeBranchInput.id, `p${pIdx}::step::${idx}`, 'horizontal')
        }
        branchLatest.set(branchName, { el: stepEl, id: `p${pIdx}::step::${idx}` })

      } else {
        const input = branchLatest.get(branchName)
        if (input) {
          const isSpatial = st.type === 'spatial_join' || st.type === 'clip' || st.type === 'erase' || st.type === 'intersect_overlay' || st.type === 'nearest_neighbor'
          const color = isSpatial ? 'var(--spatial)' : 'var(--accent)'
          const marker = isSpatial ? 'arrow-spatial' : 'arrow-accent'
          connect(input.el, stepEl, color, marker, input.id, `p${pIdx}::step::${idx}`, 'horizontal')
        }
        branchLatest.set(branchName, { el: stepEl, id: `p${pIdx}::step::${idx}` })
      }
    })

    const finalMain = branchLatest.get('main')
    if (finalMain && layerNode) {
      connect(finalMain.el, layerNode, 'var(--ok)', 'arrow-ok', finalMain.id, `p${pIdx}::layer`, 'horizontal')
    }

    // Connect Layer Node -> Shared GPKG Node (converging flow)
    if (layerNode && gpkgNode) {
      connect(layerNode, gpkgNode, 'var(--ok)', 'arrow-ok', `p${pIdx}::layer`, 'gpkg', 'horizontal')
    }
  })
}

function setupGlobalHoverEffects(outer: HTMLElement): void {
  const nodes = Array.from(outer.querySelectorAll('.lineage-node')) as HTMLElement[]
  const pathsMain = Array.from(outer.querySelectorAll('.lineage-path-main')) as SVGPathElement[]
  const pathsGlow = Array.from(outer.querySelectorAll('.lineage-path-glow')) as SVGPathElement[]

  const getConnectedPaths = (node: HTMLElement) => {
    const connected: { main: SVGPathElement; glow: SVGPathElement }[] = []
    const nodeIds: string[] = []

    const row = node.closest('.lineage-row') as HTMLElement | null
    const pIdx = row ? row.dataset.pipelineIndex : null

    if (node.classList.contains('lineage-src-node')) {
      const srcId = node.getAttribute('data-source-id')
      nodeIds.push(`p${pIdx}::src::${srcId}`)
    } else if (node.classList.contains('lineage-base-node')) {
      nodeIds.push(`p${pIdx}::base`)
    } else if (node.classList.contains('lineage-step-node')) {
      const idx = node.dataset.stepIndex
      nodeIds.push(`p${pIdx}::step::${idx}`)
    } else if (node.classList.contains('lineage-layer-node')) {
      nodeIds.push(`p${pIdx}::layer`)
    } else if (node.classList.contains('lineage-gpkg')) {
      nodeIds.push('gpkg')
    }

    pathsMain.forEach((mainPath, pIdx) => {
      const glowPath = pathsGlow[pIdx]
      const src = mainPath.dataset.source
      const dst = mainPath.dataset.target
      
      if (nodeIds.some(id => id === src || id === dst)) {
        connected.push({ main: mainPath, glow: glowPath })
      }
    })

    return connected
  }

  nodes.forEach(node => {
    node.addEventListener('mouseenter', () => {
      const connected = getConnectedPaths(node)
      if (connected.length === 0) return

      nodes.forEach(n => {
        n.classList.add('dimmed')
      })
      pathsMain.forEach(p => p.classList.add('dimmed'))
      pathsGlow.forEach(p => p.classList.add('dimmed'))

      node.classList.remove('dimmed')
      node.classList.add('highlighted')

      connected.forEach(conn => {
        conn.main.classList.remove('dimmed')
        conn.main.classList.add('highlighted')
        conn.glow.classList.remove('dimmed')
        conn.glow.classList.add('highlighted')

        const srcId = conn.main.dataset.source
        const dstId = conn.main.dataset.target

        nodes.forEach(n => {
          const r = n.closest('.lineage-row') as HTMLElement | null
          const rIdx = r ? r.dataset.pipelineIndex : null

          let matches = false
          if (srcId?.startsWith(`p${rIdx}::src::`) && n.classList.contains('lineage-src-node') && `p${rIdx}::src::${n.getAttribute('data-source-id')}` === srcId) {
            matches = true
          } else if (srcId === `p${rIdx}::base` && n.classList.contains('lineage-base-node')) {
            matches = true
          } else if (srcId?.startsWith(`p${rIdx}::step::`) && n.classList.contains('lineage-step-node') && `p${rIdx}::step::${n.dataset.stepIndex}` === srcId) {
            matches = true
          }

          if (dstId?.startsWith(`p${rIdx}::src::`) && n.classList.contains('lineage-src-node') && `p${rIdx}::src::${n.getAttribute('data-source-id')}` === dstId) {
            matches = true
          } else if (dstId === `p${rIdx}::base` && n.classList.contains('lineage-base-node')) {
            matches = true
          } else if (dstId?.startsWith(`p${rIdx}::step::`) && n.classList.contains('lineage-step-node') && `p${rIdx}::step::${n.dataset.stepIndex}` === dstId) {
            matches = true
          } else if (dstId === `p${rIdx}::layer` && n.classList.contains('lineage-layer-node') && `p${rIdx}::layer` === dstId) {
            matches = true
          } else if (dstId === 'gpkg' && n.classList.contains('lineage-gpkg')) {
            matches = true
          }

          if (matches) {
            n.classList.remove('dimmed')
            n.classList.add('highlighted')
          }
        })
      })
    })

    node.addEventListener('mouseleave', () => {
      nodes.forEach(n => {
        n.classList.remove('dimmed')
        n.classList.remove('highlighted')
      })
      pathsMain.forEach(p => {
        p.classList.remove('dimmed')
        p.classList.remove('highlighted')
      })
      pathsGlow.forEach(p => {
        p.classList.remove('dimmed')
        p.classList.remove('highlighted')
      })
    })

    node.addEventListener('click', () => {
      if (dragMoved) {
        return
      }
      const row = node.closest('.lineage-row') as HTMLElement | null
      const pIdxStr = row ? row.dataset.pipelineIndex : undefined
      if (!pIdxStr) {
        if (node.classList.contains('lineage-gpkg')) {
          const cfgOutputCard = document.getElementById('cfg_output')?.closest('.card') as HTMLElement | null
          if (cfgOutputCard) {
            cfgOutputCard.scrollIntoView({ behavior: 'smooth', block: 'center' })
            cfgOutputCard.classList.add('highlight-flash')
            setTimeout(() => cfgOutputCard.classList.remove('highlight-flash'), 1500)
          }
        }
        return
      }

      const pIdx = parseInt(pIdxStr, 10)
      const pipelineCards = Array.from(document.querySelectorAll<HTMLElement>('.pipeline-card'))
      const targetCard = pipelineCards[pIdx]
      if (!targetCard) return

      // Expand the pipeline card itself if collapsed
      targetCard.classList.remove('collapsed')

      let targetElement: HTMLElement | null = null

      const expandBlockOnly = (block: HTMLElement) => {
        targetCard.querySelectorAll('.pl-block').forEach(b => {
          if (b === block) b.classList.remove('collapsed'); else b.classList.add('collapsed')
        })
      }

      if (node.classList.contains('lineage-src-node')) {
        const srcId = node.getAttribute('data-source-id')
        const block = targetCard.querySelector<HTMLElement>('.pl-block[data-sec="sources"]')
        if (block) {
          expandBlockOnly(block)
          const srcCards = Array.from(block.querySelectorAll<HTMLElement>('.pl-sources > .card'))
          for (const card of srcCards) {
            const idInput = card.querySelector<HTMLInputElement>('[data-k="id"]')
            if (idInput && idInput.value.trim() === srcId) {
              targetElement = card
              break
            }
          }
          if (!targetElement) {
            targetElement = block
          }
        }
      } else if (node.classList.contains('lineage-base-node')) {
        const block = targetCard.querySelector<HTMLElement>('.pl-block[data-sec="base"]')
        if (block) {
          expandBlockOnly(block)
          targetElement = block
        }
      } else if (node.classList.contains('lineage-step-node')) {
        const stepIdxStr = node.dataset.stepIndex
        const block = targetCard.querySelector<HTMLElement>('.pl-block[data-sec="steps"]')
        if (block && stepIdxStr !== undefined) {
          expandBlockOnly(block)
          const stepIdx = parseInt(stepIdxStr, 10)
          const stepCards = Array.from(block.querySelectorAll<HTMLElement>('.pl-steps > .card'))
          targetElement = stepCards[stepIdx] || block
        }
      } else if (node.classList.contains('lineage-layer-node')) {
        const block = targetCard.querySelector<HTMLElement>('.pl-block[data-sec="output"]')
        if (block) {
          expandBlockOnly(block)
          targetElement = block
        }
      }

      if (targetElement) {
        targetElement.scrollIntoView({ behavior: 'smooth', block: 'center' })
        targetElement.classList.add('highlight-flash')
        setTimeout(() => targetElement!.classList.remove('highlight-flash'), 1500)
      }
    })
  })
}



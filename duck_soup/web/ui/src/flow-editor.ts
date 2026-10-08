// Edit a source, step or output layer from the lineage diagram, in a panel beside it, instead
// of scrolling down to its card — so the flow (and the map and table on the right) stay in
// view while you edit.
//
// The panel shows the card itself, not a copy: the card stays where it is in the DOM and is
// only *displayed* as a fixed-position panel (.flow-popped). Moving it into a separate
// drawer would break everything that finds cards by their place in the pipeline card —
// collectPipelineDef, the schema lookups, step numbering — and a copy would need its edits
// synced back. The CSS keeps its section rendered while it's popped (see .flow-popped in
// styles.css), since a fixed element inside a display:none parent isn't shown either.

import { esc, refreshIcons } from './dom'

// Which list a card lives in, by pipeline-card section.
const LIST_SELECTOR: Record<string, string> = {
  sources: '.pl-sources > .card',
  derived_sources: '.pl-derived-sources > .card',
  steps: '.pl-steps > .card',
  output: '.pl-output-layers > .card',
}

interface Locator { pipelineIdx: number; sec: string; index: number }

interface Open {
  card: HTMLElement
  plCard: HTMLElement
  loc: Locator
  title: string
  wasCollapsed: boolean
  returnFocus: HTMLElement | null
}

// Narrower than this beside the panel, the diagram would be mostly hidden by it.
const MIN_DIAGRAM_WIDTH = 480

let current: Open | null = null
let chrome: HTMLElement | null = null
let resizeObserver: ResizeObserver | null = null
// Re-fits the panel as the open card's content grows or shrinks (fields shown/hidden).
const contentObserver = new ResizeObserver(() => layout())

function locate(card: HTMLElement, sec: string): Locator | null {
  const plCard = card.closest<HTMLElement>('.pipeline-card')
  if (!plCard) return null
  const pipelineIdx = [...document.querySelectorAll('.pipeline-card')].indexOf(plCard)
  const index = [...plCard.querySelectorAll(LIST_SELECTOR[sec])].indexOf(card)
  return index < 0 ? null : { pipelineIdx, sec, index }
}

function find(loc: Locator): HTMLElement | null {
  const plCard = document.querySelectorAll<HTMLElement>('.pipeline-card')[loc.pipelineIdx]
  return plCard?.querySelectorAll<HTMLElement>(LIST_SELECTOR[loc.sec])[loc.index] ?? null
}

function ensureChrome(): HTMLElement {
  if (chrome) return chrome
  chrome = document.createElement('div')
  chrome.className = 'flow-editor'
  chrome.setAttribute('role', 'dialog')
  chrome.setAttribute('aria-labelledby', 'flowEditorTitle')
  chrome.hidden = true
  chrome.innerHTML = `
    <div class="flow-editor-head">
      <span class="flow-editor-kicker">editing</span>
      <span class="flow-editor-title" id="flowEditorTitle"></span>
      <span class="spacer"></span>
      <button type="button" class="mini ghost" data-fe-reveal title="Close the panel and show this card in its list">show in list</button>
      <button type="button" class="mini ghost" data-fe-close aria-label="Close the editing panel" title="Close (Esc)"><i data-lucide="x" style="width:14px;height:14px"></i></button>
    </div>`
  document.body.appendChild(chrome)

  chrome.querySelector('[data-fe-close]')!.addEventListener('click', () => closeFlowEditor())
  chrome.querySelector('[data-fe-reveal]')!.addEventListener('click', () => {
    const card = current?.card
    closeFlowEditor({ keepExpanded: true })
    if (!card) return
    card.scrollIntoView({ behavior: 'smooth', block: 'center' })
    card.classList.add('highlight-flash')
    setTimeout(() => card.classList.remove('highlight-flash'), 1500)
  })

  // Bubble phase on purpose: an open modal or drawer on top (overlay.ts) handles Escape in
  // the capture phase and stops it there, so this only closes the panel when it's on top.
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !current || e.defaultPrevented) return
    closeFlowEditor()
  })
  window.addEventListener('resize', layout)
  const builder = document.querySelector('main.builder')
  if (builder) {
    resizeObserver = new ResizeObserver(layout)
    resizeObserver.observe(builder)
  }
  refreshIcons()
  return chrome
}

/**
 * Pins the panel to the top right of the builder column — over the diagram's usually empty
 * right-hand side, so the flow stays in view — and makes it as tall as the card needs, up
 * to the bottom of the column (then the card scrolls). A column too narrow to keep the
 * diagram beside the panel gets the panel below the diagram instead, when that leaves room.
 */
function layout(): void {
  if (!current || !chrome) return
  const builder = document.querySelector('main.builder')?.getBoundingClientRect()
  if (!builder) return
  const gap = 12
  const bottom = Math.min(builder.bottom, window.innerHeight) - gap
  const width = Math.min(640, builder.width - 2 * gap)
  const left = builder.right - width - gap - 8 // clear of the column's scrollbar
  let top = Math.max(builder.top, 0) + gap
  let beside = true
  const lineage = document.getElementById('lineage-diagram-container')?.getBoundingClientRect()
  if (builder.width - width < MIN_DIAGRAM_WIDTH && lineage && lineage.bottom > top && bottom - lineage.bottom - gap >= 320) {
    top = lineage.bottom + gap
    beside = false
  } else {
    // Beside the diagram, but below its header, whose zoom and count controls stay usable.
    const header = document.querySelector('#lineage-diagram-container .lineage-header')?.getBoundingClientRect()
    if (header && header.bottom > top && bottom - header.bottom >= 320) top = header.bottom + gap
  }
  Object.assign(chrome.style, { top: `${top}px`, left: `${left}px`, width: `${width}px` })
  const head = chrome.querySelector<HTMLElement>('.flow-editor-head')!.offsetHeight
  const available = bottom - top - head
  const card = current.card
  card.style.setProperty('--fe-top', `${top + head}px`)
  card.style.setProperty('--fe-left', `${left}px`)
  card.style.setProperty('--fe-width', `${width}px`)
  card.style.setProperty('--fe-height', `${available}px`)
  // The card's own height (capped at `available` by its max-height), not its scrollHeight:
  // an open dropdown counts towards that, and the panel would grow around it. Only a card
  // that doesn't fit scrolls; otherwise its dropdowns may hang out below the panel.
  card.classList.remove('fe-scroll')
  const cardHeight = Math.min(card.offsetHeight, available)
  if (card.offsetHeight >= available - 1) card.classList.add('fe-scroll')
  chrome.style.height = `${head + cardHeight}px`
  reserveDiagramSpace(beside ? left : null)
}

/**
 * Shows `card` (a source, derived source, step or output layer card; `sec` is the pipeline
 * card section it lives in) in the panel. Its pipeline card is expanded and the section
 * activated first, so that a later switch back finds the card where it was.
 */
export function openInFlowEditor(card: HTMLElement, sec: string, title: string): void {
  const loc = locate(card, sec)
  if (!loc) return
  if (current?.card === card) { focusFirst(card); return }
  const returnFocus = current?.returnFocus ?? (document.activeElement as HTMLElement | null)
  closeFlowEditor({ restoreFocus: false })

  const plCard = card.closest<HTMLElement>('.pipeline-card')!
  plCard.classList.remove('collapsed')
  ;(plCard as HTMLElement & { _syncCollapse?: () => void })._syncCollapse?.()
  ;(plCard as HTMLElement & { _activateSection?: (sec: string) => void })._activateSection?.(sec)

  current = { card, plCard, loc, title, wasCollapsed: card.classList.contains('collapsed'), returnFocus }
  card.classList.remove('collapsed')
  ;(card as HTMLElement & { _syncCollapse?: () => void })._syncCollapse?.()
  card.classList.add('flow-popped')

  const el = ensureChrome()
  el.querySelector('#flowEditorTitle')!.innerHTML = esc(title)
  el.hidden = false
  layout()
  ;[...card.children].forEach(child => contentObserver.observe(child))
  focusFirst(card)
  document.dispatchEvent(new Event('flow-editor-change'))
}

// The card itself gets focus, not its first field: a combo field opens its list on focus,
// and that list scrolls itself into view, scrolling the builder away under the diagram.
// From the card, Tab goes straight into its fields.
function focusFirst(card: HTMLElement): void {
  setTimeout(() => {
    card.tabIndex = -1
    card.focus({ preventScroll: true })
  }, 0)
}

/**
 * The panel sits over the diagram's right-hand side. On a wide screen that's empty; on a
 * narrow one it would hide nodes, so while the panel is open the diagram keeps clear of it
 * (and scrolls sideways instead). `left` is the panel's left edge; null releases the space.
 */
function reserveDiagramSpace(left: number | null): void {
  const container = document.getElementById('lineage-diagram-container')
  if (!container) return
  if (left === null) {
    container.classList.remove('fe-open')
    container.style.removeProperty('--fe-reserve')
    return
  }
  const diagram = document.getElementById('lineage-diagram')
  // Measured without the reservation, or it would shrink a little more on every layout.
  const right = (diagram?.getBoundingClientRect().right ?? 0)
    + (container.classList.contains('fe-open') ? parseFloat(container.style.getPropertyValue('--fe-reserve')) || 0 : 0)
  container.classList.add('fe-open')
  container.style.setProperty('--fe-reserve', `${Math.max(0, right - left + 12)}px`)
}

export function closeFlowEditor(opts: { restoreFocus?: boolean; keepExpanded?: boolean } = {}): void {
  if (!current) return
  const { card, wasCollapsed, returnFocus } = current
  current = null
  contentObserver.disconnect()
  reserveDiagramSpace(null)
  card.classList.remove('flow-popped', 'fe-scroll')
  for (const prop of ['--fe-top', '--fe-left', '--fe-width', '--fe-height']) card.style.removeProperty(prop)
  if (wasCollapsed && !opts.keepExpanded) {
    card.classList.add('collapsed')
    ;(card as HTMLElement & { _syncCollapse?: () => void })._syncCollapse?.()
  }
  if (chrome) chrome.hidden = true
  if (opts.restoreFocus !== false && returnFocus?.isConnected) returnFocus.focus({ preventScroll: true })
  document.dispatchEvent(new Event('flow-editor-change'))
}

/** Which card is open, if any — the lineage diagram highlights its node. */
export function flowEditorTarget(): Locator | null {
  return current?.loc ?? null
}

/**
 * Called after every sync. A card that was moved (reordered) is followed, and one that was
 * removed closes the panel. Undo/redo and loading rebuild the whole pipeline card, which
 * takes the open card with it: then its replacement (same pipeline, section and position)
 * is opened instead.
 */
export function syncFlowEditor(): void {
  if (!current) return
  if (current.card.isConnected) {
    const loc = locate(current.card, current.loc.sec)
    if (loc) { current.loc = loc; layout(); return }
  }
  const { loc, title, returnFocus, plCard } = current
  const replacement = plCard.isConnected ? null : find(loc)
  current.card.classList.remove('flow-popped')
  current = null
  if (chrome) chrome.hidden = true
  if (replacement) {
    openInFlowEditor(replacement, loc.sec, title)
    if (current) (current as Open).returnFocus = returnFocus
  }
}

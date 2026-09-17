import {
  createIcons,
  Database, Trash2, Folder, File, AlertCircle, ChevronDown,
  Plus, ArrowUp, ArrowDown, X, MapPin, Link,
  ArrowLeft, Loader, GripVertical, CheckCircle, AlertTriangle, Info,
  Maximize2, Crosshair, Scissors, Eraser, Layers, GitMerge, Radar,
  UploadCloud, Filter as FilterIcon, Combine, GitBranch, Camera,
  Table, FileCode, Terminal, CheckSquare, Save, Play, Download, Copy,
  FolderOpen, ArrowRight, PlusCircle, Package, FileSpreadsheet, Globe, Map, Server, Eye,
  Columns2, Sparkles, Network, MapPinned, FileText, Edit3, Star, DatabaseZap
} from 'lucide'

// Superset of icons used across form-card modules; refreshed wholesale after
// any innerHTML swap large enough that per-call icon subsets aren't worth tracking.
export const appIcons = {
  Database, Trash2, Folder, File, AlertCircle, ChevronDown,
  Plus, ArrowUp, ArrowDown, X, MapPin, Link,
  ArrowLeft, Loader, GripVertical, CheckCircle, AlertTriangle, Info,
  Maximize2, Crosshair, Scissors, Eraser, Layers, GitMerge, Radar,
  UploadCloud, Filter: FilterIcon, Combine, GitBranch, Camera,
  Table, FileCode, Terminal, CheckSquare, Save, Play, Download, Copy,
  FolderOpen, ArrowRight, PlusCircle, Package, FileSpreadsheet, Globe, Map, Server, Eye,
  Columns2, Sparkles, Network, MapPinned, FileText, Edit3, Star, DatabaseZap
}

export const qs = <T extends Element = Element>(sel: string, root: Document | Element = document): T | null =>
  root.querySelector<T>(sel)

// HTML-escape a value before interpolating it into an `innerHTML` template string — as
// an attribute (`value="${esc(v)}"`) or as text content (`<span>${esc(v)}</span>`). Card
// values come from saved pipeline config (YAML/JSON, so arbitrary text): an unescaped `"`
// in e.g. a SQL `expr` breaks out of the surrounding `value="..."` attribute and corrupts
// the rendered card; `<`/`&` can do the same to surrounding markup.
export function esc(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

export function mkEl<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Partial<HTMLElementTagNameMap[K]> = {},
): HTMLElementTagNameMap[K] {
  return Object.assign(document.createElement(tag), props)
}

export function refreshIcons(): void {
  createIcons({ icons: appIcons })
}

// Read the trimmed value of a `[data-k="..."]` field within a card.
export function val(card: Element, k: string): string {
  const i = card.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(`[data-k="${k}"]`)
  return i ? i.value.trim() : ''
}

let collapseSeq = 0

/**
 * Wire a card's collapse behaviour, replacing four near-identical copies of this handler
 * across the card modules.
 *
 * The header stays clickable for the mouse, but the chevron becomes the real control: a
 * focusable <button> carrying aria-expanded. Headers can't be buttons themselves — several
 * of them contain <input>s, and a button may not wrap interactive content.
 *
 * `accordion` collapses siblings when this card expands. It is off by default: having every
 * list behave as an accordion meant you could never see a source and the step consuming it
 * at the same time.
 */
export function wireCollapse(
  card: HTMLElement,
  opts: { headerSel: string; chevronSel?: string; bodySel?: string; accordion?: boolean } = { headerSel: '.item-head' },
): void {
  const header = card.querySelector<HTMLElement>(opts.headerSel)
  if (!header) return

  const body = opts.bodySel ? card.querySelector<HTMLElement>(opts.bodySel) : null
  if (body && !body.id) body.id = `collapse-body-${collapseSeq++}`

  // Upgrade the decorative chevron <i> into a real button.
  const chevron = card.querySelector<HTMLElement>(opts.chevronSel ?? '.chevron')
  let toggle: HTMLButtonElement | null = null
  if (chevron && chevron.tagName !== 'BUTTON') {
    toggle = document.createElement('button')
    toggle.type = 'button'
    toggle.className = 'chevron-btn'
    chevron.replaceWith(toggle)
    toggle.appendChild(chevron)
  } else if (chevron) {
    toggle = chevron as HTMLButtonElement
  }

  const sync = () => {
    const expanded = !card.classList.contains('collapsed')
    toggle?.setAttribute('aria-expanded', String(expanded))
    toggle?.setAttribute('aria-label', expanded ? 'Collapse' : 'Expand')
    if (body) toggle?.setAttribute('aria-controls', body.id)
  }

  const setCollapsed = (collapsed: boolean) => {
    card.classList.toggle('collapsed', collapsed)
    if (!collapsed && opts.accordion && card.parentElement) {
      [...card.parentElement.children].forEach(sib => {
        if (sib !== card) sib.classList.add('collapsed')
      })
    }
    sync()
  }

  const toggleCollapsed = () => setCollapsed(!card.classList.contains('collapsed'))

  header.addEventListener('click', e => {
    // Don't swallow clicks meant for the controls living inside the header.
    if ((e.target as Element).closest('button,input,select,textarea,a,.drag-handle')) return
    toggleCollapsed()
  })
  toggle?.addEventListener('click', e => { e.stopPropagation(); toggleCollapsed() })

  sync()
  ;(card as HTMLElement & { _syncCollapse?: () => void })._syncCollapse = sync
}

export function getDragAfterElement(container: HTMLElement, y: number, itemSelector = '.map-item'): HTMLElement | null {
  const items = [...container.querySelectorAll<HTMLElement>(`${itemSelector}:not(.dragging)`)]
  return items.reduce<{ offset: number; el: HTMLElement | null }>(
    (closest, el) => {
      const box = el.getBoundingClientRect()
      const offset = y - box.top - box.height / 2
      if (offset < 0 && offset > closest.offset) return { offset, el }
      return closest
    },
    { offset: Number.NEGATIVE_INFINITY, el: null },
  ).el
}

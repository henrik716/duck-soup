import {
  createIcons,
  Database, Trash2, Folder, File, AlertCircle, ChevronDown,
  Plus, ArrowUp, ArrowDown, X, MapPin, Link,
  ArrowLeft, Loader, GripVertical, CheckCircle, AlertTriangle, Info,
  Maximize2, Crosshair, Scissors, Eraser, Layers, GitMerge, Radar,
  UploadCloud, Filter as FilterIcon, Combine, GitBranch, Camera,
  Table, FileCode, Terminal, CheckSquare, Save, Play, Download, Copy,
  FolderOpen, ArrowRight, PlusCircle, Package, FileSpreadsheet, Globe, Map, Server, Eye,
  Columns2, Sparkles, Network, MapPinned, FileText, Edit3
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
  Columns2, Sparkles, Network, MapPinned, FileText, Edit3
}

export const qs = <T extends Element = Element>(sel: string, root: Document | Element = document): T | null =>
  root.querySelector<T>(sel)

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

export function getDragAfterElement(container: HTMLElement, y: number): HTMLElement | null {
  const items = [...container.querySelectorAll<HTMLElement>('.map-item:not(.dragging)')]
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

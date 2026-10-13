import {
  createElement, type IconNode,
  AlertCircle, AlertTriangle, AlignLeft, ArrowDown, ArrowLeft, ArrowRight, ArrowUp, BookOpen,
  Camera, CheckCircle, CheckSquare, ChevronDown, ChevronRight, ChevronUp, ChevronsDown,
  ChevronsUp, CircleHelp, Code, Columns2, Combine, Copy, CornerDownRight, Crosshair, Database,
  DatabaseZap, Download, Edit3, Eraser, Eye, File, FileCode, FileSpreadsheet, FileText,
  Filter, Folder, FolderOpen, FolderOutput, GitBranch, GitMerge, Globe, GripVertical,
  HardDrive, Hash, History, House, Info, Layers, Link, Loader, Map, MapPin, MapPinned,
  Maximize2, Moon, Network, Package, Play, Plus, PlusCircle, Radar, RefreshCw, Save,
  Scissors, Search, Server, Sliders, Sparkles, Spline, Split, Square, Star, Sun, Table, TableProperties, Terminal,
  Trash2, UploadCloud, X,
} from 'lucide'

// ---- icons ----
// Markup names an icon with a placeholder, `<i data-lucide="trash-2">`, and renderIcons turns
// those into lucide's <svg>s. Not lucide's own createIcons: that looks at every
// [data-lucide] element in the whole document, rendered svgs included (they keep the
// attribute), rebuilds each one it has a definition for and logs a warning with the full
// markup of each one it doesn't. Called with a few icons per card, as it was, that came to
// ~90 000 warnings and as many rebuilt icons in one editing session.

/** Every icon the editor uses, keyed the way lucide names its exports: `trash-2` → `Trash2`.
 *  An icon missing here doesn't render (see icons.test.ts, which checks the markup). */
export const ICONS: Record<string, IconNode> = {
  AlertCircle, AlertTriangle, AlignLeft, ArrowDown, ArrowLeft, ArrowRight, ArrowUp, BookOpen,
  Camera, CheckCircle, CheckSquare, ChevronDown, ChevronRight, ChevronUp, ChevronsDown,
  ChevronsUp, CircleHelp, Code, Columns2, Combine, Copy, CornerDownRight, Crosshair, Database,
  DatabaseZap, Download, Edit3, Eraser, Eye, File, FileCode, FileSpreadsheet, FileText,
  Filter, Folder, FolderOpen, FolderOutput, GitBranch, GitMerge, Globe, GripVertical,
  HardDrive, Hash, History, House, Info, Layers, Link, Loader, Map, MapPin, MapPinned,
  Maximize2, Moon, Network, Package, Play, Plus, PlusCircle, Radar, RefreshCw, Save,
  Scissors, Search, Server, Sliders, Sparkles, Spline, Split, Square, Star, Sun, Table, TableProperties, Terminal,
  Trash2, UploadCloud, X,
}

/** lucide's name → export mapping (its toPascalCase): `alert-circle` → `AlertCircle`. */
export function iconKey(name: string): string {
  return name.replace(/(\w)(\w*)(_|-|\s*)/g, (_, first: string, rest: string) => first.toUpperCase() + rest.toLowerCase())
}

/** Replace one icon, a placeholder <i> or an already rendered <svg>, with icon `name`. Its
 *  other attributes (style, classes like spin-animation) carry over, as with lucide's own
 *  rendering. Returns the new <svg>, or `el` itself for an unknown name. */
export function setIcon(el: Element, name: string): Element {
  const node = ICONS[iconKey(name)]
  if (!node) {
    console.warn(`duck soup: no icon "${name}" (add it to ICONS in icons.ts)`)
    return el
  }
  const attrs: Record<string, string> = {}
  for (const a of Array.from(el.attributes)) attrs[a.name] = a.value
  // An svg being swapped still has its old icon's lucide classes.
  const own = (attrs['class'] ?? '').split(/\s+/).filter(c => c && c !== 'lucide' && !c.startsWith('lucide-'))
  const svg = createElement(node, {
    ...attrs,
    'data-lucide': name,
    class: ['lucide', `lucide-${name}`, ...own].join(' '),
  })
  el.replaceWith(svg)
  return svg
}

/** Render every icon placeholder under `root` (the whole page by default). Icons that are
 *  already rendered are left alone, so call it freely after building markup. `root` may be
 *  an element not attached to the page yet, e.g. a card before it's inserted. */
export function renderIcons(root: ParentNode = document): void {
  root.querySelectorAll('i[data-lucide]').forEach(el => setIcon(el, el.getAttribute('data-lucide')!))
}

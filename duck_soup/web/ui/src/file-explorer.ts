import {
  createElement, Folder, FolderOpen, ArrowLeft, ArrowRight, ArrowUp, File, FileSpreadsheet,
  Package, Database, Server, Map, House, HardDrive, Search, ChevronRight, X, Loader,
  FileCode, FolderOutput, ChevronDown, ChevronUp,
} from 'lucide'
import type { IconNode } from 'lucide'
import { fetchFiles, fetchFilePlaces, searchFiles } from './api'
import { qs, mkEl, esc } from './dom'
import { openOverlay, closeOverlay } from './overlay'
import { getSourceIcon } from './lineage'
import { EXT_FORMAT } from './state'
import type { FileEntry, FilePlace, FilesResponse } from './types'

// ---- file explorer ----
// A standard open/save dialog: back/forward/up, clickable breadcrumbs (click the bar or
// Ctrl+L to type a path), a places sidebar, sortable columns, recursive search, type-ahead,
// and a footer filename box. Single click selects, double click / Enter opens or picks.

export interface FileExplorerOptions {
  // 'save' lets the user type a filename that doesn't exist yet (pipeline output).
  mode?: 'open' | 'save'
  // Extensions (no dot) shown under the "data files" filter; folders always show.
  exts?: string[]
  title?: string
}

type SortKey = 'name' | 'type' | 'size' | 'modified'

const ICON_NODES: Record<string, IconNode> = {
  'file-spreadsheet': FileSpreadsheet, package: Package, database: Database,
  server: Server, map: Map,
}
const PLACE_ICONS: Record<FilePlace['kind'], IconNode> = {
  project: Package, data: Database, pipelines: FileCode, output: FolderOutput,
  home: House, drive: HardDrive,
}
const LAST_DIR_KEY = 'duckSoup.fileExplorer.lastDir'
const DEFAULT_EXTS = Object.keys(EXT_FORMAT)

let target: HTMLInputElement | null = null
let opts: Required<FileExplorerOptions> = { mode: 'open', exts: DEFAULT_EXTS, title: 'select file' }
let current: FilesResponse | null = null
let entries: FileEntry[] = []          // what's currently rendered, in display order
let selected: FileEntry | null = null
let history: string[] = []
let historyIdx = -1
let sortKey: SortKey = 'name'
let sortAsc = true
let dataOnly = true
let searchResults: FileEntry[] | null = null
let searchTruncated = false
let searchAbort: AbortController | null = null
let searchTimer: number | undefined
let typeahead = ''
let typeaheadTimer: number | undefined
let wired = false

function icon(node: IconNode, size = 14): SVGElement {
  const svg = createElement(node)
  svg.setAttribute('width', String(size))
  svg.setAttribute('height', String(size))
  svg.setAttribute('aria-hidden', 'true')
  return svg
}

function ext(name: string): string {
  const i = name.lastIndexOf('.')
  return i > 0 ? name.slice(i + 1).toLowerCase() : ''
}

// A File Geodatabase (.gdb) is a directory on disk, but a leaf data source: picking it
// selects it rather than browsing into it.
const isGdb = (e: FileEntry): boolean => e.is_dir && ext(e.name) === 'gdb'
const isFolder = (e: FileEntry): boolean => e.is_dir && !isGdb(e)

function typeLabel(e: FileEntry): string {
  if (isFolder(e)) return 'folder'
  const x = ext(e.name)
  return x ? x : 'file'
}

function fmtSize(n: number | null): string {
  if (n == null) return ''
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`
}

function fmtDate(t: number | null): string {
  if (t == null) return ''
  const d = new Date(t * 1000)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  return sameDay
    ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

function joinPath(dir: string, name: string): string {
  if (!dir) return name
  return dir.endsWith('/') ? dir + name : `${dir}/${name}`
}

function isAbsolute(p: string): boolean {
  return p.startsWith('/') || /^[a-zA-Z]:[\\/]/.test(p)
}

function splitPath(p: string): { dir: string; name: string } {
  const norm = p.replace(/\\/g, '/')
  const i = norm.lastIndexOf('/')
  if (i < 0) return { dir: '', name: norm }
  // Keep the slash on a bare drive/root ("C:/", "/") so it still resolves as absolute.
  const dir = norm.slice(0, i) || '/'
  return { dir: /^[a-zA-Z]:$/.test(dir) ? dir + '/' : dir, name: norm.slice(i + 1) }
}

function readLastDir(): string | null {
  try { return localStorage.getItem(LAST_DIR_KEY) } catch { return null }
}
function writeLastDir(p: string): void {
  try { localStorage.setItem(LAST_DIR_KEY, p) } catch { /* storage unavailable */ }
}

// ---- open / close ----

export function openFileExplorer(input: HTMLInputElement, options: FileExplorerOptions = {}): void {
  wire()
  target = input
  opts = {
    mode: options.mode ?? 'open',
    exts: options.exts ?? DEFAULT_EXTS,
    title: options.title ?? (options.mode === 'save' ? 'save as' : 'select file'),
  }
  dataOnly = true
  history = []
  historyIdx = -1
  selected = null
  clearSearch()

  qs('#fxTitle')!.textContent = opts.title
  qs('#fxChoose')!.textContent = opts.mode === 'save' ? 'save here' : 'select'
  const filter = qs<HTMLSelectElement>('#fxFilter')!
  const extList = opts.exts.map(x => '.' + x).join(', ')
  filter.options[0].text = opts.exts.length <= 3 ? `${extList} files` : 'data files'
  filter.title = `shows folders and ${extList}`
  filter.value = 'data'
  const nameInp = qs<HTMLInputElement>('#fxFileName')!
  nameInp.value = ''
  nameInp.placeholder = opts.mode === 'save' ? 'output.gpkg' : 'pick a file, or paste a path'

  const modal = qs<HTMLElement>('#fileModal')!
  modal.classList.add('show')
  openOverlay(modal, closeFileExplorer)

  void loadPlaces()

  // Start where the field already points (preselecting the file), else where the user was last.
  const value = input.value.trim()
  const looksLocal = value && !/^[a-z][a-z0-9+.-]*:\/\//i.test(value) && !/^(WFS|PG):/i.test(value)
  if (looksLocal) {
    const { dir, name } = splitPath(value)
    nameInp.value = opts.mode === 'save' ? name : ''
    void navigate(dir, { preselect: name, fallback: readLastDir() ?? '' })
  } else {
    void navigate(readLastDir() ?? '', { fallback: '' })
  }
  setTimeout(() => qs<HTMLElement>('#fileExplorerList')?.focus(), 30)
}

export function closeFileExplorer(): void {
  const modal = qs<HTMLElement>('#fileModal')
  modal?.classList.remove('show')
  if (modal) closeOverlay(modal)
  searchAbort?.abort()
  target = null
}

function choose(path: string): void {
  if (target) {
    target.value = path
    target.dispatchEvent(new Event('input', { bubbles: true }))
  }
  if (current) writeLastDir(current.current)
  closeFileExplorer()
}

// ---- navigation ----

async function navigate(
  path: string,
  { push = true, preselect, fallback }: { push?: boolean; preselect?: string; fallback?: string } = {},
): Promise<void> {
  const listEl = qs<HTMLElement>('#fileExplorerList')!
  setStatus('paddling over…', true)
  try {
    const data = await fetchFiles(path)
    current = data
    clearSearch()
    if (push) {
      history = history.slice(0, historyIdx + 1)
      if (history[historyIdx] !== data.current) { history.push(data.current); historyIdx = history.length - 1 }
    }
    selected = preselect ? data.entries.find(e => e.name === preselect) ?? null : null
    renderCrumbs()
    renderList()
    updateNavButtons()
    highlightPlace()
    if (selected) scrollSelectedIntoView()
  } catch (e) {
    if (fallback !== undefined && fallback !== path) {
      return navigate(fallback, { push, preselect })
    }
    listEl.innerHTML = ''
    listEl.appendChild(emptyState('this pond is off limits', (e as Error).message, true))
    setStatus('')
  }
}

function goBack(): void {
  if (historyIdx > 0) { historyIdx--; void navigate(history[historyIdx], { push: false }) }
}
function goForward(): void {
  if (historyIdx < history.length - 1) { historyIdx++; void navigate(history[historyIdx], { push: false }) }
}
function goUp(): void {
  if (current?.parent != null) {
    const leaving = current.crumbs[current.crumbs.length - 1]?.name
    void navigate(current.parent, { preselect: leaving })
  }
}

function updateNavButtons(): void {
  qs<HTMLButtonElement>('#fxBack')!.disabled = historyIdx <= 0
  qs<HTMLButtonElement>('#fxForward')!.disabled = historyIdx >= history.length - 1
  qs<HTMLButtonElement>('#fxUp')!.disabled = current?.parent == null
}

// ---- breadcrumbs / path editing ----

function renderCrumbs(): void {
  const el = qs<HTMLElement>('#fxCrumbs')!
  el.innerHTML = ''
  const crumbs = current?.crumbs ?? []
  crumbs.forEach((c, i) => {
    if (i > 0) el.appendChild(icon(ChevronRight, 12))
    const b = mkEl('button', { className: 'explorer-crumb', type: 'button' })
    b.textContent = c.name
    b.title = c.path || 'project folder'
    if (i === crumbs.length - 1) b.setAttribute('aria-current', 'location')
    b.onclick = ev => { ev.stopPropagation(); void navigate(c.path) }
    el.appendChild(b)
  })
  el.scrollLeft = el.scrollWidth
}

function editPath(): void {
  const crumbs = qs<HTMLElement>('#fxCrumbs')!
  const inp = qs<HTMLInputElement>('#fxPathInput')!
  crumbs.hidden = true
  inp.hidden = false
  inp.value = current?.current ?? ''
  inp.focus()
  inp.select()
}

function stopEditPath(): void {
  qs<HTMLElement>('#fxCrumbs')!.hidden = false
  qs<HTMLInputElement>('#fxPathInput')!.hidden = true
}

// ---- places ----

async function loadPlaces(): Promise<void> {
  const nav = qs<HTMLElement>('#fxPlaces')!
  if (nav.childElementCount) { highlightPlace(); return }
  const places = await fetchFilePlaces()
  nav.innerHTML = ''
  let lastWasDrive = false
  for (const p of places) {
    if (p.kind === 'drive' && !lastWasDrive) {
      nav.appendChild(mkEl('div', { className: 'explorer-places-label', textContent: 'drives' }))
    }
    lastWasDrive = p.kind === 'drive'
    const b = mkEl('button', { className: 'explorer-place', type: 'button' })
    b.dataset['path'] = p.path
    b.title = p.path || 'project folder'
    b.append(icon(PLACE_ICONS[p.kind] ?? Folder), mkEl('span', { textContent: p.name }))
    b.onclick = () => void navigate(p.path)
    nav.appendChild(b)
  }
  highlightPlace()
}

function highlightPlace(): void {
  const cur = current?.current
  document.querySelectorAll<HTMLElement>('#fxPlaces .explorer-place').forEach(b =>
    b.classList.toggle('active', b.dataset['path'] === cur))
}

// ---- search ----

function clearSearch(): void {
  searchAbort?.abort()
  window.clearTimeout(searchTimer)
  searchResults = null
  searchTruncated = false
  const s = qs<HTMLInputElement>('#fxSearch')
  if (s) s.value = ''
  qs<HTMLElement>('#fxSearchClear')?.toggleAttribute('hidden', true)
}

function onSearchInput(): void {
  const q = qs<HTMLInputElement>('#fxSearch')!.value.trim()
  qs<HTMLElement>('#fxSearchClear')!.toggleAttribute('hidden', !q)
  searchAbort?.abort()
  window.clearTimeout(searchTimer)
  searchResults = null
  // Instant: filter this folder. Then, debounced: search every subfolder below it.
  renderList()
  if (q.length < 2) return
  setStatus(`fishing for “${q}” in subfolders…`, true)
  searchTimer = window.setTimeout(async () => {
    searchAbort = new AbortController()
    try {
      const res = await searchFiles(q, current?.current ?? '', searchAbort.signal)
      searchResults = res.results
      searchTruncated = res.truncated
      renderList()
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setStatus('search failed')
    }
  }, 250)
}

// ---- list ----

function visibleEntries(): FileEntry[] {
  const q = qs<HTMLInputElement>('#fxSearch')?.value.trim().toLowerCase() ?? ''
  let list = searchResults ?? (current?.entries ?? []).filter(e => !q || e.name.toLowerCase().includes(q))
  if (dataOnly) {
    const allowed = new Set(opts.exts)
    list = list.filter(e => isFolder(e) || allowed.has(ext(e.name)))
  }
  const dir = sortAsc ? 1 : -1
  const val = (e: FileEntry): string | number => {
    switch (sortKey) {
      case 'size': return e.size ?? -1
      case 'modified': return e.modified ?? 0
      case 'type': return typeLabel(e)
      default: return e.name.toLowerCase()
    }
  }
  return [...list].sort((a, b) => {
    // Folders first, like every desktop explorer, regardless of sort column.
    if (isFolder(a) !== isFolder(b)) return isFolder(a) ? -1 : 1
    const va = val(a), vb = val(b)
    const c = va < vb ? -1 : va > vb ? 1 : a.name.localeCompare(b.name)
    return c * dir
  })
}

function renderList(): void {
  const listEl = qs<HTMLElement>('#fileExplorerList')!
  entries = visibleEntries()
  listEl.innerHTML = ''
  const q = qs<HTMLInputElement>('#fxSearch')?.value.trim() ?? ''

  document.querySelectorAll<HTMLElement>('#fxHead [data-sort]').forEach(h => {
    const active = h.dataset['sort'] === sortKey
    h.setAttribute('aria-sort', active ? (sortAsc ? 'ascending' : 'descending') : 'none')
    h.querySelector('svg')?.remove()
    if (active) h.appendChild(icon(sortAsc ? ChevronUp : ChevronDown, 12))
  })

  if (!entries.length) {
    const hidden = (current?.entries.length ?? 0) > 0 && dataOnly && !q
    listEl.appendChild(q
      ? emptyState('no ducks found', searchResults ? `nothing matching “${q}” here or in subfolders` : `nothing matching “${q}” in this folder`)
      : hidden
        ? emptyState('no data files here', 'switch the filter to “all files” to see everything')
        : emptyState('an empty pond', 'this folder has nothing in it'))
  }

  for (const e of entries) listEl.appendChild(renderRow(e))
  if (selected && !entries.some(e => e.path === selected!.path)) selected = null
  syncSelection()
  renderStatus()
}

function renderRow(e: FileEntry): HTMLElement {
  const row = mkEl('div', { className: `explorer-item ${isFolder(e) ? 'directory' : 'file'}` })
  row.setAttribute('role', 'option')
  row.tabIndex = -1
  row.dataset['path'] = e.path

  const fmt = EXT_FORMAT[ext(e.name)]
  let iconEl: SVGElement
  if (isFolder(e)) {
    iconEl = icon(Folder)
  } else if (fmt) {
    const { icon: name, color } = getSourceIcon(fmt)
    iconEl = icon(ICON_NODES[name] ?? File)
    iconEl.style.color = color
  } else {
    iconEl = icon(File)
  }

  const name = mkEl('span', { className: 'explorer-name' })
  name.appendChild(iconEl)
  const label = mkEl('span', { className: 'explorer-name-text' })
  label.innerHTML = highlight(e.name)
  name.appendChild(label)
  if (e.folder !== undefined) {
    name.appendChild(mkEl('span', { className: 'explorer-in', textContent: `in ${e.folder || '/'}`, title: e.folder }))
  }

  const type = mkEl('span', { className: 'explorer-col-type' })
  if (fmt && !isFolder(e)) type.appendChild(mkEl('span', { className: 'explorer-badge', textContent: typeLabel(e) }))
  else type.textContent = typeLabel(e)

  row.append(
    name,
    type,
    mkEl('span', { className: 'explorer-col-size', textContent: fmtSize(e.size) }),
    mkEl('span', { className: 'explorer-col-date', textContent: fmtDate(e.modified) }),
  )
  row.onclick = () => select(e)
  row.ondblclick = () => activate(e)
  return row
}

function highlight(name: string): string {
  const q = qs<HTMLInputElement>('#fxSearch')?.value.trim() ?? ''
  if (!q) return esc(name)
  const i = name.toLowerCase().indexOf(q.toLowerCase())
  if (i < 0) return esc(name)
  return `${esc(name.slice(0, i))}<mark>${esc(name.slice(i, i + q.length))}</mark>${esc(name.slice(i + q.length))}`
}

function emptyState(title: string, detail: string, warn = false): HTMLElement {
  const el = mkEl('div', { className: `explorer-empty${warn ? ' warn' : ''}` })
  el.innerHTML = `<div class="explorer-empty-duck" aria-hidden="true">🦆</div><div class="explorer-empty-title">${esc(title)}</div><div>${esc(detail)}</div>`
  return el
}

function setStatus(text: string, busy = false): void {
  const el = qs<HTMLElement>('#fxStatus')!
  el.innerHTML = ''
  if (busy) { const s = icon(Loader, 12); s.classList.add('spin-animation'); el.appendChild(s) }
  el.appendChild(document.createTextNode(text))
}

function renderStatus(): void {
  const total = current?.entries.length ?? 0
  const q = qs<HTMLInputElement>('#fxSearch')?.value.trim() ?? ''
  if (searchResults) {
    setStatus(`${entries.length} match${entries.length === 1 ? '' : 'es'} in subfolders${searchTruncated ? ' (stopped early, narrow it down or search a deeper folder)' : ''}`)
  } else if (q) {
    if (q.length < 2) setStatus(`${entries.length} in this folder · type one more letter to search subfolders`)
  } else {
    const hidden = total - entries.length
    setStatus(`${entries.length} item${entries.length === 1 ? '' : 's'}${hidden > 0 ? ` · ${hidden} other file${hidden === 1 ? '' : 's'} hidden by filter` : ''}`)
  }
}

// ---- selection ----

function select(e: FileEntry | null, focus = true): void {
  selected = e
  syncSelection(focus)
  if (e && !isFolder(e)) {
    // Save mode: the footer holds a bare name joined to the current folder; open mode
    // (and search hits, which may live elsewhere) hold the full path.
    qs<HTMLInputElement>('#fxFileName')!.value =
      opts.mode === 'save' && e.folder === undefined ? e.name : e.path
  }
}

function syncSelection(focus = false): void {
  const listEl = qs<HTMLElement>('#fileExplorerList')!
  let found: HTMLElement | null = null
  listEl.querySelectorAll<HTMLElement>('.explorer-item').forEach(r => {
    const on = !!selected && r.dataset['path'] === selected.path
    r.classList.toggle('selected', on)
    r.setAttribute('aria-selected', String(on))
    if (on) found = r
  })
  if (found && focus) scrollSelectedIntoView()
  qs<HTMLButtonElement>('#fxChoose')!.disabled = !canChoose()
}

function scrollSelectedIntoView(): void {
  qs<HTMLElement>('#fileExplorerList .explorer-item.selected')?.scrollIntoView({ block: 'nearest' })
}

function canChoose(): boolean {
  if (selected && !isFolder(selected)) return true
  return !!qs<HTMLInputElement>('#fxFileName')?.value.trim()
}

function activate(e: FileEntry): void {
  if (isFolder(e)) void navigate(e.path)
  else choose(e.path)
}

function chooseFromFooter(): void {
  const typed = qs<HTMLInputElement>('#fxFileName')!.value.trim()
  if (selected && isFolder(selected) && (!typed || opts.mode === 'open')) { activate(selected); return }
  if (!typed) { if (selected) activate(selected); return }
  const full = isAbsolute(typed) || typed.includes('/') || typed.includes('\\')
    ? typed.replace(/\\/g, '/')
    : joinPath(current?.current ?? '', typed)
  // A typed folder path navigates there instead of choosing it.
  const asFolder = current?.entries.find(e => e.path === full && isFolder(e))
  if (asFolder) { void navigate(asFolder.path); return }
  choose(full)
}

function moveSelection(delta: number | 'start' | 'end'): void {
  if (!entries.length) return
  const i = selected ? entries.findIndex(e => e.path === selected!.path) : -1
  let next: number
  if (delta === 'start') next = 0
  else if (delta === 'end') next = entries.length - 1
  else next = i < 0 ? (delta > 0 ? 0 : entries.length - 1) : Math.max(0, Math.min(entries.length - 1, i + delta))
  select(entries[next])
}

function onListKey(ev: KeyboardEvent): void {
  if (ev.altKey && ev.key === 'ArrowUp') { ev.preventDefault(); goUp(); return }
  switch (ev.key) {
    case 'ArrowDown': ev.preventDefault(); moveSelection(1); return
    case 'ArrowUp': ev.preventDefault(); moveSelection(-1); return
    case 'PageDown': ev.preventDefault(); moveSelection(10); return
    case 'PageUp': ev.preventDefault(); moveSelection(-10); return
    case 'Home': ev.preventDefault(); moveSelection('start'); return
    case 'End': ev.preventDefault(); moveSelection('end'); return
    case 'Enter': ev.preventDefault(); if (selected) activate(selected); return
    case 'Backspace': ev.preventDefault(); goUp(); return
  }
  // Type-ahead: typing jumps to the first name starting with the typed letters.
  if (ev.key.length === 1 && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
    window.clearTimeout(typeaheadTimer)
    typeahead += ev.key.toLowerCase()
    typeaheadTimer = window.setTimeout(() => { typeahead = '' }, 700)
    const hit = entries.find(e => e.name.toLowerCase().startsWith(typeahead))
    if (hit) select(hit)
  }
}

// ---- wiring ----

function wire(): void {
  if (wired) return
  wired = true

  qs('#fxBack')!.addEventListener('click', goBack)
  qs('#fxForward')!.addEventListener('click', goForward)
  qs('#fxUp')!.addEventListener('click', goUp)
  qs('#fxCancel')!.addEventListener('click', closeFileExplorer)
  qs('#fxChoose')!.addEventListener('click', chooseFromFooter)

  qs('#fxCrumbs')!.addEventListener('click', editPath)
  qs('#fxEditPath')!.addEventListener('click', editPath)
  const pathInp = qs<HTMLInputElement>('#fxPathInput')!
  pathInp.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') {
      ev.preventDefault()
      const p = pathInp.value.trim()
      stopEditPath()
      const looksLikeFile = !!ext(splitPath(p).name) && !p.toLowerCase().endsWith('.gdb')
      if (looksLikeFile) {
        // A pasted file path: go to its folder and select it.
        const { dir, name } = splitPath(p)
        void navigate(dir, { preselect: name }).then(() => { if (selected) select(selected) })
      } else {
        void navigate(p)
      }
      qs<HTMLElement>('#fileExplorerList')!.focus()
    } else if (ev.key === 'Escape') {
      ev.preventDefault(); ev.stopPropagation()
      stopEditPath()
    }
  })
  pathInp.addEventListener('blur', stopEditPath)

  const search = qs<HTMLInputElement>('#fxSearch')!
  search.addEventListener('input', onSearchInput)
  search.addEventListener('keydown', ev => {
    if (ev.key === 'ArrowDown') { ev.preventDefault(); qs<HTMLElement>('#fileExplorerList')!.focus(); moveSelection('start') }
    else if (ev.key === 'Enter') { ev.preventDefault(); if (entries.length === 1) activate(entries[0]); else { qs<HTMLElement>('#fileExplorerList')!.focus(); moveSelection('start') } }
    else if (ev.key === 'Escape' && search.value) { ev.preventDefault(); ev.stopPropagation(); clearSearch(); renderList() }
  })
  qs('#fxSearchClear')!.addEventListener('click', () => { clearSearch(); renderList(); search.focus() })

  qs<HTMLSelectElement>('#fxFilter')!.addEventListener('change', ev => {
    dataOnly = (ev.target as HTMLSelectElement).value === 'data'
    renderList()
  })

  const nameInp = qs<HTMLInputElement>('#fxFileName')!
  nameInp.addEventListener('input', () => { qs<HTMLButtonElement>('#fxChoose')!.disabled = !canChoose() })
  nameInp.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); chooseFromFooter() } })

  qs('#fileExplorerList')!.addEventListener('keydown', ev => onListKey(ev as KeyboardEvent))

  document.querySelectorAll<HTMLElement>('#fxHead [data-sort]').forEach(h => {
    h.addEventListener('click', () => {
      const key = h.dataset['sort'] as SortKey
      if (sortKey === key) sortAsc = !sortAsc
      else { sortKey = key; sortAsc = key === 'name' || key === 'type' }
      renderList()
    })
  })

  // Dialog-wide shortcuts, while it's open.
  qs('#fileModal')!.addEventListener('keydown', ev => {
    const e = ev as KeyboardEvent
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') { e.preventDefault(); search.focus(); search.select() }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'l') { e.preventDefault(); editPath() }
    else if (e.altKey && e.key === 'ArrowLeft') { e.preventDefault(); goBack() }
    else if (e.altKey && e.key === 'ArrowRight') { e.preventDefault(); goForward() }
  })

  // Static toolbar/header icons.
  const put = (sel: string, node: IconNode, size = 14) => qs(sel)?.prepend(icon(node, size))
  put('#fxBack', ArrowLeft); put('#fxForward', ArrowRight); put('#fxUp', ArrowUp)
  put('#fxSearchIcon', Search); put('#fxSearchClear', X, 12); put('#fxTitleIcon', FolderOpen, 16)
}

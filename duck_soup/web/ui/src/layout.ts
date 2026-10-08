// Resizable panes. Two splitters — builder | side, and map / bottom panel inside the side
// column — drive CSS variables on their grid containers (`--builder-w` on .wrap, `--map-h`
// on aside.side), plus a collapse toggle that shrinks the map to a strip holding just its
// toolbar. Sizes are remembered in localStorage. The map follows along through its own
// ResizeObserver (map.ts), and the lineage diagram through its own.
//
// Below the stacked-layout breakpoint (980px, see styles.css) the splitters are hidden and
// the stacked layout's fixed sizes win, so nothing here needs to know about it.

const STORAGE_KEY = 'duckSoup.layout'

const COLS = { def: 58, min: 30, max: 75 }
const MAP = { def: 35, min: 12, max: 80 }
const KEY_STEP = 2

interface LayoutState { cols: number; map: number; mapCollapsed: boolean }

const clamp = (v: number, lim: { min: number; max: number }) => Math.min(lim.max, Math.max(lim.min, v))

function load(): LayoutState {
  const s: LayoutState = { cols: COLS.def, map: MAP.def, mapCollapsed: false }
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')
    if (typeof raw.cols === 'number') s.cols = clamp(raw.cols, COLS)
    if (typeof raw.map === 'number') s.map = clamp(raw.map, MAP)
    s.mapCollapsed = raw.mapCollapsed === true
  } catch { /* private mode / bad JSON */ }
  return s
}

function save(s: LayoutState): void {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(s)) } catch { /* private mode etc. */ }
}

export function initLayout(): void {
  const wrap = document.querySelector<HTMLElement>('.wrap')
  const side = document.querySelector<HTMLElement>('aside.side')
  const colSplit = document.getElementById('split-cols')
  const mapSplit = document.getElementById('split-map')
  const toggle = document.getElementById('mapCollapseBtn')
  if (!wrap || !side || !colSplit || !mapSplit || !toggle) return

  const state = load()

  const apply = () => {
    wrap.style.setProperty('--builder-w', `${state.cols}%`)
    side.style.setProperty('--map-h', `${state.map}%`)
    side.classList.toggle('map-collapsed', state.mapCollapsed)
    colSplit.setAttribute('aria-valuenow', String(Math.round(state.cols)))
    mapSplit.setAttribute('aria-valuenow', String(state.mapCollapsed ? 0 : Math.round(state.map)))
    toggle.setAttribute('aria-pressed', String(state.mapCollapsed))
    const label = state.mapCollapsed ? 'Expand map' : 'Collapse map'
    toggle.setAttribute('aria-label', label)
    toggle.title = label
  }

  wireSplitter(colSplit, {
    axis: 'x',
    container: wrap,
    set: pct => { state.cols = clamp(pct, COLS); apply() },
    get: () => state.cols,
    reset: () => { state.cols = COLS.def; apply() },
    commit: () => save(state),
  })
  wireSplitter(mapSplit, {
    axis: 'y',
    container: side,
    // Dragging the handle of a collapsed map is the obvious way to get it back.
    set: pct => { state.mapCollapsed = false; state.map = clamp(pct, MAP); apply() },
    get: () => (state.mapCollapsed ? 0 : state.map),
    reset: () => { state.mapCollapsed = false; state.map = MAP.def; apply() },
    commit: () => save(state),
  })

  toggle.addEventListener('click', () => {
    state.mapCollapsed = !state.mapCollapsed
    apply()
    save(state)
  })

  apply()
}

interface SplitterOpts {
  axis: 'x' | 'y'
  container: HTMLElement
  set: (pct: number) => void
  get: () => number
  reset: () => void
  commit: () => void
}

function wireSplitter(handle: HTMLElement, o: SplitterOpts): void {
  const pctAt = (e: PointerEvent) => {
    const r = o.container.getBoundingClientRect()
    return o.axis === 'x'
      ? ((e.clientX - r.left) / r.width) * 100
      : ((e.clientY - r.top) / r.height) * 100
  }

  handle.addEventListener('pointerdown', e => {
    if (e.button !== 0) return
    e.preventDefault()
    handle.setPointerCapture(e.pointerId)
    document.body.classList.add(o.axis === 'x' ? 'resizing-x' : 'resizing-y')

    const move = (ev: PointerEvent) => o.set(pctAt(ev))
    const end = () => {
      handle.removeEventListener('pointermove', move)
      handle.removeEventListener('pointerup', end)
      handle.removeEventListener('pointercancel', end)
      document.body.classList.remove('resizing-x', 'resizing-y')
      o.commit()
    }
    handle.addEventListener('pointermove', move)
    handle.addEventListener('pointerup', end)
    handle.addEventListener('pointercancel', end)
  })

  handle.addEventListener('dblclick', () => { o.reset(); o.commit() })

  handle.addEventListener('keydown', e => {
    const dec = o.axis === 'x' ? 'ArrowLeft' : 'ArrowUp'
    const inc = o.axis === 'x' ? 'ArrowRight' : 'ArrowDown'
    if (e.key === dec || e.key === inc) {
      o.set(o.get() + (e.key === inc ? KEY_STEP : -KEY_STEP))
    } else if (e.key === 'Home' || e.key === 'Enter') {
      o.reset()
    } else {
      return
    }
    e.preventDefault()
    o.commit()
  })
}

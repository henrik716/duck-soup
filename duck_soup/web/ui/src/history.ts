import { collectConfig, hydrate } from './config-io'
import { showToast } from './toast'
import type { Config } from './types'

// Undo/redo. This is cheap here because `collectConfig()` and `hydrate()` already
// round-trip the entire editor state in both directions — a snapshot is just a Config,
// and restoring one is a hydrate. No per-widget undo bookkeeping needed.
//
// Snapshots are pushed on *structural* mutations only (add/remove/reorder a card, auto-map,
// load, new). Text edits are not snapshotted individually: they'd flood the stack, and the
// input elements themselves already give native per-field undo.
//
// Known trade-off: hydrate() rebuilds the pipeline DOM, so undo does not restore focus
// position. Collapse state survives via the persistence in pipeline-card.ts.

const MAX_DEPTH = 50

interface Snapshot {
  label: string
  config: Config
}

let past: Snapshot[] = []
let future: Snapshot[] = []
let syncFn: (() => void) | null = null
// Set while we are restoring, so the hydrate()-triggered sync doesn't push a snapshot of
// the state we just restored to.
let restoring = false

export function initHistory(sync: () => void): void {
  syncFn = sync
}

export function isRestoring(): boolean {
  return restoring
}

function snapshot(label: string): Snapshot {
  return { label, config: structuredClone(collectConfig()) }
}

/**
 * Record the state *before* a structural change, so undo can return to it.
 * Call this immediately before mutating, with a label phrased as the action
 * being taken ("remove step", "auto-map columns").
 */
export function pushSnapshot(label: string): void {
  if (restoring) return
  past.push(snapshot(label))
  if (past.length > MAX_DEPTH) past.shift()
  future = []
}

/** Discard all history — for a fresh config where the previous state is not meaningful. */
export function clearHistory(): void {
  past = []
  future = []
}

function restore(entry: Snapshot): void {
  if (!syncFn) return
  restoring = true
  try {
    hydrate(entry.config, syncFn)
  } finally {
    restoring = false
  }
}

export function undo(): void {
  const entry = past.pop()
  if (!entry) {
    showToast('Nothing to undo', 'info')
    return
  }
  future.push(snapshot(entry.label))
  restore(entry)
  showToast(`Undid: ${entry.label}`, 'info')
}

export function redo(): void {
  const entry = future.pop()
  if (!entry) {
    showToast('Nothing to redo', 'info')
    return
  }
  past.push(snapshot(entry.label))
  restore(entry)
  showToast(`Redid: ${entry.label}`, 'info')
}

/**
 * Undo one specific snapshot if it is still the most recent one — used by the "Undo"
 * button in a delete toast, which must not fire if the user has since done something else.
 */
export function undoIfTop(label: string): boolean {
  if (past.length === 0 || past[past.length - 1].label !== label) return false
  undo()
  return true
}

/**
 * Record a structural change and, optionally, offer an inline Undo.
 *
 * Call this *before* mutating — the snapshot is of the state being left behind. The toast
 * action is the light-touch alternative to a confirm dialog: at this click volume,
 * confirming every delete would be noise, but losing work silently is worse.
 */
export function mutate(label: string, opts: { undoToast?: string } = {}): void {
  pushSnapshot(label)
  if (opts.undoToast) {
    showToast(opts.undoToast, 'info', {
      action: { label: 'Undo', onClick: () => undoIfTop(label) },
    })
  }
}

export function wireHistoryShortcuts(): void {
  document.addEventListener('keydown', e => {
    if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'z') return

    // The expression drawer is a real text editor with its own undo semantics; likewise any
    // free-text field the user is mid-edit in. Leave native undo alone there.
    const t = e.target as HTMLElement | null
    if (t && (t.tagName === 'TEXTAREA' || t.isContentEditable)) return

    e.preventDefault()
    if (e.shiftKey) redo()
    else undo()
  })
}

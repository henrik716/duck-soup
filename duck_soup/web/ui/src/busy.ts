import { qs } from './dom'

// Preview feedback. The preview request is the highest-frequency operation in the app and
// previously gave no signal at all — on a local GeoPackage that's merely unpolished, but
// `wfs` / `arcgis_rest` sources are re-downloaded in full by the backend on *every*
// preview, so those requests can run for minutes looking exactly like a hang.
//
// Two-stage on purpose: the progress bar appears immediately (cheap, non-destructive),
// while dimming the stale table waits out a short grace period so the common fast local
// preview doesn't flash the content.

const DIM_DELAY_MS = 150

let depth = 0
let dimTimer = 0
// Separate from `depth` on purpose: a run and a preview can overlap (a debounced preview
// firing while a run is in flight, or vice versa), and resetPreviewBusy()'s unconditional
// zeroing of `depth` on config load/new/import must not also hide a still-running run's
// indicator — nor should a run finishing hide a preview that's still loading.
let runActive = false

// What the preview and the run say about how far they are, shown in full in a chip at the
// top of the preview panel (the header's status pill has no room for it), with the bar filled to `pct`
// when there is one. The preview's wins while both are going: it's what the user is
// looking at.
interface BusyProgress { text: string; pct: number | null }
let previewProgress: BusyProgress | null = null
let runProgress: BusyProgress | null = null

function bar(): HTMLElement | null {
  return qs<HTMLElement>('#preview-progress')
}

function renderProgress(): void {
  const p = previewProgress ?? runProgress
  const label = qs<HTMLElement>('#busyLabel')
  if (label) {
    label.textContent = p?.text ?? ''
    label.hidden = !p
  }
  const el = bar()
  if (!el) return
  const known = p?.pct != null
  el.classList.toggle('known', known)
  if (known) el.style.setProperty('--pct', `${Math.min(100, Math.max(0, p!.pct!))}%`)
}

/** The preview's progress (`text` null: nothing to say). Cleared when the preview is done. */
export function setPreviewProgress(text: string | null, pct: number | null = null): void {
  previewProgress = text ? { text, pct } : null
  renderProgress()
}

/** The run's progress, like setPreviewProgress. Cleared when the run is done. */
export function setRunProgress(text: string | null, pct: number | null = null): void {
  runProgress = text ? { text, pct } : null
  renderProgress()
}

function applyDim(on: boolean): void {
  const container = qs<HTMLElement>('#preview-table-container')
  container?.classList.toggle('is-stale', on)
}

export function setPreviewBusy(busy: boolean): void {
  // Reference-counted: a step preview and a debounced output preview can overlap, and the
  // earlier one finishing must not clear the later one's indicator.
  depth = Math.max(0, depth + (busy ? 1 : -1))
  const active = depth > 0

  bar()?.classList.toggle('active', active || runActive)

  clearTimeout(dimTimer)
  if (active) {
    dimTimer = window.setTimeout(() => applyDim(true), DIM_DELAY_MS)
  } else {
    applyDim(false)
    setPreviewProgress(null)
  }
}

/** Drop any outstanding busy state — used when resetting the editor wholesale. */
export function resetPreviewBusy(): void {
  depth = 0
  clearTimeout(dimTimer)
  bar()?.classList.toggle('active', runActive)
  applyDim(false)
  setPreviewProgress(null)
}

/** A run's own busy state — shares the same progress bar as preview loading, but tracked
 *  independently so the two can't clobber each other's indicator when they overlap. */
export function setRunBusy(busy: boolean): void {
  runActive = busy
  bar()?.classList.toggle('active', depth > 0 || runActive)
  if (!busy) setRunProgress(null)
}

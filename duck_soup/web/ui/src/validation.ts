import { newClientId, previewConfig } from './api'
import type { Config } from './types'

export type ValidationState = 'idle' | 'pending' | 'ok' | 'bad'

export interface LiveValidationHandle {
  /** Debounced check — call on every keystroke. */
  schedule: () => void
  /** Cancels any pending debounce and checks immediately — call on blur / open. */
  runNow: () => void
  dispose: () => void
}

export interface LiveValidationOptions {
  getValue: () => string
  /** Build a throwaway single-column preview Config for the current draft value, or null
   *  if there isn't enough context yet (e.g. no base source picked). */
  buildPreviewConfig: (draft: string) => Config | null
  render: (state: ValidationState, message?: string) => void
  /** Column preview rows are keyed under; defaults to '_check'. */
  previewCol?: string
  /** Format the sample values shown on success; defaults to joining stringified values. */
  extractSample?: (rows: Record<string, unknown>[], previewCol: string) => string
  debounceMs?: number
}

/**
 * Generalizes the "does this parse against real sample data" check originally built for the
 * expression drawer: debounced, race-guarded (a superseded edit's response is dropped), and
 * backed by /api/preview against a throwaway Config rather than a dedicated endpoint.
 */
export function attachLiveValidation(opts: LiveValidationOptions): LiveValidationHandle {
  const { getValue, buildPreviewConfig, render, debounceMs = 500 } = opts
  const previewCol = opts.previewCol ?? '_check'
  let timer: ReturnType<typeof setTimeout> | undefined
  let seq = 0
  let disposed = false
  // This check's own client id: a newer check replaces an older one on the server, without
  // touching the main preview (or another field's check).
  const client = newClientId()

  const run = async () => {
    const draft = getValue().trim()
    if (!draft) { render('idle'); return }
    const cfg = buildPreviewConfig(draft)
    if (!cfg) { render('idle'); return }
    const mySeq = ++seq
    // A check runs the pipeline's steps on a few rows: on a slow pipeline that's seconds,
    // so count them rather than leave a "Checking…" that looks stuck.
    const started = Date.now()
    const pending = () => {
      const secs = Math.floor((Date.now() - started) / 1000)
      render('pending', secs < 2 ? 'Checking…' : `Checking against sample data… ${secs}s (runs the pipeline's steps)`)
    }
    pending()
    const ticker = setInterval(() => { if (disposed || mySeq !== seq) clearInterval(ticker); else pending() }, 1000)
    try {
      const d = await previewConfig(cfg, 0, 5, undefined, undefined, false, client)
      if (disposed || mySeq !== seq || d.stale) return
      if (d.ok && d.rows) {
        const sample = opts.extractSample
          ? opts.extractSample(d.rows as Record<string, unknown>[], previewCol)
          : d.rows.map(r => JSON.stringify((r as Record<string, unknown>)[previewCol])).join(', ')
        render('ok', sample ? `✓ valid — sample: ${sample}` : '✓ valid')
      } else {
        // A mapping error names the throwaway column the check maps into and its config path
        // (see MappingError in engine.py): only the error itself means anything here.
        render('bad', (d.error || 'invalid').split('\n')[0].replace(/^mapping '[^']*' \(\w+\): /, ''))
      }
    } catch {
      if (disposed || mySeq !== seq) return
      render('bad', 'validation request failed')
    } finally {
      clearInterval(ticker)
    }
  }

  return {
    schedule: () => {
      if (timer) clearTimeout(timer)
      timer = setTimeout(run, debounceMs)
    },
    runNow: () => {
      if (timer) clearTimeout(timer)
      run()
    },
    dispose: () => {
      disposed = true
      if (timer) clearTimeout(timer)
    },
  }
}

/** Creates the small inline ✓/✗ message element, styled like the expression builder's. */
export function mkValidationMsg(): HTMLElement {
  const el = document.createElement('div')
  el.className = 'expr-validation-msg'
  return el
}

export function renderValidationMsg(el: HTMLElement, state: ValidationState, message?: string): void {
  el.textContent = state === 'idle' ? '' : (message || '')
  el.className = state === 'idle' ? 'expr-validation-msg' : `expr-validation-msg ${state}`
}

/**
 * Zero-network alternative for fields that are really just "is this one of a known set of
 * names" (a column, a source id) — flags the input instead of round-tripping to /api/preview.
 * An empty valid-set means the set isn't known yet (e.g. schema not inspected) and is treated
 * as "can't say", not "invalid", to avoid false positives before data loads.
 */
export function attachMembershipCheck(
  input: HTMLInputElement,
  getValidNames: () => Iterable<string>,
): { check: () => void } {
  const check = () => {
    const v = input.value.trim()
    if (!v) { input.classList.remove('field-invalid'); input.title = ''; return }
    const valid = new Set(getValidNames())
    if (valid.size === 0 || valid.has(v)) {
      input.classList.remove('field-invalid')
      input.removeAttribute('data-invalid-title')
    } else {
      input.classList.add('field-invalid')
      input.title = `"${v}" is not among the known columns`
    }
  }
  input.addEventListener('input', check)
  input.addEventListener('blur', check)
  input.addEventListener('change', check)
  return { check }
}

const CRS_RE = /^EPSG:\d+$/

/** Cheap client-side format check for CRS fields — catches the common typo classes
 *  (missing "EPSG:" prefix, stray characters) without needing a full EPSG code list. */
export function attachCrsFormatCheck(input: HTMLInputElement): { check: () => void } {
  const check = () => {
    const v = input.value.trim()
    if (!v || CRS_RE.test(v)) {
      input.classList.remove('field-invalid')
      input.title = ''
    } else {
      input.classList.add('field-invalid')
      input.title = `Expected the form "EPSG:<code>", e.g. EPSG:25833`
    }
  }
  input.addEventListener('input', check)
  input.addEventListener('blur', check)
  return { check }
}

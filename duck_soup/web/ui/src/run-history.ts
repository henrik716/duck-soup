// The History tab: a config's past runs (GET /api/runs/<name>, history.py) — editor runs and
// scheduled `duck-soup run`s alike — so "did last night's run work, and how many rows did it
// write?" has an answer in the editor. A run's log is only fetched once it's expanded.

import { fetchRun, fetchRuns } from './api'
import { esc } from './dom'
import type { RunRecord } from './types'

const fmtRows = (n: number) => n.toLocaleString()

function when(iso: string): string {
  const d = new Date(iso)
  return isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function duration(s: number): string {
  if (s < 60) return `${s.toFixed(1)} s`
  const m = Math.floor(s / 60)
  return `${m} min ${Math.round(s - m * 60)} s`
}

function summary(run: RunRecord): string {
  if (!run.ok) return esc((run.error ?? 'failed').split('\n')[0])
  const layers = run.layers.filter(l => l.kind === 'layer')
  const rejects = run.layers.filter(l => l.kind === 'rejects')
  const rows = layers.reduce((n, l) => n + l.rows, 0)
  let text = `${layers.length} layer${layers.length !== 1 ? 's' : ''} · ${fmtRows(rows)} rows`
  const rejected = rejects.reduce((n, l) => n + l.rows, 0)
  if (rejects.length) text += ` · ${fmtRows(rejected)} rejected`
  return text
}

function details(run: RunRecord): string {
  const layerRows = run.layers.map(l =>
    `<tr class="${l.kind === 'rejects' ? 'is-rejects' : ''}"><td>${esc(l.pipeline)}</td><td>${esc(l.layer)}${l.kind === 'rejects' ? ' <span class="run-tag">rejects</span>' : ''}</td><td class="num">${fmtRows(l.rows)}</td></tr>`,
  ).join('')
  return `
    ${run.error ? `<pre class="run-error">${esc(run.error)}</pre>` : ''}
    ${layerRows ? `<table class="run-layers"><thead><tr><th>pipeline</th><th>layer</th><th class="num">rows</th></tr></thead><tbody>${layerRows}</tbody></table>` : ''}
    <div class="run-meta">
      ${run.output ? `<span>output <code>${esc(run.output)}</code></span>` : ''}
      ${run.config_hash ? `<span title="Fingerprint of the config that ran: runs of the same config version share it">config <code>${esc(run.config_hash)}</code></span>` : ''}
    </div>
    <div class="run-log" data-run-log><span class="log-line">loading log…</span></div>`
}

export async function renderRunHistory(container: HTMLElement, name: string): Promise<void> {
  if (!name) {
    container.innerHTML = '<p class="hint">Save the config to keep its run history. Runs from the editor and from <code>duck-soup run</code> (scheduled runs too) are recorded under <code>runs/&lt;config name&gt;/</code> in the project folder.</p>'
    return
  }
  let runs: RunRecord[]
  try {
    runs = await fetchRuns(name)
  } catch {
    container.innerHTML = '<span class="log-line bad">Couldn\'t load the run history.</span>'
    return
  }
  if (runs.length === 0) {
    container.innerHTML = `<p class="hint">No runs of <strong>${esc(name)}</strong> yet. Runs from the editor and from <code>duck-soup run pipelines/${esc(name)}.yaml</code> (scheduled runs too) are recorded here.</p>`
    return
  }

  container.innerHTML = runs.map(run => `
    <details class="run-entry ${run.ok ? 'ok' : 'bad'}" data-run-id="${esc(run.id)}">
      <summary>
        <span class="run-dot" aria-label="${run.ok ? 'succeeded' : 'failed'}"></span>
        <span class="run-when">${esc(when(run.started_at))}</span>
        <span class="run-tag">${run.trigger === 'cli' ? 'cli' : 'editor'}</span>
        <span class="run-summary">${summary(run)}</span>
        <span class="run-duration">${duration(run.duration_s)}</span>
      </summary>
      <div class="run-body">${details(run)}</div>
    </details>`).join('')

  container.querySelectorAll<HTMLDetailsElement>('details.run-entry').forEach(d => {
    d.addEventListener('toggle', async () => {
      const logEl = d.querySelector<HTMLElement>('[data-run-log]')
      if (!d.open || !logEl || logEl.dataset['loaded']) return
      logEl.dataset['loaded'] = '1'
      const full = await fetchRun(name, d.dataset['runId'] ?? '')
      const lines = full?.log ?? []
      logEl.innerHTML = lines.length
        ? lines.map(l => `<div class="log-line">${esc(l)}</div>`).join('')
        : '<span class="log-line">no log</span>'
    })
  })
}

// The SQL tab: every view the engine builds for a pipeline (POST /api/plan, Engine.sql_plan
// in engine.py), in order, so what a step actually does is never a black box. Fetching the
// plan reads no data, so it's refreshed after every successful validation.

import { esc } from './dom'
import { highlightExpr } from './expr-highlight'
import type { PlanEntry } from './types'

const SQL_KEYWORDS = new Set([
  'select', 'from', 'where', 'join', 'left', 'inner', 'on', 'group', 'by', 'order', 'with',
  'materialized', 'union', 'all', 'exclude', 'replace', 'limit', 'lateral', 'over', 'having',
  'create', 'view', 'table', 'temp', 'or', 'columns', 'lambda',
])

const KIND_LABEL: Record<PlanEntry['kind'], string> = {
  source: 'source', derived: 'derived', base: 'base', step: 'step', split: 'split',
  rejects: 'rejects', branch: 'branch', layer: 'layer',
}

// Long and rarely the point, so they start closed; steps, rejects and layers start open.
const START_CLOSED = new Set<PlanEntry['kind']>(['source', 'derived', 'base', 'split', 'branch'])

let lastPlan: PlanEntry[] = []
// Which entries the user opened or closed, so a refresh (every edit) doesn't undo that.
const toggled = new Map<string, boolean>()

const keyOf = (e: PlanEntry) => `${e.kind}:${e.step ?? ''}:${e.id ?? e.layer ?? e.view ?? ''}`

export function renderSqlPlan(container: HTMLElement, plan: PlanEntry[]): void {
  lastPlan = plan
  if (plan.length === 0) {
    container.innerHTML = '<span class="log-line">—</span>'
    return
  }
  container.innerHTML = plan.map(e => {
    const key = keyOf(e)
    const open = toggled.get(key) ?? !START_CLOSED.has(e.kind)
    const title = e.title ?? e.view ?? e.kind
    return `<details class="sql-entry kind-${e.kind}" data-key="${esc(key)}"${e.step ? ` data-step="${e.step}"` : ''}${open ? ' open' : ''}>
      <summary><span class="sql-kind">${KIND_LABEL[e.kind]}</span><span class="sql-title">${esc(title)}</span>${e.view ? `<span class="sql-view">${esc(e.view)}</span>` : ''}</summary>
      <pre class="sql-code">${highlightExpr(e.sql, SQL_KEYWORDS)}</pre>
    </details>`
  }).join('')
  container.querySelectorAll<HTMLDetailsElement>('details.sql-entry').forEach(d => {
    d.addEventListener('toggle', () => toggled.set(d.dataset['key'] ?? '', d.open))
  })
}

export function renderSqlPlanError(container: HTMLElement, error: string): void {
  lastPlan = []
  container.innerHTML = `<span class="log-line bad">${esc(error)}</span>`
}

/** The whole plan as one SQL script, each view as the CREATE statement the engine runs. */
export function sqlPlanText(): string {
  return lastPlan.map(e => {
    const head = `-- ${e.title ?? e.kind}`
    return e.view ? `${head}\nCREATE OR REPLACE TEMP VIEW "${e.view}" AS\n${e.sql};` : `${head}\n${e.sql};`
  }).join('\n\n')
}

/** Opens and scrolls to step `step`'s entries (1-based), its rejects included. */
export function revealPlanStep(container: HTMLElement, step: number): void {
  const entries = [...container.querySelectorAll<HTMLDetailsElement>(`details.sql-entry[data-step="${step}"]`)]
    .filter(d => !d.classList.contains('kind-branch'))
  if (entries.length === 0) return
  entries.forEach(d => { d.open = true })
  entries[0].scrollIntoView({ behavior: 'smooth', block: 'start' })
  entries.forEach(d => {
    d.classList.add('highlight-flash')
    setTimeout(() => d.classList.remove('highlight-flash'), 1500)
  })
}

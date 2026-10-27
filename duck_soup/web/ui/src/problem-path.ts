// ---- problems: config paths ----
// Pydantic flattens its ValidationError to a string before it reaches us, but the loc
// paths survive in the text ("pipelines.0.steps.3.source" on its own line, followed by an
// indented message); the engine's own errors (MappingError, the CRS check) are written in
// the same shape. Recovering them lets a problem link back to the card that caused it.
const LOC_RE = /^([A-Za-z_][\w]*(?:\.[\w]+)+)$/

export interface Problem { path: string; msg: string }

export function parseProblems(detail: string): Problem[] {
  const lines = detail.split('\n')
  const out: Problem[] = []
  lines.forEach((line, i) => {
    const m = line.trim().match(LOC_RE)
    if (!m) return
    const next = (lines[i + 1] ?? '').trim()
    // Skip the trailing "For further information visit ..." noise.
    const msg = next.startsWith('For further information') ? '' : next.replace(/\s*\[type=.*$/, '')
    out.push({ path: m[1], msg })
  })
  return out
}

/** What one item of each list in the config is called, numbered from 1 in the editor. */
const ITEM_NAMES: Record<string, string> = {
  pipelines: 'pipeline',
  sources: 'source',
  derived_sources: 'derived source',
  steps: 'step',
  mapping: 'mapping row',
  layers: 'output layer',
  aggregates: 'aggregate',
  columns: 'column',
  by: 'group key',
  cases: 'case',
}

/** A config path in the editor's words: `pipelines.0.steps.7.aggregates.0` → "step 8 ·
 *  aggregate 1". The pipeline is named only when `withPipeline` (there's more than one). */
export function describeProblemPath(path: string, withPipeline = false): string {
  const parts = path.split('.')
  const out: string[] = []
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i]
    const next = parts[i + 1]
    const item = ITEM_NAMES[part]
    if (item && next !== undefined && /^\d+$/.test(next)) {
      if (part !== 'pipelines' || withPipeline) out.push(`${item} ${Number(next) + 1}`)
      i++
    } else if (part === 'fields' && next !== undefined) {
      out.push(`field '${next}'`)
      i++
    } else {
      out.push(part === 'working_crs' ? 'working CRS' : part.replace(/_/g, ' '))
    }
  }
  return out.join(' · ')
}

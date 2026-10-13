// Helpers for the http_lookup step card (cards/http-lookup.ts), free of the DOM.

/** The `{column}` placeholders of a URL template, in order (`{{`/`}}` are literal braces),
 *  like config.url_template_columns. */
export function templateColumns(url: string): string[] {
  const out: string[] = []
  for (const m of url.matchAll(/\{\{|\}\}|\{([^{}]*)\}/g)) {
    if (m[1] !== undefined) out.push(m[1].trim())
  }
  return out
}

/** Follow a dot path (`results.0`, a number being a list index) into a parsed JSON value. */
export function atPath(value: unknown, path: string): unknown {
  let cur = value
  for (const seg of path.split('.').map(s => s.trim()).filter(Boolean)) {
    if (cur === null || typeof cur !== 'object') return undefined
    cur = Array.isArray(cur) && /^\d+$/.test(seg) ? cur[Number(seg)] : (cur as Record<string, unknown>)[seg]
  }
  return cur
}

export interface LeafPath { path: string; sample: string }

/** Every leaf (a value that isn't an object or a list) inside `value`, as dot paths with a
 *  sample of the value, depth first; a list contributes its first item (`.0`). At most `max`. */
export function leafPaths(value: unknown, max = 80): LeafPath[] {
  const out: LeafPath[] = []
  const walk = (v: unknown, path: string) => {
    if (out.length >= max) return
    if (Array.isArray(v)) {
      if (v.length) walk(v[0], path ? `${path}.0` : '0')
      return
    }
    if (v !== null && typeof v === 'object') {
      for (const [k, child] of Object.entries(v)) walk(child, path ? `${path}.${k}` : k)
      return
    }
    if (path) out.push({ path, sample: v === null ? 'null' : String(v) })
  }
  walk(value, '')
  return out
}

/** The first list of objects inside `value` (depth first), the likely `records`: a search
 *  API's hits. Null when the response is no such thing. */
export function guessRecords(value: unknown, path = ''): string | null {
  if (Array.isArray(value)) return value.length && typeof value[0] === 'object' && value[0] !== null ? path : null
  if (value !== null && typeof value === 'object') {
    for (const [k, child] of Object.entries(value)) {
      const found = guessRecords(child, path ? `${path}.${k}` : k)
      if (found !== null) return found
    }
  }
  return null
}

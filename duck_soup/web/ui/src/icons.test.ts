import { describe, expect, it } from 'vitest'
import { ICONS, iconKey } from './icons'

// Every source file and the page itself, as text.
const sources: Record<string, string> = {
  ...import.meta.glob<string>(['./**/*.ts', '!./**/*.test.ts'], { query: '?raw', import: 'default', eager: true }),
  ...import.meta.glob<string>('../index.html', { query: '?raw', import: 'default', eager: true }),
}

const all = (re: RegExp, text: string) => [...text.matchAll(re)].map(m => m[1])

// The icon names the editor can put in a data-lucide attribute.
function usedIconNames(): Map<string, string> {
  const used = new Map<string, string>()
  const add = (name: string, where: string) => { if (!used.has(name)) used.set(name, where) }
  for (const [file, text] of Object.entries(sources)) {
    all(/data-lucide="([a-z0-9-]+)"/g, text).forEach(n => add(n, file))
    // Icons picked at run time: the step icon maps, getSourceIcon's `icon: '…'`.
    for (const block of all(/const STEP_ICONS?\b[^=]*=\s*\{([^}]*)\}/g, text)) {
      all(/:\s*'([a-z0-9-]+)'/g, block).forEach(n => add(n, file))
    }
    all(/\bicon:\s*'([a-z0-9-]+)'/g, text).forEach(n => add(n, file))
  }
  // The rest are literals passed along: lineage's makeNode arguments and layer node, the
  // toast's type icons.
  for (const n of ['play', 'link', 'corner-down-right', 'layers', 'folder', 'package',
    'check-circle', 'alert-triangle', 'info']) add(n, 'literal')
  return used
}

describe('icons', () => {
  it('maps names the way lucide does', () => {
    expect(iconKey('trash-2')).toBe('Trash2')
    expect(iconKey('alert-circle')).toBe('AlertCircle')
    expect(iconKey('database-zap')).toBe('DatabaseZap')
  })

  it('has every icon the markup uses', () => {
    const used = usedIconNames()
    expect(used.size).toBeGreaterThan(50) // the scan found the markup at all
    const missing = [...used].filter(([n]) => !ICONS[iconKey(n)]).map(([n, f]) => `${n} (${f})`)
    expect(missing).toEqual([])
  })
})

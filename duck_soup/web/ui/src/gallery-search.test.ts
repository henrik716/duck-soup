import { describe, expect, it } from 'vitest'
import { matchesQuery, normalize } from './gallery-search'
import { STEP_GALLERY } from './step-gallery'

/** The step types the gallery shows for `query`, in gallery order. */
const found = (query: string): string[] =>
  STEP_GALLERY.flatMap(g => g.items.filter(it => matchesQuery(it, query, `${g.name} ${g.what}`)).map(it => it.type))

describe('normalize', () => {
  it('lower-cases, drops accents and turns punctuation into single spaces', () => {
    expect(normalize('  Erase / Difference ')).toBe('erase difference')
    expect(normalize('Généralisér_v.clean')).toBe('generaliser v clean')
  })
})

describe('matchesQuery', () => {
  it('matches everything for an empty query', () => {
    expect(found('').length).toBe(STEP_GALLERY.flatMap(g => g.items).length)
    expect(found('   ').length).toBe(found('').length)
  })

  it('finds a step by its name, type or description', () => {
    expect(found('simplify')).toContain('simplify')
    expect(found('remove_duplicates')).toEqual(['remove_duplicates'])
    expect(found('centimetres')).toEqual(['snap_to_grid'])
  })

  it('finds a step by an FME transformer or QGIS tool name, in any case', () => {
    expect(found('Deaggregator')).toEqual(['explode'])
    expect(found('linecombiner')).toEqual(['combine_lines'])
    expect(found('donuthole')).toEqual(['fill_holes'])
    expect(found('HTTPCaller')).toEqual(['http_lookup'])
    expect(found('delete duplicate geometries')).toEqual(['remove_duplicates'])
  })

  it('needs every word, each one anywhere', () => {
    expect(found('snap grid')).toEqual(['snap_to_grid'])
    expect(found('remove dup')).toEqual(['remove_duplicates'])
    expect(found('grid snap')).toEqual(['snap_to_grid'])
  })

  it('finds words written together', () => {
    expect(found('snapto')).toEqual(['snap_to_grid'])
    expect(found('multipart')).toContain('explode')
  })

  it('matches the group too', () => {
    expect(found('paddling')).toEqual(STEP_GALLERY[0]!.items.map(it => it.type))
  })

  it('finds nothing for a word no card has', () => {
    expect(found('zebra crossing')).toEqual([])
  })
})

describe('STEP_GALLERY', () => {
  it('has each step type once', () => {
    const types = STEP_GALLERY.flatMap(g => g.items.map(it => it.type))
    expect(new Set(types).size).toBe(types.length)
  })
})

import { describe, expect, it } from 'vitest'
import { atPath, guessRecords, leafPaths, templateColumns } from './http-paths'

const RESPONSE = {
  metadata: { totaltAntallTreff: 1 },
  adresser: [
    { adressetekst: 'Karl Johans gate 1', postnummer: '0154', representasjonspunkt: { lat: 59.9, lon: 10.7 }, bruksenheter: ['H0101'] },
    { adressetekst: 'other' },
  ],
}

describe('templateColumns', () => {
  it('lists the placeholders, not literal braces', () => {
    expect(templateColumns('https://x/sok?sok={ adresse }&n={{1}}&k={kommune}')).toEqual(['adresse', 'kommune'])
  })
})

describe('leafPaths', () => {
  it('lists leaves as dot paths with the first item of a list', () => {
    expect(leafPaths(RESPONSE.adresser[0])).toEqual([
      { path: 'adressetekst', sample: 'Karl Johans gate 1' },
      { path: 'postnummer', sample: '0154' },
      { path: 'representasjonspunkt.lat', sample: '59.9' },
      { path: 'representasjonspunkt.lon', sample: '10.7' },
      { path: 'bruksenheter.0', sample: 'H0101' },
    ])
  })

  it('stops at max', () => {
    expect(leafPaths(RESPONSE, 2)).toHaveLength(2)
  })
})

describe('guessRecords / atPath', () => {
  it('finds the list of hits and follows paths into it', () => {
    expect(guessRecords(RESPONSE)).toBe('adresser')
    expect(atPath(RESPONSE, 'adresser.0.representasjonspunkt.lat')).toBe(59.9)
    expect(guessRecords({ a: 1 })).toBeNull()
  })
})

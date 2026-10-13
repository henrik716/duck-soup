import { describe, expect, it } from 'vitest'
import { buildCondition, parseCondition, type Clause, type Condition } from './conditions'

const and = (...clauses: Clause[]): Condition => ({ join: 'and', clauses })
const c = (column: string, op: Clause['op'], value = ''): Clause => ({ column, op, value })

describe('buildCondition', () => {
  it('writes each operator', () => {
    expect(buildCondition(and(
      c('speed', 'gt', '50'), c('name', 'eq', "O'Hare"), c('LOK.GATENAVN', 'contains', 'gate'),
      c('kind', 'not_empty'), c('code', 'in', 'A, B'),
    ))).toBe(`"speed" > 50 AND "name" = 'O''Hare' AND "LOK.GATENAVN" ILIKE '%gate%' AND "kind" IS NOT NULL AND "code" IN ('A', 'B')`)
  })

  it('quotes a number for a text column', () => {
    expect(buildCondition(and(c('kommune', 'eq', '0301')), new Set(['kommune']))).toBe(`"kommune" = '0301'`)
  })

  it('leaves out rows still being filled in, and joins with OR', () => {
    expect(buildCondition({ join: 'or', clauses: [c('a', 'eq', '1'), c('b', 'eq'), c('', 'empty'), c('c', 'empty')] }))
      .toBe(`"a" = 1 OR "c" IS NULL`)
  })
})

describe('parseCondition', () => {
  it('reads back what buildCondition writes', () => {
    const cond = and(
      c('speed', 'ge', '50'), c('name', 'ne', "O'Hare"), c('LOK.GATENAVN', 'not_contains', 'gate'),
      c('x', 'starts', 'Ab'), c('kind', 'empty'), c('code', 'not_in', 'A, B, 3'), c('n', 'le', '-2.5'),
    )
    expect(parseCondition(buildCondition(cond))).toEqual(cond)
    const or: Condition = { join: 'or', clauses: [c('a', 'lt', '1'), c('b', 'in', 'x')] }
    expect(parseCondition(buildCondition(or))).toEqual(or)
  })

  it('reads hand-written SQL in the same shape: bare names, any case, != ', () => {
    expect(parseCondition(`pond is not null and bread_crumbs >= 1000 AND kind != 'x'`)).toEqual(and(
      c('pond', 'not_empty'), c('bread_crumbs', 'ge', '1000'), c('kind', 'ne', 'x'),
    ))
  })

  it('keeps AND and OR inside strings and brackets out of the split', () => {
    expect(parseCondition(`name = 'Salt and Pepper' AND code IN ('A', 'B')`)).toEqual(and(
      c('name', 'eq', 'Salt and Pepper'), c('code', 'in', 'A, B'),
    ))
  })

  it('is empty for no SQL, and null for anything it cannot show', () => {
    expect(parseCondition('  ')).toEqual(and())
    expect(parseCondition('a = 1 AND b = 2 OR c = 3')).toBeNull()
    expect(parseCondition('upper(name) = 1')).toBeNull()
    expect(parseCondition(`name ILIKE '%a%b%'`)).toBeNull()
    expect(parseCondition('a = b')).toBeNull()
  })
})

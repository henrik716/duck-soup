import { describe, expect, it } from 'vitest'
import { exprMentions, howFits, howLabel, howSql, parseHowSql, quoteIdent } from './aggregates'

// As /api/meta sends them (steps.AGGREGATE_SQL).
const TEMPLATES: Record<string, string> = {
  any: 'any_value({col})',
  most_common: 'mode({col})',
  from_largest: 'arg_max({col}, greatest(ST_Area(geom), ST_Length(geom)))',
  all: "string_agg(DISTINCT CAST({col} AS VARCHAR), ', ' ORDER BY CAST({col} AS VARCHAR))",
  sum: 'sum({col})',
  average: 'avg({col})',
  min: 'min({col})',
  max: 'max({col})',
  count: 'count({col})',
}

describe('howSql / parseHowSql', () => {
  it('quotes the column, doubling any quote in its name', () => {
    expect(quoteIdent('NET.TYPEVEG')).toBe('"NET.TYPEVEG"')
    expect(howSql('most_common', 'say "hi"', TEMPLATES)).toBe('mode("say ""hi""")')
  })

  it('counts everything without a column', () => {
    expect(howSql('count', '', TEMPLATES)).toBe('count(*)')
    expect(parseHowSql('count(*)', TEMPLATES)).toEqual({ how: 'count', from: '' })
  })

  it('reads back what it writes, for every how', () => {
    for (const how of Object.keys(TEMPLATES)) {
      const sql = howSql(how as never, 'LOK.GATENAVN', TEMPLATES)
      expect(parseHowSql(sql, TEMPLATES)).toEqual({ how, from: 'LOK.GATENAVN' })
    }
  })

  it('takes an unquoted column, any case and surrounding space', () => {
    expect(parseHowSql('  SUM(population) ', TEMPLATES)).toEqual({ how: 'sum', from: 'population' })
  })

  it('needs the same column wherever a template repeats it', () => {
    expect(parseHowSql(`string_agg(DISTINCT CAST(a AS VARCHAR), ', ' ORDER BY CAST(b AS VARCHAR))`, TEMPLATES)).toBeNull()
  })

  it("is null for SQL that isn't one of the templates", () => {
    expect(parseHowSql('sum(population) / 2', TEMPLATES)).toBeNull()
    expect(parseHowSql('any_value(*)', TEMPLATES)).toBeNull()
  })
})

describe('howFits', () => {
  const numeric = ['sum', 'average']
  it('keeps sum and average for numbers', () => {
    expect(howFits('sum', 'VARCHAR', numeric)).toBe(false)
    expect(howFits('average', 'DECIMAL(10,2)', numeric)).toBe(true)
    expect(howFits('sum', 'BIGINT', numeric)).toBe(true)
  })
  it('lets anything else, and an unknown type, through', () => {
    expect(howFits('most_common', 'VARCHAR', numeric)).toBe(true)
    expect(howFits('sum', '', numeric)).toBe(true)
  })
})

describe('exprMentions', () => {
  it('finds a quoted column, or a bare one as a whole word in any case', () => {
    expect(exprMentions('mode("NET.TYPEVEG")', 'NET.TYPEVEG')).toBe(true)
    expect(exprMentions('sum(Population) / 2', 'population')).toBe(true)
    expect(exprMentions('sum(population_2020)', 'population')).toBe(false)
    expect(exprMentions('count(*)', 'population')).toBe(false)
  })
  it("doesn't count a column named in a string", () => {
    expect(exprMentions("string_agg(name, 'material')", 'material')).toBe(false)
  })
})

it('labels a how it has no words for by its name', () => {
  expect(howLabel('min')).toBe('lowest')
  expect(howLabel('some_new_how')).toBe('some new how')
})

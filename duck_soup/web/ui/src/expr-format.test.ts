import { describe, expect, it } from 'vitest'
import { formatExpr } from './expr-format'

const fmt = (src: string): string => {
  const res = formatExpr(src)
  if ('error' in res) throw new Error(`unexpected error: ${res.error}`)
  return res.text
}

const errorOf = (src: string): string | undefined => {
  const res = formatExpr(src)
  return 'error' in res ? res.error : undefined
}

describe('formatExpr: one line', () => {
  it('uppercases keywords and leaves functions, identifiers and strings as written', () => {
    expect(fmt(`coalesce(Name, 'none') is not null and x like '%case when%'`))
      .toBe(`coalesce(Name, 'none') IS NOT NULL AND x LIKE '%case when%'`)
  })

  it('collapses whitespace and line breaks', () => {
    expect(fmt('  a   +\n\t b  ')).toBe('a + b')
  })

  it('puts one space after each comma and none before', () => {
    expect(fmt('round(x,2)')).toBe('round(x, 2)')
    expect(fmt('round(x , 2)')).toBe('round(x, 2)')
  })

  it('keeps operator spacing as written', () => {
    expect(fmt('a+b')).toBe('a+b')
    expect(fmt('x::int')).toBe('x::int')
    expect(fmt('t.col')).toBe('t.col')
  })

  it('keeps a keyword call like CAST(...) attached to its parentheses', () => {
    expect(fmt('cast(x as varchar)')).toBe('CAST(x AS varchar)')
  })

  it('leaves quoted identifiers and escaped quotes alone', () => {
    expect(fmt(`"Case" || 'it''s'`)).toBe(`"Case" || 'it''s'`)
  })

  it('does not treat -- or /* inside a string as a comment', () => {
    expect(fmt(`a || ' -- /* x */ '`)).toBe(`a || ' -- /* x */ '`)
  })

  it('keeps a CASE with a single WHEN on one line when it fits', () => {
    expect(fmt(`case when a = 1 then 'x' else 'y' end`)).toBe(`CASE WHEN a = 1 THEN 'x' ELSE 'y' END`)
  })

  it('formats an empty expression as empty', () => {
    expect(fmt('')).toBe('')
    expect(fmt('   ')).toBe('')
  })
})

describe('formatExpr: breaking lines', () => {
  it('always breaks a CASE with two or more WHENs', () => {
    expect(fmt(`case when a = 1 then 'one' when a = 2 then 'two' else 'many' end`)).toBe([
      'CASE',
      `  WHEN a = 1 THEN 'one'`,
      `  WHEN a = 2 THEN 'two'`,
      `  ELSE 'many'`,
      'END',
    ].join('\n'))
  })

  it('keeps a simple CASE operand on the CASE line', () => {
    expect(fmt(`case kind when 'a' then 1 when 'b' then 2 end`)).toBe([
      'CASE kind',
      `  WHEN 'a' THEN 1`,
      `  WHEN 'b' THEN 2`,
      'END',
    ].join('\n'))
  })

  it('puts one argument per line when a call does not fit', () => {
    expect(fmt(`concat(first_column_name, ' - ', second_column_name, ' - ', third_column_name_here)`)).toBe([
      'concat(',
      '  first_column_name,',
      `  ' - ',`,
      '  second_column_name,',
      `  ' - ',`,
      '  third_column_name_here',
      ')',
    ].join('\n'))
  })

  it('breaks the call around a CASE that must break', () => {
    expect(fmt('coalesce(case when a then 1 when b then 2 end, 0)')).toBe([
      'coalesce(',
      '  CASE',
      '    WHEN a THEN 1',
      '    WHEN b THEN 2',
      '  END,',
      '  0',
      ')',
    ].join('\n'))
  })

  it('breaks a long line before its top-level AND / OR', () => {
    expect(fmt(`population > 1000 and category = 'school' or region_name is not null and name like 'A%'`)).toBe([
      'population > 1000',
      `AND category = 'school'`,
      'OR region_name IS NOT NULL',
      `AND name LIKE 'A%'`,
    ].join('\n'))
  })

  it('does not break at the AND of a BETWEEN', () => {
    expect(fmt(`area between 100 and 5000 and category = 'park' and region_name is not null and owner = 'municipality'`)).toBe([
      'area BETWEEN 100 AND 5000',
      `AND category = 'park'`,
      'AND region_name IS NOT NULL',
      `AND owner = 'municipality'`,
    ].join('\n'))
  })

  it('indents AND / OR breaks inside a CASE clause under that clause', () => {
    expect(fmt(`case when population > 100000 and category = 'city' and region_name is not null and is_capital then 'big' when population > 1000 then 'small' end`)).toBe([
      'CASE',
      `  WHEN population > 100000`,
      `    AND category = 'city'`,
      `    AND region_name IS NOT NULL`,
      `    AND is_capital THEN 'big'`,
      `  WHEN population > 1000 THEN 'small'`,
      'END',
    ].join('\n'))
  })

  it('keeps every line within 80 columns when the pieces allow it', () => {
    const out = fmt(`concat(upper(first_column_name), ' - ', lower(second_column_name), ' - ', coalesce(third_column_name_here, 'n/a'))`)
    for (const line of out.split('\n')) expect(line.length).toBeLessThanOrEqual(80)
  })
})

describe('formatExpr: idempotence', () => {
  const samples = [
    `coalesce(Name, 'none') is not null and x like '%case when%'`,
    `case when a = 1 then 'one' when a = 2 then 'two' else 'many' end`,
    `case kind when 'a' then 1 when 'b' then 2 end`,
    `concat(first_column_name, ' - ', second_column_name, ' - ', third_column_name_here)`,
    'coalesce(case when a then 1 when b then 2 end, 0)',
    `area between 100 and 5000 and category = 'park' and region_name is not null and owner = 'municipality'`,
    `case when population > 100000 and category = 'city' and region_name is not null and is_capital then 'big' when population > 1000 then 'small' end`,
    `list_transform([1, 2, 3], x -> x * 2)`,
    `{'a': 1, 'b': name}`,
  ]

  it.each(samples)('formatting twice changes nothing: %s', src => {
    const once = fmt(src)
    expect(fmt(once)).toBe(once)
  })
})

describe('formatExpr: refuses what it cannot format', () => {
  it('refuses comments, which moving would break', () => {
    expect(errorOf('a -- note\n+ b')).toMatch(/comments/)
    expect(errorOf('a /* note */ + b')).toMatch(/comments/)
  })

  it('refuses unbalanced brackets', () => {
    expect(errorOf('round(x, 2')).toBe('Missing ")".')
    expect(errorOf('x)')).toBe('Unmatched ")".')
    expect(errorOf('[1, 2')).toBe('Missing "]".')
  })

  it('refuses a CASE without END', () => {
    expect(errorOf('case when a then 1')).toBe('CASE without END.')
  })
})

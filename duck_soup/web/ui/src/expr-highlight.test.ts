import { describe, expect, it } from 'vitest'
import { highlightExpr } from './expr-highlight'

const kw = (s: string) => `<span class="expr-tok-keyword">${s}</span>`
const fn = (s: string) => `<span class="expr-tok-func">${s}</span>`
const str = (s: string) => `<span class="expr-tok-string">${s}</span>`
const num = (s: string) => `<span class="expr-tok-number">${s}</span>`

describe('highlightExpr', () => {
  it('wraps keywords, function calls, strings and numbers; leaves columns plain', () => {
    expect(highlightExpr(`CASE WHEN round(pop, 2) > 10 THEN 'big' END`)).toBe(
      `${kw('CASE')} ${kw('WHEN')} ${fn('round')}(pop, ${num('2')}) &gt; ${num('10')} ${kw('THEN')} ${str('&#39;big&#39;')} ${kw('END')}\n`,
    )
  })

  it('matches keywords in any case and keeps their spelling', () => {
    expect(highlightExpr('a is Not null')).toBe(`a ${kw('is')} ${kw('Not')} ${kw('null')}\n`)
  })

  it('treats a name followed by "(" as a call, even across whitespace', () => {
    expect(highlightExpr('upper (name)')).toBe(`${fn('upper')} (name)\n`)
  })

  it('prefers keyword over call for keywords followed by "("', () => {
    expect(highlightExpr('cast(x as int)')).toBe(`${kw('cast')}(x ${kw('as')} int)\n`)
  })

  it('does not highlight inside strings', () => {
    expect(highlightExpr(`'case when x'`)).toBe(`${str('&#39;case when x&#39;')}\n`)
  })

  it('escapes HTML in plain text', () => {
    expect(highlightExpr('a < b & "c"')).toBe('a &lt; b &amp; &quot;c&quot;\n')
  })

  it('ends with a newline so the overlay keeps the textarea\'s line count', () => {
    expect(highlightExpr('')).toBe('\n')
    expect(highlightExpr('a\n')).toBe('a\n\n')
  })

  it('widens the keyword set with extraKeywords', () => {
    expect(highlightExpr('select a from t', new Set(['select', 'from'])))
      .toBe(`${kw('select')} a ${kw('from')} t\n`)
  })
})

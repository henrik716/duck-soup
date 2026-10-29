// The no-SQL form of a dissolve/combine_lines aggregate (`{from, how}`): what each `how` is
// called in the editor, which columns it fits, and its SQL. The vocabulary and the SQL come
// from /api/meta (config.AGGREGATE_HOWS, steps.AGGREGATE_SQL); only the words are here.
import type { AggregateHow } from './types'

/** What the "how" dropdown says for each `how`. */
export const HOW_LABELS: Record<string, string> = {
  any: 'one value',
  most_common: 'most common',
  from_largest: 'from the largest',
  all: 'all of them',
  sum: 'sum',
  average: 'average',
  min: 'lowest',
  max: 'highest',
  count: 'count',
}

/** Each `how`'s tooltip. */
export const HOW_HINTS: Record<string, string> = {
  any: 'One of the values, picked at random: for a column with the same value throughout',
  most_common: 'The value most features have',
  from_largest: 'The value of the longest line or the largest area',
  all: 'Every different value, joined with ", "',
  sum: 'The values added up (numbers only)',
  average: 'The mean of the values (numbers only)',
  min: 'The lowest value (smallest number, earliest date, first alphabetically)',
  max: 'The highest value (largest number, latest date, last alphabetically)',
  count: 'How many features were combined',
}

export function howLabel(how: string): string {
  return HOW_LABELS[how] ?? how.replace(/_/g, ' ')
}

export const NUMERIC_TYPE = /^(TINYINT|SMALLINT|INTEGER|INT|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|UHUGEINT|FLOAT|REAL|DOUBLE|DECIMAL|NUMERIC)\b/i

/** Whether a column of DuckDB type `type` can take `how`. An unknown type ('') can take
 *  anything: the engine's check says so if it can't. */
export function howFits(how: string, type: string, numericHows: readonly string[]): boolean {
  return !type || !numericHows.includes(how) || NUMERIC_TYPE.test(type.trim())
}

/** A column name as SQL: double-quoted, with any double quote in it doubled. */
export function quoteIdent(name: string): string {
  return `"${name.replace(/"/g, '""')}"`
}

/** The SQL of `how` on column `from` (none: count everything), from the meta templates. */
export function howSql(how: AggregateHow, from: string, templates: Record<string, string>): string {
  return (templates[how] ?? '').replace(/\{col\}/g, from ? quoteIdent(from) : '*')
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** Whether SQL `expr` uses column `name`: quoted exactly, or bare as a whole word in any
 *  case. A name inside a string literal doesn't count. */
export function exprMentions(expr: string, name: string): boolean {
  const code = expr.replace(/'(?:[^']|'')*'/g, "''")
  if (code.includes(quoteIdent(name))) return true
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name)
    && new RegExp(`(^|[^A-Za-z0-9_."])${escapeRe(name)}($|[^A-Za-z0-9_"])`, 'i').test(code)
}

/** Which of `columns` SQL `expr` is, all by itself (quoted exactly, or bare in any case), or
 *  null: a group key renamed with "as" still groups by that column. */
export function exprColumn(expr: string, columns: string[]): string | null {
  const sql = expr.trim()
  return columns.find(n => sql === quoteIdent(n)
    || (/^[A-Za-z_][A-Za-z0-9_]*$/.test(n) && sql.toLowerCase() === n.toLowerCase())) ?? null
}

/** The `{how, from}` a SQL aggregate was written by, if it's exactly one of the templates
 *  (as howSql writes it, or with the column unquoted), else null. */
export function parseHowSql(
  expr: string, templates: Record<string, string>,
): { how: AggregateHow; from: string } | null {
  const sql = expr.trim()
  for (const [how, template] of Object.entries(templates)) {
    const parts = template.split('{col}').map(escapeRe)
    const col = '("(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_]*|\\*)'
    // Every {col} in a template is the same column: the first captures it, the rest repeat it.
    const pattern = parts.reduce((acc, p, i) => acc + (i === 0 ? '' : i === 1 ? col : '\\2') + p, '')
    const m = new RegExp(`^(${pattern})$`, 'i').exec(sql)
    if (!m) continue
    const raw = m[2] ?? ''
    if (raw === '*') {
      if (how === 'count') return { how: 'count', from: '' }
      continue
    }
    const from = raw.startsWith('"') ? raw.slice(1, -1).replace(/""/g, '"') : raw
    return { how: how as AggregateHow, from }
  }
  return null
}

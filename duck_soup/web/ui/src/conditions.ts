// The no-SQL form of a condition (a filter step's `where`, an output layer's `filter`, a
// derived source's `where`): rows of column · operator · value, all joined by AND or all by
// OR. The YAML keeps the SQL: buildCondition writes it, parseCondition reads back what it
// wrote (or SQL hand-written in the same shape), and anything else stays SQL.
import { quoteIdent } from './aggregates'

export type ConditionOp =
  | 'eq' | 'ne' | 'gt' | 'ge' | 'lt' | 'le'
  | 'contains' | 'not_contains' | 'starts'
  | 'empty' | 'not_empty' | 'in' | 'not_in'

export interface Clause {
  column: string
  op: ConditionOp
  /** The value as typed; for in / not_in, the values separated by commas. Unused for
   *  empty / not_empty. */
  value: string
}

export interface Condition {
  join: 'and' | 'or'
  clauses: Clause[]
}

/** What the operator dropdown says, in order. */
export const OP_LABELS: Record<ConditionOp, string> = {
  eq: 'is', ne: 'is not', gt: 'greater than', ge: 'at least', lt: 'less than', le: 'at most',
  contains: 'contains', not_contains: "doesn't contain", starts: 'starts with',
  empty: 'is empty', not_empty: 'is not empty', in: 'is one of', not_in: 'is not one of',
}

export const VALUELESS_OPS: ConditionOp[] = ['empty', 'not_empty']

const COMPARE: Partial<Record<ConditionOp, string>> = { eq: '=', ne: '<>', gt: '>', ge: '>=', lt: '<', le: '<=' }

const NUMBER = /^-?\d+(\.\d+)?$/

/** A typed value as SQL: a number as it is (unless the column holds text), else a string. */
function literal(value: string, asText: boolean): string {
  const v = value.trim()
  return !asText && NUMBER.test(v) ? v : `'${v.replace(/'/g, "''")}'`
}

/** One clause as SQL. `textColumns` (lower-cased) are compared as text even when the value
 *  looks like a number: '0301' must stay a string for a code column. */
export function clauseSql(c: Clause, textColumns: Set<string> = new Set()): string {
  const col = quoteIdent(c.column)
  const asText = textColumns.has(c.column.toLowerCase())
  const cmp = COMPARE[c.op]
  if (cmp) return `${col} ${cmp} ${literal(c.value, asText)}`
  const esc = (v: string) => v.trim().replace(/'/g, "''")
  switch (c.op) {
    case 'contains': return `${col} ILIKE '%${esc(c.value)}%'`
    case 'not_contains': return `${col} NOT ILIKE '%${esc(c.value)}%'`
    case 'starts': return `${col} ILIKE '${esc(c.value)}%'`
    case 'empty': return `${col} IS NULL`
    case 'not_empty': return `${col} IS NOT NULL`
    case 'in':
    case 'not_in': {
      const values = c.value.split(',').map(v => v.trim()).filter(Boolean).map(v => literal(v, asText))
      return `${col} ${c.op === 'in' ? 'IN' : 'NOT IN'} (${values.join(', ')})`
    }
  }
  return ''
}

/** Whether a clause has what it needs to be written. */
export function clauseComplete(c: Clause): boolean {
  return !!c.column.trim() && (VALUELESS_OPS.includes(c.op) || !!c.value.trim())
}

export function buildCondition(cond: Condition, textColumns?: Set<string>): string {
  return cond.clauses.filter(clauseComplete).map(c => clauseSql(c, textColumns))
    .join(cond.join === 'and' ? ' AND ' : ' OR ')
}

// ---- reading SQL back ----
const IDENT = String.raw`("(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_]*)`
const STR = String.raw`'(?:[^']|'')*'`
const VALUE = String.raw`(${STR}|-?\d+(?:\.\d+)?)`

const unquoteIdent = (s: string) => s.startsWith('"') ? s.slice(1, -1).replace(/""/g, '"') : s
const unquoteValue = (s: string) => s.startsWith("'") ? s.slice(1, -1).replace(/''/g, "'") : s

const CLAUSE_PATTERNS: [RegExp, (m: RegExpExecArray) => Clause | null][] = [
  [new RegExp(`^${IDENT}\\s*(=|<>|!=|>=|<=|>|<)\\s*${VALUE}$`), m => {
    const op = ({ '=': 'eq', '<>': 'ne', '!=': 'ne', '>': 'gt', '>=': 'ge', '<': 'lt', '<=': 'le' } as const)[m[2] as '=']
    return { column: unquoteIdent(m[1]!), op, value: unquoteValue(m[3]!) }
  }],
  [new RegExp(`^${IDENT}\\s+(NOT\\s+)?ILIKE\\s+(${STR})$`, 'i'), m => {
    const pattern = unquoteValue(m[3]!)
    const inner = pattern.slice(1, -1)
    if (pattern.length > 1 && pattern.startsWith('%') && pattern.endsWith('%') && !inner.includes('%')) {
      return { column: unquoteIdent(m[1]!), op: m[2] ? 'not_contains' : 'contains', value: inner }
    }
    if (!m[2] && pattern.endsWith('%') && !pattern.slice(0, -1).includes('%')) {
      return { column: unquoteIdent(m[1]!), op: 'starts', value: pattern.slice(0, -1) }
    }
    return null
  }],
  [new RegExp(`^${IDENT}\\s+IS\\s+(NOT\\s+)?NULL$`, 'i'), m =>
    ({ column: unquoteIdent(m[1]!), op: m[2] ? 'not_empty' : 'empty', value: '' })],
  [new RegExp(`^${IDENT}\\s+(NOT\\s+)?IN\\s*\\(\\s*(${VALUE}(?:\\s*,\\s*${VALUE})*)\\s*\\)$`, 'i'), m => {
    const values = [...m[3]!.matchAll(new RegExp(VALUE, 'g'))].map(v => unquoteValue(v[0]))
    return { column: unquoteIdent(m[1]!), op: m[2] ? 'not_in' : 'in', value: values.join(', ') }
  }],
]

/** Splits `sql` on a top-level keyword (AND / OR), outside quotes and brackets. */
function splitTopLevel(sql: string, word: 'AND' | 'OR'): string[] {
  const parts: string[] = []
  let depth = 0, quote = '', start = 0
  for (let i = 0; i < sql.length; i++) {
    const ch = sql[i]!
    if (quote) { if (ch === quote) quote = sql[i + 1] === quote ? (i++, quote) : ''; continue }
    if (ch === "'" || ch === '"') quote = ch
    else if (ch === '(') depth++
    else if (ch === ')') depth--
    else if (depth === 0 && /\s/.test(ch)) {
      const m = new RegExp(`^\\s+${word}\\s+`, 'i').exec(sql.slice(i))
      if (m) { parts.push(sql.slice(start, i)); i += m[0].length - 1; start = i + 1 }
    }
  }
  parts.push(sql.slice(start))
  return parts.map(p => p.trim())
}

/** The condition `sql` was written as, if every part of it is one the rows can show, all
 *  joined the same way; else null. An empty `sql` is an empty AND condition. */
export function parseCondition(sql: string): Condition | null {
  const text = sql.trim()
  if (!text) return { join: 'and', clauses: [] }
  const ands = splitTopLevel(text, 'AND')
  const ors = splitTopLevel(text, 'OR')
  if (ands.length > 1 && ors.length > 1) return null  // mixed: needs brackets, so SQL
  const join = ors.length > 1 ? 'or' : 'and'
  const clauses: Clause[] = []
  for (const part of join === 'or' ? ors : ands) {
    let clause: Clause | null = null
    for (const [re, make] of CLAUSE_PATTERNS) {
      const m = re.exec(part)
      if (m) { clause = make(m); if (clause) break }
    }
    if (!clause) return null
    clauses.push(clause)
  }
  return { join, clauses }
}

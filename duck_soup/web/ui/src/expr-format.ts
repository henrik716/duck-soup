import { KEYWORDS } from './expr-highlight'

// The expression builder's Format button: a small pretty printer for one SQL expression,
// not a general SQL formatter. Keywords are uppercased and whitespace normalized; whatever
// fits in MAX_WIDTH stays on one line. Otherwise a CASE puts each WHEN / ELSE on its own
// line, a call or list that doesn't fit puts one argument per line, and a line that's still
// too long breaks before its top-level AND / OR. A CASE with two or more WHENs always breaks.
// Function names, identifiers and strings are left exactly as written.

const MAX_WIDTH = 80
const INDENT = '  '

type Kind = 'kw' | 'str' | 'num' | 'ident' | 'op'
interface Tok { t: 'tok'; kind: Kind; text: string; sp: boolean }
interface Group { t: 'group'; open: string; close: string; args: Item[][]; sp: boolean }
interface Case { t: 'case'; head: Item[]; clauses: Item[][]; sp: boolean }
type Item = Tok | Group | Case

const TOKEN_RE = new RegExp([
  /(\s+)/.source,
  /(--[^\n]*|\/\*[\s\S]*?(?:\*\/|$))/.source,       // comments
  /('(?:[^']|'')*'?|"(?:[^"]|"")*"?)/.source,       // strings, quoted identifiers
  /(\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+)/.source,      // numbers
  /([A-Za-z_][A-Za-z0-9_]*)/.source,                // words
  /(::|->|>=|<=|<>|!=|\|\||\*\*|\/\/|[\s\S])/.source, // operators, punctuation
].join('|'), 'g')

const CLOSERS: Record<string, string> = { '(': ')', '[': ']', '{': '}' }

class Unformattable extends Error {}

function tokenize(src: string): Tok[] {
  const toks: Tok[] = []
  let sp = false
  TOKEN_RE.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = TOKEN_RE.exec(src))) {
    const [, ws, comment, str, num, word, op] = m
    if (ws !== undefined) { sp = true; continue }
    // A -- comment runs to the end of its line, so moving it would comment out code.
    if (comment !== undefined) throw new Unformattable('Remove the comments to format this expression.')
    if (str !== undefined) toks.push({ t: 'tok', kind: str[0] === "'" ? 'str' : 'ident', text: str, sp })
    else if (num !== undefined) toks.push({ t: 'tok', kind: 'num', text: num, sp })
    else if (word !== undefined) {
      const kw = KEYWORDS.has(word.toLowerCase())
      toks.push({ t: 'tok', kind: kw ? 'kw' : 'ident', text: kw ? word.toUpperCase() : word, sp })
    } else toks.push({ t: 'tok', kind: 'op', text: op, sp })
    sp = false
  }
  return toks
}

const isKw = (it: Item | undefined, word?: string): boolean =>
  !!it && it.t === 'tok' && it.kind === 'kw' && (word === undefined || it.text === word)

// Turns the token list into nested groups: (…) / […] / {…} split at their commas, and CASE … END.
function parse(toks: Tok[]): Item[] {
  let i = 0
  const seq = (stop: (t: Tok) => boolean): Item[] => {
    const items: Item[] = []
    while (i < toks.length && !stop(toks[i])) {
      const tok = toks[i++]
      if (tok.kind === 'op' && CLOSERS[tok.text]) {
        const close = CLOSERS[tok.text]
        const args: Item[][] = []
        for (;;) {
          args.push(seq(t => t.kind === 'op' && (t.text === ',' || t.text === close)))
          const end = toks[i++]
          if (!end) throw new Unformattable(`Missing "${close}".`)
          if (end.text === close) break
        }
        items.push({ t: 'group', open: tok.text, close, args, sp: tok.sp })
      } else if (tok.kind === 'op' && Object.values(CLOSERS).includes(tok.text)) {
        throw new Unformattable(`Unmatched "${tok.text}".`)
      } else if (isKw(tok, 'CASE')) {
        const body = seq(t => isKw(t, 'END'))
        if (i++ >= toks.length) throw new Unformattable('CASE without END.')
        const firstClause = body.findIndex(it => isKw(it, 'WHEN') || isKw(it, 'ELSE'))
        const head = firstClause === -1 ? body : body.slice(0, firstClause)
        const clauses: Item[][] = []
        for (const it of firstClause === -1 ? [] : body.slice(firstClause)) {
          if (isKw(it, 'WHEN') || isKw(it, 'ELSE')) clauses.push([])
          clauses[clauses.length - 1].push(it)
        }
        items.push({ t: 'case', head, clauses, sp: tok.sp })
      } else {
        items.push(tok)
      }
    }
    return items
  }
  const items = seq(() => false)
  return items
}

const isOp = (it: Item, ...ops: string[]) => it.t === 'tok' && it.kind === 'op' && ops.includes(it.text)

// Whether a space goes between two neighbouring items on one line.
function spaced(prev: Item, cur: Item): boolean {
  if (isOp(cur, ',')) return false
  if (isOp(prev, ',')) return true
  if (isOp(prev, '.', '::') || isOp(cur, '.', '::')) return cur.sp
  const kwish = (it: Item) => isKw(it) || it.t === 'case'
  if (kwish(prev) || kwish(cur)) return cur.t === 'group' ? cur.sp : true
  return cur.sp
}

const whenCount = (c: Case) => c.clauses.filter(cl => isKw(cl[0], 'WHEN')).length

function forced(it: Item): boolean {
  if (it.t === 'group') return it.args.some(a => a.some(forced))
  if (it.t === 'case') return whenCount(it) >= 2 || [it.head, ...it.clauses].some(s => s.some(forced))
  return false
}

function flat(it: Item): string {
  if (it.t === 'tok') return it.text
  if (it.t === 'group') return it.open + it.args.map(flatSeq).join(', ') + it.close
  return ['CASE', flatSeq(it.head), ...it.clauses.map(flatSeq), 'END'].filter(Boolean).join(' ')
}

function flatSeq(items: Item[]): string {
  return items.map((it, k) => (k > 0 && spaced(items[k - 1], it) ? ' ' : '') + flat(it)).join('')
}

class Out {
  s = ''
  get col(): number { return this.s.length - this.s.lastIndexOf('\n') - 1 }
  get lineIndent(): string { return /^ */.exec(this.s.slice(this.s.lastIndexOf('\n') + 1))![0] }
  newline(indent: string) { this.s = this.s.trimEnd() + '\n' + indent }
}

// `cont` is the indent for a line broken before AND / OR (default: the current line's).
function emitSeq(items: Item[], out: Out, cont = out.lineIndent): void {
  const oneLine = flatSeq(items)
  if (!items.some(forced) && out.col + oneLine.length <= MAX_WIDTH) { out.s += oneLine; return }
  let inBetween = false // the AND of `x BETWEEN a AND b` is no place to break
  items.forEach((it, k) => {
    if (k > 0) {
      if ((isKw(it, 'AND') && !inBetween) || isKw(it, 'OR')) out.newline(cont)
      else if (spaced(items[k - 1], it)) out.s += ' '
    }
    if (isKw(it, 'BETWEEN')) inBetween = true
    else if (isKw(it, 'AND')) inBetween = false
    emit(it, out)
  })
}

function emit(it: Item, out: Out): void {
  if (it.t === 'tok') { out.s += it.text; return }
  if (!forced(it) && out.col + flat(it).length <= MAX_WIDTH) { out.s += flat(it); return }
  const base = out.lineIndent
  if (it.t === 'group') {
    out.s += it.open
    it.args.forEach((arg, k) => {
      out.newline(base + INDENT)
      emitSeq(arg, out)
      if (k < it.args.length - 1) out.s += ','
    })
    out.newline(base)
    out.s += it.close
    return
  }
  out.s += 'CASE'
  if (it.head.length) { out.s += ' '; emitSeq(it.head, out) }
  for (const clause of it.clauses) {
    out.newline(base + INDENT)
    emitSeq(clause, out, base + INDENT + INDENT)
  }
  out.newline(base)
  out.s += 'END'
}

/** `src` pretty-printed, or `{ error }` when it can't be parsed (unbalanced brackets, a
 *  CASE without END, a comment). */
export function formatExpr(src: string): { text: string } | { error: string } {
  try {
    const out = new Out()
    emitSeq(parse(tokenize(src)), out)
    return { text: out.s.trimEnd() }
  } catch (e) {
    if (e instanceof Unformattable) return { error: e.message }
    throw e
  }
}

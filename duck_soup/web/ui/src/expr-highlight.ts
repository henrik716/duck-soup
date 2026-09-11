import { esc } from './dom'

// SQL keywords worth coloring distinctly from functions/columns in the expression editor.
// Deliberately small — this is a hand-rolled overlay highlighter, not a real SQL parser.
const KEYWORDS = new Set([
  'case', 'when', 'then', 'else', 'end', 'and', 'or', 'not', 'is', 'null',
  'in', 'like', 'ilike', 'between', 'as', 'distinct', 'asc', 'desc',
  'true', 'false', 'interval', 'cast'
])

const TOKEN_RE = /('(?:[^']|'')*')|(\d+\.?\d*)|([A-Za-z_][A-Za-z0-9_]*)/g

// Tokenizes a raw SQL expression into HTML with <span> wrappers for keywords, function
// calls (identifier immediately followed by "("), strings, and numbers — everything else
// (columns, punctuation) stays unstyled. Rendered into a <pre> sitting behind the editable
// textarea; a trailing newline keeps it the same line-count as the textarea for scroll sync.
export function highlightExpr(src: string): string {
  let out = ''
  let lastIndex = 0
  TOKEN_RE.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = TOKEN_RE.exec(src))) {
    out += esc(src.slice(lastIndex, m.index))
    const [full, str, num, ident] = m
    if (str !== undefined) {
      out += `<span class="expr-tok-string">${esc(str)}</span>`
    } else if (num !== undefined) {
      out += `<span class="expr-tok-number">${esc(num)}</span>`
    } else if (ident !== undefined) {
      const lower = ident.toLowerCase()
      const after = src.slice(m.index + full.length)
      if (KEYWORDS.has(lower)) {
        out += `<span class="expr-tok-keyword">${esc(ident)}</span>`
      } else if (/^\s*\(/.test(after)) {
        out += `<span class="expr-tok-func">${esc(ident)}</span>`
      } else {
        out += esc(ident)
      }
    }
    lastIndex = m.index + full.length
  }
  out += esc(src.slice(lastIndex))
  return out + '\n'
}

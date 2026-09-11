import { mkEl, esc } from './dom'

export interface AutocompleteItem { name: string; type: string }

// Computed-style properties that must be mirrored onto the offscreen measuring div so its
// text wraps identically to the real textarea — otherwise the measured caret position drifts.
const MIRROR_PROPS: (keyof CSSStyleDeclaration)[] = [
  'boxSizing', 'width', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
  'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth',
  'fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing',
]

// Pixel offset of the caret at `pos` relative to the textarea's own top-left content
// corner (ignoring scroll) — the standard "mirror div" technique, since textareas expose
// no native caret-coordinates API.
function caretOffset(ta: HTMLTextAreaElement, pos: number): { top: number; left: number } {
  const div = mkEl('div')
  const style = getComputedStyle(ta)
  MIRROR_PROPS.forEach(p => { (div.style as any)[p] = style[p] })
  Object.assign(div.style, {
    position: 'absolute', visibility: 'hidden', whiteSpace: 'pre-wrap', wordWrap: 'break-word',
    top: '0', left: '-9999px', width: `${ta.clientWidth}px`,
  })
  div.textContent = ta.value.slice(0, pos)
  const marker = mkEl('span', { textContent: '​' })
  div.appendChild(marker)
  document.body.appendChild(div)
  const top = marker.offsetTop
  const left = marker.offsetLeft
  document.body.removeChild(div)
  return { top, left }
}

// Cursor-anchored autocomplete popup for a SQL expression textarea. Doesn't attach its own
// keydown listener — the host (expr-drawer.ts) owns key handling and calls handleKeydown()
// first, since Escape/Enter/Tab need to mean "control the popup" while it's open and their
// normal drawer-level meaning (cancel/apply/indent) otherwise.
export class ExprAutocomplete {
  private popup: HTMLElement
  private matches: AutocompleteItem[] = []
  private selected = 0
  isOpen = false

  constructor(private ta: HTMLTextAreaElement, private getItems: () => AutocompleteItem[]) {
    this.popup = mkEl('div', { className: 'expr-autocomplete-popup' })
    document.body.appendChild(this.popup)
    // preventDefault on mousedown (not click) keeps the textarea focused so no blur fires
    // before the selection is read.
    this.popup.addEventListener('mousedown', e => {
      const item = (e.target as HTMLElement).closest<HTMLElement>('.expr-ac-item')
      if (!item) return
      e.preventDefault()
      this.selected = parseInt(item.dataset['i'] || '0', 10)
      this.insertSelected()
    })
  }

  private wordRange(): { start: number; end: number; word: string } {
    const pos = this.ta.selectionStart
    const val = this.ta.value
    let start = pos
    while (start > 0 && /[A-Za-z0-9_]/.test(val[start - 1])) start--
    return { start, end: pos, word: val.slice(start, pos) }
  }

  private render(): void {
    this.popup.innerHTML = this.matches.map((m, i) =>
      `<div class="expr-ac-item${i === this.selected ? ' sel' : ''}" data-i="${i}">` +
      `<span class="expr-ac-name">${esc(m.name)}</span><span class="expr-ac-type">${esc(m.type)}</span></div>`
    ).join('')
  }

  private position(): void {
    const { start } = this.wordRange()
    const { top, left } = caretOffset(this.ta, start)
    const rect = this.ta.getBoundingClientRect()
    const lineHeight = parseFloat(getComputedStyle(this.ta).lineHeight) || 16
    this.popup.style.left = `${rect.left + left - this.ta.scrollLeft}px`
    this.popup.style.top = `${rect.top + top - this.ta.scrollTop + lineHeight}px`
  }

  private insertSelected(): void {
    const m = this.matches[this.selected]
    if (!m) return
    const { start, end } = this.wordRange()
    const val = this.ta.value
    this.ta.value = val.slice(0, start) + m.name + val.slice(end)
    this.ta.selectionStart = this.ta.selectionEnd = start + m.name.length
    this.close()
    this.ta.focus()
    this.ta.dispatchEvent(new Event('input', { bubbles: true }))
  }

  // Call on every textarea 'input' event.
  update(): void {
    const { word } = this.wordRange()
    if (!word) { this.close(); return }
    const q = word.toLowerCase()
    this.matches = this.getItems().filter(it => it.name.toLowerCase().startsWith(q)).slice(0, 8)
    if (!this.matches.length) { this.close(); return }
    this.selected = 0
    this.isOpen = true
    this.popup.style.display = 'block'
    this.render()
    this.position()
  }

  close(): void {
    this.isOpen = false
    this.popup.style.display = 'none'
  }

  // Returns true if the popup consumed the key (caller should not also act on it).
  handleKeydown(e: KeyboardEvent): boolean {
    if (!this.isOpen) return false
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      this.selected = (this.selected + 1) % this.matches.length
      this.render()
      return true
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      this.selected = (this.selected - 1 + this.matches.length) % this.matches.length
      this.render()
      return true
    }
    // Plain Enter/Tab picks the suggestion; Ctrl/Cmd+Enter is the drawer's "Apply" shortcut
    // and must always win even while the popup happens to be open.
    if (e.key === 'Tab' || (e.key === 'Enter' && !e.ctrlKey && !e.metaKey)) {
      e.preventDefault()
      this.insertSelected()
      return true
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      this.close()
      return true
    }
    return false
  }
}

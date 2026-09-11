// Shared modal/drawer behaviour: Escape to close, focus trapped inside while open, and
// focus returned to whatever opened it. Previously only the expression drawer handled
// Escape, and none of the three restored focus — a keyboard user who opened the file
// browser was dropped back at the top of the document.

interface Entry {
  el: HTMLElement
  close: () => void
  restoreTo: HTMLElement | null
}

const stack: Entry[] = []

const FOCUSABLE = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled])',
  'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
].join(',')

function focusable(el: HTMLElement): HTMLElement[] {
  return [...el.querySelectorAll<HTMLElement>(FOCUSABLE)]
    .filter(n => n.offsetParent !== null || n === document.activeElement)
}

let wired = false
function wire(): void {
  if (wired) return
  wired = true

  document.addEventListener('keydown', e => {
    const top = stack[stack.length - 1]
    if (!top) return

    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      top.close()
      return
    }

    if (e.key === 'Tab') {
      const items = focusable(top.el)
      if (items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]
      // Wrap rather than letting focus escape to the page behind the overlay.
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
  }, true)
}

export function openOverlay(el: HTMLElement, close: () => void): void {
  wire()
  if (stack.some(s => s.el === el)) return
  stack.push({ el, close, restoreTo: document.activeElement as HTMLElement | null })
  // Let the overlay paint before moving focus into it.
  setTimeout(() => focusable(el)[0]?.focus(), 0)
}

export function closeOverlay(el: HTMLElement): void {
  const i = stack.findIndex(s => s.el === el)
  if (i < 0) return
  const [entry] = stack.splice(i, 1)
  entry.restoreTo?.focus?.()
}

import { esc } from './dom'

// ---- generic searchable combo dropdown (reused for any pipeline dropdown) ----
export interface ComboOptionDef {
  value: string
  // May contain deliberate markup (e.g. a colored category badge) — callers that embed
  // dynamic text inside it are responsible for escaping that text themselves; plain
  // `value`-only options (the `string` case below) are escaped here since they're just data.
  label?: string
}

// An empty option list used to open as a blank box, which reads as broken rather than as
// "there's nothing to offer yet". `emptyText` fills it with an explanation instead. It
// deliberately uses its own class: every keyboard/filter path below queries `.combo-option`,
// so `.combo-empty` is inert without any further special-casing.
function comboOptionsHtml(options: (string | ComboOptionDef)[], emptyText = ''): string {
  if (options.length === 0) {
    return emptyText ? `<div class="combo-empty">${esc(emptyText)}</div>` : ''
  }
  return options.map(o => {
    if (typeof o === 'string') {
      return `<div class="combo-option" role="option" data-value="${esc(o)}">${esc(o)}</div>`
    } else {
      const displayLabel = o.label || esc(o.value)
      return `<div class="combo-option" role="option" data-value="${esc(o.value)}">${displayLabel}</div>`
    }
  }).join('')
}

// attrHtml is the literal attribute text for the underlying <input>, e.g. `data-k="format"` or `class="pl-base"`
export function comboField(attrHtml: string, value: string, options: (string | ComboOptionDef)[], placeholder = '', emptyText = ''): string {
  return `
    <div class="combo">
      <input ${attrHtml} placeholder="${esc(placeholder)}" value="${esc(value)}" autocomplete="off"
             role="combobox" aria-expanded="false" aria-autocomplete="list">
      <i data-lucide="chevron-down" class="combo-arrow"></i>
      <div class="combo-list" role="listbox">${comboOptionsHtml(options, emptyText)}</div>
    </div>`
}

// Replace a combo's option list in place (e.g. once a source's layers/columns are known)
export function setComboOptions(input: Element, options: (string | ComboOptionDef)[], emptyText = ''): void {
  const list = input.closest('.combo')?.querySelector<HTMLElement>('.combo-list')
  if (list) list.innerHTML = comboOptionsHtml(options, emptyText)
}

// Add a value to a combo's option list if it isn't already there (used when hydrating from saved config)
export function ensureComboOption(input: Element, value: string): void {
  if (!value) return
  const list = input.closest('.combo')?.querySelector<HTMLElement>('.combo-list')
  if (list && ![...list.querySelectorAll<HTMLElement>('.combo-option')].some(o => o.dataset['value'] === value)) {
    // The list is no longer empty, so drop any "nothing to offer" placeholder first.
    list.querySelector('.combo-empty')?.remove()
    list.insertAdjacentHTML('beforeend', `<div class="combo-option" role="option" data-value="${esc(value)}">${esc(value)}</div>`)
  }
}

// One delegated listener for the whole document, installed once. Previously every wired
// combo added its own `document` click listener that was never removed — and combos are
// re-rendered constantly by updateDatalistsScoped, so those accumulated without bound.
let outsideClickWired = false
function wireOutsideClick(): void {
  if (outsideClickWired) return
  outsideClickWired = true
  document.addEventListener('click', e => {
    const openCombo = (e.target as HTMLElement)?.closest?.('.combo')
    document.querySelectorAll<HTMLElement>('.combo-list.open').forEach(list => {
      if (list.closest('.combo') !== openCombo) {
        list.classList.remove('open')
        list.closest('.combo')?.querySelector('input')?.setAttribute('aria-expanded', 'false')
      }
    })
  })
}

let comboSeq = 0

export function wireCombos(root: HTMLElement): void {
  wireOutsideClick()

  root.querySelectorAll<HTMLElement>('.combo').forEach(combo => {
    if (combo.dataset['wired']) return
    combo.dataset['wired'] = '1'

    const input = combo.querySelector<HTMLInputElement>('input')!
    const list = combo.querySelector<HTMLElement>('.combo-list')!

    const listId = `combo-list-${comboSeq++}`
    list.id = listId
    input.setAttribute('role', 'combobox')
    input.setAttribute('aria-controls', listId)
    input.setAttribute('aria-autocomplete', 'list')
    input.setAttribute('aria-expanded', 'false')
    list.setAttribute('role', 'listbox')

    // Options are replaced wholesale by setComboOptions, so ids and roles are (re)applied
    // each time the list opens rather than once at wire time.
    const visibleOptions = (): HTMLElement[] =>
      [...list.querySelectorAll<HTMLElement>('.combo-option')].filter(o => o.style.display !== 'none')

    const decorate = () => {
      list.querySelectorAll<HTMLElement>('.combo-option').forEach((o, i) => {
        o.setAttribute('role', 'option')
        if (!o.id) o.id = `${listId}-opt-${i}`
      })
    }

    const setActive = (opt: HTMLElement | null) => {
      list.querySelectorAll<HTMLElement>('.combo-option.active').forEach(o => {
        o.classList.remove('active')
        o.setAttribute('aria-selected', 'false')
      })
      if (opt) {
        opt.classList.add('active')
        opt.setAttribute('aria-selected', 'true')
        input.setAttribute('aria-activedescendant', opt.id)
        opt.scrollIntoView({ block: 'nearest' })
      } else {
        input.removeAttribute('aria-activedescendant')
      }
    }

    const showAll = () => {
      list.querySelectorAll<HTMLElement>('.combo-option').forEach(o => { o.style.display = '' })
    }
    const filter = () => {
      const q = input.value.trim().toLowerCase()
      list.querySelectorAll<HTMLElement>('.combo-option').forEach(o => {
        o.style.display = !q || (o.dataset['value'] ?? '').toLowerCase().includes(q) ? '' : 'none'
      })
      // The previously active option may have just been filtered out.
      const active = list.querySelector<HTMLElement>('.combo-option.active')
      if (active && active.style.display === 'none') setActive(null)
    }
    // Opening shows every alternative, even when the input already holds a selected value;
    // filtering only kicks in once the user starts typing to narrow the list.
    const open = () => {
      showAll()
      decorate()
      list.classList.add('open')
      input.setAttribute('aria-expanded', 'true')
    }
    const close = () => {
      list.classList.remove('open')
      input.setAttribute('aria-expanded', 'false')
      setActive(null)
    }
    const isOpen = () => list.classList.contains('open')

    const commit = (opt: HTMLElement) => {
      input.value = opt.dataset['value'] ?? ''
      close()
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new Event('change', { bubbles: true }))
    }

    const move = (delta: number) => {
      const opts = visibleOptions()
      if (!opts.length) return
      const current = opts.findIndex(o => o.classList.contains('active'))
      // From no selection, ArrowDown lands on the first option and ArrowUp on the last.
      const next = current === -1
        ? (delta > 0 ? 0 : opts.length - 1)
        : Math.min(opts.length - 1, Math.max(0, current + delta))
      setActive(opts[next])
    }

    input.addEventListener('focus', open)
    input.addEventListener('click', open)
    input.addEventListener('input', () => { if (!isOpen()) open(); filter() })

    input.addEventListener('keydown', e => {
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          if (!isOpen()) { open(); filter() }
          move(1)
          break
        case 'ArrowUp':
          e.preventDefault()
          if (!isOpen()) { open(); filter() }
          move(-1)
          break
        case 'Home':
          if (!isOpen()) return
          e.preventDefault()
          setActive(visibleOptions()[0] ?? null)
          break
        case 'End': {
          if (!isOpen()) return
          e.preventDefault()
          const opts = visibleOptions()
          setActive(opts[opts.length - 1] ?? null)
          break
        }
        case 'Enter': {
          const active = list.querySelector<HTMLElement>('.combo-option.active')
          if (isOpen() && active) {
            // Only swallow Enter when it actually selects something; otherwise let the
            // typed free-text value stand — these combos accept arbitrary input.
            e.preventDefault()
            commit(active)
          } else if (isOpen()) {
            close()
          }
          break
        }
        case 'Escape':
          if (isOpen()) { e.stopPropagation(); close() }
          else input.blur()
          break
        case 'Tab':
          close()
          break
      }
    })

    list.addEventListener('mousedown', e => {
      const opt = (e.target as HTMLElement).closest<HTMLElement>('.combo-option')
      if (!opt) return
      e.preventDefault()
      commit(opt)
    })
  })
}

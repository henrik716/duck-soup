// ---- generic searchable combo dropdown (reused for any pipeline dropdown) ----
export interface ComboOptionDef {
  value: string
  label?: string
}

function comboOptionsHtml(options: (string | ComboOptionDef)[]): string {
  return options.map(o => {
    if (typeof o === 'string') {
      return `<div class="combo-option" data-value="${o}">${o}</div>`
    } else {
      const displayLabel = o.label || o.value
      return `<div class="combo-option" data-value="${o.value}">${displayLabel}</div>`
    }
  }).join('')
}

// attrHtml is the literal attribute text for the underlying <input>, e.g. `data-k="format"` or `class="pl-base"`
export function comboField(attrHtml: string, value: string, options: (string | ComboOptionDef)[], placeholder = ''): string {
  return `
    <div class="combo">
      <input ${attrHtml} placeholder="${placeholder}" value="${value ?? ''}" autocomplete="off">
      <i data-lucide="chevron-down" class="combo-arrow"></i>
      <div class="combo-list">${comboOptionsHtml(options)}</div>
    </div>`
}

// Replace a combo's option list in place (e.g. once a source's layers/columns are known)
export function setComboOptions(input: Element, options: (string | ComboOptionDef)[]): void {
  const list = input.closest('.combo')?.querySelector<HTMLElement>('.combo-list')
  if (list) list.innerHTML = comboOptionsHtml(options)
}

// Add a value to a combo's option list if it isn't already there (used when hydrating from saved config)
export function ensureComboOption(input: Element, value: string): void {
  if (!value) return
  const list = input.closest('.combo')?.querySelector<HTMLElement>('.combo-list')
  if (list && ![...list.querySelectorAll<HTMLElement>('.combo-option')].some(o => o.dataset['value'] === value)) {
    list.insertAdjacentHTML('beforeend', `<div class="combo-option" data-value="${value}">${value}</div>`)
  }
}

export function wireCombos(root: HTMLElement): void {
  root.querySelectorAll<HTMLElement>('.combo').forEach(combo => {
    if (combo.dataset['wired']) return
    combo.dataset['wired'] = '1'

    const input = combo.querySelector<HTMLInputElement>('input')!
    const list = combo.querySelector<HTMLElement>('.combo-list')!

    const showAll = () => {
      list.querySelectorAll<HTMLElement>('.combo-option').forEach(o => { o.style.display = '' })
    }
    const filter = () => {
      const q = input.value.trim().toLowerCase()
      list.querySelectorAll<HTMLElement>('.combo-option').forEach(o => {
        o.style.display = !q || (o.dataset['value'] ?? '').toLowerCase().includes(q) ? '' : 'none'
      })
    }
    // Opening shows every alternative, even when the input already holds a selected value;
    // filtering only kicks in once the user starts typing to narrow the list.
    const open = () => { showAll(); list.classList.add('open') }
    const close = () => list.classList.remove('open')

    input.addEventListener('focus', open)
    input.addEventListener('click', open)
    input.addEventListener('input', filter)
    input.addEventListener('keydown', e => { if (e.key === 'Escape') { close(); input.blur() } })
    document.addEventListener('click', e => { if (!combo.contains(e.target as Node)) close() })

    list.addEventListener('mousedown', e => {
      const opt = (e.target as HTMLElement).closest<HTMLElement>('.combo-option')
      if (!opt) return
      e.preventDefault()
      input.value = opt.dataset['value'] ?? ''
      close()
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new Event('change', { bubbles: true }))
    })
  })
}

import { createIcons, Package, Trash2, ChevronDown } from 'lucide'
import { mkEl } from '../dom'
import type { OutputLayer } from '../types'

// ---- output layer card ----
// One entry in a pipeline's `layers:` list — the same upstream chain, written
// out with its own name/CRS/filter. Add more than one to fan a converged
// chain out into several layers (e.g. matched vs. unmatched) in one pass.
export function outputLayerCard(ol: Partial<OutputLayer> = {}, syncFn: () => void): HTMLElement {
  const c = mkEl('div', { className: 'card collapsed' })
  c.innerHTML = `
    <div class="item-head" style="cursor:pointer; user-select:none;">
      <span class="tag"><i data-lucide="package" style="width:12px;height:12px;margin-right:2px"></i>layer</span>
      <span class="item-title" style="font-family:var(--mono); font-size:11px; font-weight:600; margin-left:8px; color:var(--ink);"></span>
      <span class="spacer"></span>
      <button class="mini danger ghost" data-del><i data-lucide="trash-2" style="width:12px;height:12px"></i> remove</button>
      <i data-lucide="chevron-down" class="card-chevron" style="width:14px;height:14px;color:var(--muted);transition:transform 0.2s;margin-left:8px;"></i>
    </div>
    <div class="card-content" style="margin-top:12px;">
      <div class="row">
        <label class="field grow">layer name<input data-k="layer" placeholder="MyLayer"></label>
        <label class="field grow">CRS<input data-k="crs" placeholder="EPSG:25833"></label>
      </div>
      <label class="field" style="margin-top:8px">filter (optional SQL; rows where this is false are excluded from this layer)
        <input data-k="filter" placeholder="county IS NOT NULL"></label>
    </div>`

  c.querySelector('[data-del]')!.addEventListener('click', (e) => {
    e.stopPropagation()
    c.classList.add('slide-out')
    setTimeout(() => { c.remove(); syncFn() }, 250)
  })

  for (const [k, v] of Object.entries(ol)) {
    if (k === 'mapping') continue
    const inp = c.querySelector<HTMLInputElement>(`[data-k="${k}"]`)
    if (inp && v != null) inp.value = String(v)
  }

  c.querySelectorAll('[data-k]').forEach(i => {
    i.addEventListener('input', syncFn)
    i.addEventListener('change', syncFn)
  })

  const layerInp = c.querySelector<HTMLInputElement>('[data-k="layer"]')!
  const crsInp = c.querySelector<HTMLInputElement>('[data-k="crs"]')!
  const titleEl = c.querySelector<HTMLElement>('.item-title')!
  const updateTitle = () => {
    const name = layerInp.value.trim()
    const crsV = crsInp.value.trim()
    titleEl.textContent = name ? `${name}${crsV ? ' · ' + crsV : ''}` : ''
  }
  layerInp.addEventListener('input', updateTitle)
  crsInp.addEventListener('input', updateTitle)
  updateTitle()

  const head = c.querySelector('.item-head')!
  head.addEventListener('click', e => {
    if ((e.target as Element).closest('button, input, select, a')) return
    const wasCollapsed = c.classList.contains('collapsed')
    if (wasCollapsed) {
      const parent = c.parentElement
      if (parent) {
        parent.querySelectorAll(':scope > .card').forEach(sibling => {
          if (sibling !== c) sibling.classList.add('collapsed')
        })
      }
      c.classList.remove('collapsed')
    } else {
      c.classList.add('collapsed')
    }
  })

  createIcons({ icons: { Package, Trash2, ChevronDown } })
  return c
}

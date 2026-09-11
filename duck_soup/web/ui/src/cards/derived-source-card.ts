import { createIcons, GitBranch, Trash2, ChevronDown } from 'lucide'
import { mkEl, wireCollapse } from '../dom'
import { wireCombos, comboField } from '../combo'
import { mutate } from '../history'
import type { DerivedSource } from '../types'

// ---- derived source card ----
// A filtered/buffered view of an existing source, registered under its own
// id so any step's `source:` can target it — the fork/transform/rejoin
// pattern (filter a subset of a source, buffer it, join it back into base).
export function derivedSourceCard(ds: Partial<DerivedSource> = {}, syncFn: () => void, sourceIds: string[] = []): HTMLElement {
  const c = mkEl('div', { className: 'card collapsed' })
  c.innerHTML = `
    <div class="item-head" style="cursor:pointer; user-select:none;">
      <span class="tag"><i data-lucide="git-branch" style="width:12px;height:12px;margin-right:2px"></i>derived</span>
      <span class="item-title" style="font-family:var(--mono); font-size:11px; font-weight:600; margin-left:8px; color:var(--ink);"></span>
      <span class="spacer"></span>
      <button class="mini danger ghost" data-del aria-label="Remove this derived source"><i data-lucide="trash-2" style="width:12px;height:12px"></i> remove</button>
      <i data-lucide="chevron-down" class="card-chevron" style="width:14px;height:14px;color:var(--muted);transition:transform 0.2s;margin-left:8px;"></i>
    </div>
    <div class="card-content" style="margin-top:12px;">
      <div class="row">
        <label class="field grow">id<input data-k="id" placeholder="fylke_buffered"></label>
        <label class="field grow">from${comboField('data-k="from"', ds.from ?? '', sourceIds)}</label>
      </div>
      <label class="field" style="margin-top:8px">where (optional SQL filter)
        <input data-k="where" placeholder="facility_type = 'ISPS'">
      </label>
      <div class="row" style="margin-top:8px">
        <label class="field grow">buffer distance (optional, working CRS units)<input data-k="buffer" type="number" step="any" placeholder="none"></label>
        <label class="field" style="flex:0 0 auto"><span>&nbsp;</span>
          <span style="display:flex;align-items:center;gap:6px;color:var(--ink);font-family:system-ui;white-space:nowrap">
            <input type="checkbox" data-k="make_valid" checked style="width:auto;margin:0"> repair invalid geometry</span></label>
      </div>
      <p class="hint" style="margin-top:8px">A filtered/buffered view of an existing source, usable anywhere a step needs a "source" — fork part of a source, transform it, then join it back into the base flow.</p>
    </div>`

  wireCombos(c)

  c.querySelector('[data-del]')!.addEventListener('click', (e) => {
    e.stopPropagation()
    const id = c.querySelector<HTMLInputElement>('[data-k="id"]')?.value.trim()
    mutate('remove derived source', { undoToast: `Removed derived source${id ? ` "${id}"` : ''}` })
    c.classList.add('slide-out')
    setTimeout(() => { c.remove(); syncFn() }, 250)
  })

  for (const [k, v] of Object.entries(ds)) {
    if (k === 'make_valid') continue
    const inp = c.querySelector<HTMLInputElement | HTMLSelectElement>(`[data-k="${k}"]`)
    if (inp && v != null) inp.value = String(v)
  }
  c.querySelector<HTMLInputElement>('[data-k="make_valid"]')!.checked = ds.make_valid !== false

  c.querySelectorAll('[data-k]').forEach(i => {
    i.addEventListener('input', syncFn)
    i.addEventListener('change', syncFn)
  })

  const idInp = c.querySelector<HTMLInputElement>('[data-k="id"]')!
  const fromInp = c.querySelector<HTMLInputElement>('[data-k="from"]')!
  const titleEl = c.querySelector<HTMLElement>('.item-title')!
  const updateTitle = () => {
    const id = idInp.value.trim()
    const from = fromInp.value.trim()
    titleEl.textContent = id ? `${id}${from ? ` ← ${from}` : ''}` : ''
  }
  idInp.addEventListener('input', updateTitle)
  fromInp.addEventListener('input', updateTitle)
  fromInp.addEventListener('change', updateTitle)
  updateTitle()

  wireCollapse(c, { headerSel: '.item-head', chevronSel: '.card-chevron', bodySel: '.card-content' })

  createIcons({ icons: { GitBranch, Trash2, ChevronDown } })
  return c
}

import { createIcons, Loader, Database, ChevronDown, AlertCircle, Plus } from 'lucide'
import { mkEl, val, refreshIcons } from './dom'
import { SOURCE_SCHEMAS } from './state'
import { setComboOptions, ensureComboOption, type ComboOptionDef } from './combo'
import { mapRow } from './cards/map-row'
import { showToast } from './toast'

// ---- schema badge ----
export function updateSourceBadge(card: Element, status: null | { loading?: boolean; ok?: boolean; columns?: { name: string; type: string }[]; error?: string }): void {
  const badge = card.querySelector<HTMLElement>('.schema-badge')
  const schemaList = card.querySelector<HTMLElement>('.schema-list')
  if (!badge || !schemaList) return

  if (!status) {
    badge.innerHTML = ''
    schemaList.innerHTML = ''
    schemaList.style.display = 'none'
    return
  }

  if (status.loading) {
    badge.innerHTML = `<span style="font-size:11px;color:var(--muted);display:inline-flex;align-items:center;gap:4px">
      <i data-lucide="loader" class="spin-animation" style="width:11px;height:11px"></i> inspecting…</span>`
    createIcons({ icons: { Loader } })
    return
  }

  if (status.ok && status.columns) {
    const colCount = status.columns.length
    badge.innerHTML = `<a href="#" class="schema-toggle-btn" style="font-size:11px;color:var(--ok);border:1px solid rgba(99,255,173,0.2);background:var(--ok-soft);padding:2px 6px;border-radius:4px;display:inline-flex;align-items:center;gap:4px">
      <i data-lucide="database" style="width:11px;height:11px"></i> ${colCount} cols <i data-lucide="chevron-down" style="width:10px;height:10px"></i></a>`
    schemaList.innerHTML =
      `<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 12px;max-height:120px;overflow-y:auto;padding:4px;">` +
      status.columns.map(c =>
        `<div style="display:flex;justify-content:space-between;border-bottom:1px dashed rgba(255,255,255,0.05);">` +
        `<span style="color:var(--ink)">${c.name}</span><span style="color:var(--muted);font-size:10px">${c.type}</span></div>`
      ).join('') + `</div>`

    badge.querySelector<HTMLAnchorElement>('.schema-toggle-btn')!.onclick = e => {
      e.preventDefault()
      const hidden = schemaList.style.display === 'none'
      schemaList.style.display = hidden ? 'block' : 'none'
      const chevron = badge.querySelector<HTMLElement>('.schema-toggle-btn i:last-child')
      if (chevron) chevron.style.transform = hidden ? 'rotate(180deg)' : 'none'
    }
    createIcons({ icons: { Database, ChevronDown } })
  } else {
    const msg = status.error || 'failed'
    badge.innerHTML = `<span title="${msg.replace(/"/g, '&quot;')}" style="font-size:11px;color:var(--warn);border:1px solid rgba(255,107,107,0.2);background:var(--warn-soft);padding:2px 6px;border-radius:4px;display:inline-flex;align-items:center;gap:4px;cursor:help">
      <i data-lucide="alert-circle" style="width:11px;height:11px"></i> error</span>`
    schemaList.innerHTML = `<div style="color:var(--warn);padding:4px;">${msg}</div>`
    schemaList.style.display = 'none'
    createIcons({ icons: { AlertCircle } })
  }
}

// ---- autocomplete datalists (scoped to a pipeline card) ----
export function collectSourceIdsScoped(scope: Element): string[] {
  return [...scope.querySelectorAll('.pl-sources > .card')]
    .map(c => (c.querySelector<HTMLInputElement>('[data-k="id"]')?.value ?? '').trim())
    .filter(Boolean)
}

export function collectDerivedSourceIdsScoped(scope: Element): string[] {
  return [...scope.querySelectorAll('.pl-derived-sources > .card')]
    .map(c => (c.querySelector<HTMLInputElement>('[data-k="id"]')?.value ?? '').trim())
    .filter(Boolean)
}

// snapshot steps name the running chain's state mid-pipeline; any *later*
// step can join back against that name like any other source. The combo
// options below don't enforce position (any step can pick any snapshot id) —
// picking one defined later in the list is still caught by backend/CLI
// validation with a clear error.
export function collectSnapshotIdsScoped(scope: Element): string[] {
  return [...scope.querySelectorAll('.pl-steps > .card[data-type="snapshot"]')]
    .map(c => (c.querySelector<HTMLInputElement>('[data-k="id"]')?.value ?? '').trim())
    .filter(Boolean)
}

// Real sources + derived sources + snapshots — anywhere a step's `source:`
// (or a derived_source's `from:`) can point.
export function collectAllSourceIdsScoped(scope: Element): string[] {
  return [...collectSourceIdsScoped(scope), ...collectDerivedSourceIdsScoped(scope), ...collectSnapshotIdsScoped(scope)]
}

// Resolve a source/derived_source/snapshot id to its underlying column schema
// by walking the derived_source `from` chain until a real, inspected source
// is found, or falling back to the chain's accumulated columns for a snapshot.
export function resolveSchemaScoped(scope: Element, id: string, seen = new Set<string>()): { name: string; type: string }[] {
  if (SOURCE_SCHEMAS[id]) return SOURCE_SCHEMAS[id]
  if (seen.has(id)) return []
  seen.add(id)
  const derivedCard = [...scope.querySelectorAll('.pl-derived-sources > .card')]
    .find(c => (c.querySelector<HTMLInputElement>('[data-k="id"]')?.value ?? '').trim() === id)
  const fromId = (derivedCard?.querySelector<HTMLInputElement>('[data-k="from"]')?.value ?? '').trim()
  if (fromId) return resolveSchemaScoped(scope, fromId, seen)
  if (collectSnapshotIdsScoped(scope).includes(id)) {
    return [...collectAvailableColumnsScoped(scope)].map(name => ({ name, type: '' }))
  }
  return []
}

export function collectAvailableColumnsScoped(scope: Element): Set<string> {
  const baseId = scope.querySelector<HTMLInputElement>('.pl-base')?.value
  const cols = new Set<string>()
  if (baseId && SOURCE_SCHEMAS[baseId]) SOURCE_SCHEMAS[baseId].forEach(c => cols.add(c.name))
  scope.querySelectorAll('.pl-steps [data-fields] .kv').forEach(r => {
    const out = r.querySelector<HTMLInputElement>('[data-fo]')?.value?.trim()
    if (out) cols.add(out)
  })
  return cols
}

export function updateDatalistsScoped(scope: Element): void {
  let container = scope.querySelector<HTMLElement>('.pl-datalists')
  if (!container) return
  let html = ''
  for (const [srcId, columns] of Object.entries(SOURCE_SCHEMAS)) {
    html += `<datalist id="dl-src-${scope.getAttribute('data-pl-id')}-${srcId}">`
    columns.forEach(c => { html += `<option value="${c.name}">${c.type}</option>` })
    html += '</datalist>'
  }
  const available = collectAvailableColumnsScoped(scope)
  const plId = scope.getAttribute('data-pl-id') ?? '0'
  html += `<datalist id="dl-cols-${plId}">`
  available.forEach(col => { html += `<option value="${col}">available column</option>` })
  html += '</datalist>'
  container.innerHTML = html

  const baseId = scope.querySelector<HTMLInputElement>('.pl-base')?.value || ''
  const baseCols = baseId ? (SOURCE_SCHEMAS[baseId] || []) : []

  // Build rich ComboOptionDef list for mapping columns
  const availableOptions: ComboOptionDef[] = [...available].map(col => {
    const baseCol = baseCols.find(c => c.name === col)
    if (baseCol) {
      return {
        value: col,
        label: `<span style="font-family:var(--mono);">${col}</span> <span style="float:right;font-size:10px;color:var(--muted);background:rgba(255,255,255,0.03);border:1px solid var(--line);padding:1px 4px;border-radius:3px;margin-left:8px;">${baseCol.type || 'unknown'} [base]</span>`
      }
    }

    // Find which step added this output column
    let originText = 'step'
    const steps = Array.from(scope.querySelectorAll('.pl-steps > .card'))
    for (let i = 0; i < steps.length; i++) {
      const stepCard = steps[i] as HTMLElement
      const stepType = stepCard.dataset['type'] || ''
      const outputInputs = Array.from(stepCard.querySelectorAll('[data-fields] .kv [data-fo]')) as HTMLInputElement[]
      if (outputInputs.some(oi => oi.value.trim() === col)) {
        originText = `step ${i + 1} (${stepType})`
        break
      }
    }
    return {
      value: col,
      label: `<span style="font-family:var(--mono);">${col}</span> <span style="float:right;font-size:10px;color:var(--accent);background:var(--accent-soft);border:1px solid rgba(139,108,255,0.2);padding:1px 4px;border-radius:3px;margin-left:8px;">${originText}</span>`
    }
  })

  // Render fields in the side visual schema mapper panel
  const fieldsListEl = scope.querySelector('.pl-available-fields-list')
  if (fieldsListEl) {
    fieldsListEl.innerHTML = ''
    availableOptions.forEach(opt => {
      const fieldEl = mkEl('div', { className: 'available-field-badge' })
      fieldEl.style.cssText = 'padding: 6px 10px; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-sm); font-size: 11px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; transition: all 0.2s; user-select: none;'

      fieldEl.innerHTML = `
        <span style="display: flex; align-items: center; gap: 6px; font-weight: 500;">
          <i data-lucide="plus" style="width: 11px; height: 11px; color: var(--muted); opacity: 0.7; transition: color 0.2s;"></i>
          <span style="font-family: var(--mono); color: var(--ink);">${opt.value}</span>
        </span>
        ${opt.label ? opt.label.substring(opt.label.indexOf('<span style="float:right;')) : ''}
      `

      fieldEl.addEventListener('mouseenter', () => {
        fieldEl.style.borderColor = 'var(--accent)'
        fieldEl.style.background = 'var(--accent-soft)'
        const icon = fieldEl.querySelector('i')
        if (icon) icon.style.color = 'var(--accent)'
      })
      fieldEl.addEventListener('mouseleave', () => {
        fieldEl.style.borderColor = 'var(--line)'
        fieldEl.style.background = 'var(--panel)'
        const icon = fieldEl.querySelector('i')
        if (icon) icon.style.color = 'var(--muted)'
      })
      fieldEl.addEventListener('click', () => {
        const mappingEl = scope.querySelector('.pl-mapping')!
        const mappedTo = new Set([...mappingEl.querySelectorAll('.map-item')]
          .map(w => w.querySelector<HTMLInputElement>('[data-to]')?.value.trim() || '').filter(Boolean))

        if (!mappedTo.has(opt.value)) {
          const syncCallback = (scope as any)._scopedSync || (() => {})
          mappingEl.appendChild(mapRow({ to: opt.value, from: opt.value }, syncCallback, plId))
          refreshIcons()
          syncCallback()
          showToast(`Mapped ${opt.value}`, 'ok')
        } else {
          showToast(`${opt.value} is already mapped`, 'info')
        }
      })
      fieldsListEl.appendChild(fieldEl)
    })

    if (availableOptions.length === 0) {
      fieldsListEl.innerHTML = '<div style="color:var(--muted); font-size: 11px; padding: 12px 4px; text-align: center;">No fields available. Set base source first.</div>'
    } else {
      createIcons({ icons: { Plus } })
    }
  }

  // Point all "from" inputs to the right datalist
  scope.querySelectorAll<HTMLInputElement>('[data-from-list]').forEach(inp => {
    const cur = inp.value
    setComboOptions(inp, availableOptions)
    ensureComboOption(inp, cur)
  })

  // Populate each step's source-column selects from the inspected source schema
  scope.querySelectorAll<HTMLElement>('.pl-steps .card').forEach(stepEl => {
    const srcId = (stepEl.querySelector<HTMLInputElement>('[data-k="source"]')?.value ?? '').trim()
    const cols = srcId ? resolveSchemaScoped(scope, srcId) : []
    stepEl.querySelectorAll<HTMLInputElement>('[data-fc]').forEach(sel => {
      const cur = sel.value

      const stepColOptions: ComboOptionDef[] = cols.map(c => ({
        value: c.name,
        label: `<span style="font-family:var(--mono);">${c.name}</span> <span style="float:right;font-size:10px;color:var(--muted);background:rgba(255,255,255,0.05);padding:1px 4px;border-radius:3px;margin-left:8px;">${c.type || 'unknown'}</span>`
      }))

      setComboOptions(sel, stepColOptions)
      if (cur) {
        ensureComboOption(sel, cur)
        sel.value = cur
      }
    })
  })
}

export function refreshBaseOptionsScoped(scope: Element): void {
  const ids = collectSourceIdsScoped(scope)
  const allIds = collectAllSourceIdsScoped(scope)
  const baseEl = scope.querySelector<HTMLInputElement>('.pl-base')!
  if (!baseEl) return
  const cur = baseEl.value
  setComboOptions(baseEl, ids)
  if (ids.includes(cur)) baseEl.value = cur

  scope.querySelectorAll<HTMLInputElement>('.pl-steps [data-k="source"]').forEach(s => {
    const c = s.value
    setComboOptions(s, allIds)
    if (allIds.includes(c)) s.value = c
  })

  scope.querySelectorAll<HTMLInputElement>('.pl-derived-sources [data-k="from"]').forEach(s => {
    const c = s.value
    setComboOptions(s, allIds)
    if (allIds.includes(c)) s.value = c
  })

  const snapshotIds = collectSnapshotIdsScoped(scope)
  scope.querySelectorAll<HTMLInputElement>('.pl-steps [data-k="branch"]').forEach(s => {
    const c = s.value
    setComboOptions(s, snapshotIds)
    if (snapshotIds.includes(c)) s.value = c
  })

  // Highlight base source
  const baseId = baseEl.value
  scope.querySelectorAll<HTMLElement>('.pl-sources > .card').forEach(c => {
    const id = val(c, 'id')
    const isBase = !!id && id === baseId
    c.style.borderColor = isBase ? 'rgba(139,108,255,0.6)' : 'var(--line)'
    c.style.boxShadow = isBase ? '0 0 15px rgba(139,108,255,0.15)' : 'none'
    let badge = c.querySelector<HTMLElement>('.base-badge')
    if (isBase && !badge) {
      badge = mkEl('span', { className: 'base-badge', textContent: 'BASE' })
      badge.style.cssText = 'font-size:10px;color:var(--accent);border:1px solid rgba(139,108,255,0.3);background:var(--accent-soft);padding:1px 5px;border-radius:4px;margin-left:8px;font-family:var(--mono);font-weight:600;'
      c.querySelector('.item-head')!.insertBefore(badge, c.querySelector('.spacer'))
    } else if (!isBase && badge) {
      badge.remove()
    }
  })
}

import { createIcons, Trash2, GripVertical, ChevronDown } from 'lucide'
import { mkEl, refreshIcons, esc } from '../dom'
import { mutate } from '../history'
import { META, SOURCE_SCHEMAS } from '../state'
import { comboField, wireCombos, type ComboOptionDef } from '../combo'
import { collectAvailableColumnsScoped, collectAvailableColumnDetailsScoped } from '../schema'
import { openCodelistDrawer } from './codelist'
import { openExprDrawer } from '../expr-drawer'
import { collectPipelineDef } from './pipeline-card'
import type { CodeList, Config, MapItem } from '../types'

// ---- map row ----
const FUNC_DESCRIPTIONS: Record<string, string> = {
  uuid: 'Generates a unique UUIDv4 per row',
  now: 'Current date and time timestamp',
  today: 'Current date',
  lon: 'Centroid longitude in EPSG:4326',
  lat: 'Centroid latitude in EPSG:4326',
  mgrs: 'MGRS spatial coordinate string',
  wkb: 'Well-Known Binary geometry format',
  area: 'Calculated area in metric units',
  length: 'Calculated perimeter/length in metric units'
}

export interface MapRowElement extends HTMLElement {
  _readMapping(): MapItem | null
}

export function mapRow(m: Partial<MapItem> = {}, syncFn: () => void, _plId = '0'): MapRowElement {
  const wrap = mkEl('div', { className: 'map-item' }) as unknown as MapRowElement
  const row = mkEl('div', { className: 'map-grid' })
  row.style.marginTop = '6px'

  const kind = m.from !== undefined ? 'from' : m.const !== undefined ? 'const'
    : m.expr !== undefined ? 'expr' : m.func !== undefined ? 'func'
    : m.codelist !== undefined ? 'codelist' : 'from'
  const initVal = m.from ?? m.const ?? m.expr ?? m.func ?? ''

  row.innerHTML = `
    <span class="drag-handle"><i data-lucide="grip-vertical" style="width:14px;height:14px"></i></span>
    <input data-to placeholder="column" value="${esc(m.to)}">
    ${comboField('data-kind', kind, ['from', 'const', 'expr', 'func', 'codelist'])}
    <span data-valwrap></span>
    ${comboField('data-cast', m.cast ? m.cast.toUpperCase() : '', ['INTEGER', 'DOUBLE', 'VARCHAR', 'BOOLEAN', 'DATE', 'TIMESTAMP'], '— cast —')}
    <button class="mini danger ghost" data-del aria-label="Remove this output column"><i data-lucide="trash-2" style="width:12px;height:12px"></i></button>`

  const KIND_VALUES = ['from', 'const', 'expr', 'func', 'codelist'] as const
  const normalizeKind = (v: string): typeof KIND_VALUES[number] =>
    (KIND_VALUES as readonly string[]).includes(v) ? v as typeof KIND_VALUES[number] : 'from'

  const kindSel = row.querySelector<HTMLInputElement>('[data-kind]')!
  const valWrap = row.querySelector<HTMLElement>('[data-valwrap]')!
  wireCombos(row)

  let panel: HTMLElement | null = null
  let currentCodelist: Partial<CodeList> = typeof m.codelist === 'object' && m.codelist ? m.codelist : {}
  // A single-line <input> silently strips \n from its value (the HTML value-sanitization
  // algorithm for text controls), so a multi-line expression can't live in the row's inline
  // field without losing its formatting. The real value lives here instead; the inline
  // input only ever shows a newline-free preview of it — see exprPreviewText() below.
  let currentExprValue: string = typeof m.expr === 'string' ? m.expr : ''
  const exprPreviewText = (v: string) => v.includes('\n') ? v.replace(/\s+/g, ' ').trim() : v

  const renderVal = () => {
    valWrap.innerHTML = ''
    if (panel) { panel.remove(); panel = null }
    wrap.classList.remove('has-detail')

    const k = normalizeKind(kindSel.value)
    if (k === 'func') {
      const FUNC_CATEGORIES: Record<string, { name: string, color: string, bg: string }> = {
        uuid: { name: 'ID', color: 'var(--accent)', bg: 'var(--accent-soft)' },
        now: { name: 'TIME', color: 'var(--accent)', bg: 'var(--accent-soft)' },
        today: { name: 'TIME', color: 'var(--accent)', bg: 'var(--accent-soft)' },
        lon: { name: 'COORD', color: '#00ebd7', bg: 'rgba(0,235,215,0.08)' },
        lat: { name: 'COORD', color: '#00ebd7', bg: 'rgba(0,235,215,0.08)' },
        mgrs: { name: 'COORD', color: '#00ebd7', bg: 'rgba(0,235,215,0.08)' },
        wkb: { name: 'GEOM', color: '#00ebd7', bg: 'rgba(0,235,215,0.08)' },
        area: { name: 'MEAS', color: 'var(--ok)', bg: 'var(--ok-soft)' },
        length: { name: 'CALC', color: 'var(--ok)', bg: 'var(--ok-soft)' }
      }

      const wrapperDiv = mkEl('div')
      const funcOptions = META.funcs.map(f => {
        const cat = FUNC_CATEGORIES[f] || { name: 'FUNC', color: 'var(--muted)', bg: 'rgba(255,255,255,0.05)' }
        const catTag = `<span style="color:${cat.color}; background:${cat.bg}; font-size:8.5px; font-weight:600; padding:1px 4px; border-radius:3px; margin-right:8px; font-family:var(--display);">${cat.name}</span>`
        return {
          value: f,
          label: `${catTag}<span style="font-family:var(--mono);font-weight:600;">${f}</span> <span style="float:right;font-size:10px;color:var(--muted);margin-left:12px;">${FUNC_DESCRIPTIONS[f] || ''}</span>`
        }
      })
      wrapperDiv.innerHTML = comboField('', META.funcs.includes(String(initVal)) ? String(initVal) : '', funcOptions, 'select function')
      const comboEl = wrapperDiv.firstElementChild as HTMLElement
      const inp = comboEl.querySelector<HTMLInputElement>('input')!

      const updateHelp = () => {
        const val = inp.value.trim()
        const desc = FUNC_DESCRIPTIONS[val]
        inp.title = desc || 'Select function'
      }

      inp.addEventListener('input', () => { syncFn(); updateHelp() })
      inp.addEventListener('change', () => { syncFn(); updateHelp() })
      setTimeout(updateHelp, 0)

      valWrap.appendChild(comboEl)
      wireCombos(valWrap)
      refreshIcons()
    } else if (k === 'codelist') {
      const btn = mkEl('button', { className: 'mini ghost', type: 'button' })
      btn.innerHTML = `<i data-lucide="sliders" style="width:12px;height:12px;color:var(--accent)"></i> configure rules`
      btn.onclick = e => {
        e.preventDefault()
        const colName = row.querySelector<HTMLInputElement>('[data-to]')!.value.trim() || 'unnamed_column'
        openCodelistDrawer(colName, currentCodelist, updatedCodelist => {
          currentCodelist = updatedCodelist
          syncFn()
        })
      }
      valWrap.appendChild(btn)
      refreshIcons()
    } else if (k === 'from') {
      const wrapperDiv = mkEl('div')
      wrapperDiv.innerHTML = comboField('data-from-list="1"', (typeof initVal === 'string' || typeof initVal === 'number') ? String(initVal) : '', [], 'source_column')
      const comboEl = wrapperDiv.firstElementChild as HTMLElement
      const inp = comboEl.querySelector<HTMLInputElement>('input')!
      inp.classList.add('has-badge')

      const badgeContainer = mkEl('div', { className: 'pl-origin-badge-container' })
      comboEl.appendChild(badgeContainer)

      const updateRowBadge = () => {
        const val = inp.value.trim()
        badgeContainer.innerHTML = ''
        if (!val) return

        const card = row.closest('.pipeline-card')
        if (!card) return
        const baseId = card.querySelector<HTMLInputElement>('.pl-base')?.value || ''
        const baseCols = baseId ? (SOURCE_SCHEMAS[baseId] || []) : []
        const baseCol = baseCols.find(c => c.name === val)

        if (baseCol) {
          badgeContainer.innerHTML = `<span>base</span>`
        } else {
          let originText = ''
          const steps = Array.from(card.querySelectorAll('.pl-steps > .card'))
          for (let i = 0; i < steps.length; i++) {
            const stepCard = steps[i] as HTMLElement
            const outputInputs = Array.from(stepCard.querySelectorAll('[data-fields] .kv [data-fo]')) as HTMLInputElement[]
            if (outputInputs.some(oi => oi.value.trim() === val)) {
              originText = `step ${i + 1}`
              break
            }
          }
          if (originText) {
            badgeContainer.innerHTML = `<span style="color:var(--accent)">${originText}</span>`
          }
        }
      }

      inp.addEventListener('input', () => { syncFn(); updateRowBadge() })
      inp.addEventListener('change', () => { syncFn(); updateRowBadge() })
      setTimeout(updateRowBadge, 0)

      valWrap.appendChild(comboEl)
      wireCombos(valWrap)
      refreshIcons()
    } else {
      if (k === 'expr') {
        const wrapperDiv = mkEl('div')
        const card = row.closest('.pipeline-card')
        const available = card ? [...collectAvailableColumnsScoped(card)] : []
        const availableOptions: ComboOptionDef[] = available.map(col => ({
          value: col,
          label: `<span style="font-family:var(--mono);">${esc(col)}</span> <span style="float:right;font-size:10px;color:var(--muted);">column</span>`
        }))
        wrapperDiv.innerHTML = comboField('', exprPreviewText(currentExprValue), availableOptions, 'SQL expression (e.g. col1 + col2)')
        const comboEl = wrapperDiv.firstElementChild as HTMLElement
        const inp = comboEl.querySelector<HTMLInputElement>('input')!
        inp.classList.add('has-badge')

        const syncReadonly = () => {
          const multiLine = currentExprValue.includes('\n')
          inp.readOnly = multiLine
          inp.title = multiLine ? 'Multi-line expression — use the edit icon to change it' : ''
        }
        syncReadonly()

        const editBtn = mkEl('button', { className: 'mini ghost expr-edit-btn', type: 'button' })
        editBtn.title = 'Open SQL Expression Builder'
        editBtn.style.cssText = 'position: absolute; right: 28px; top: 50%; transform: translateY(-50%); padding: 4px; z-index: 5; background: transparent; border: none; height: 18px; width: 18px; min-width: 18px; display: flex; align-items: center; justify-content: center; cursor: pointer;'
        editBtn.innerHTML = '<i data-lucide="edit-3" style="width: 11px; height: 11px; color: var(--accent);"></i>'
        comboEl.appendChild(editBtn)

        editBtn.addEventListener('click', e => {
          e.preventDefault()
          e.stopPropagation()
          const availableDetails = card ? collectAvailableColumnDetailsScoped(card) : []
          // Builds a throwaway single-column preview config from the pipeline's real
          // sources/steps as currently drafted in the form, so the drawer can validate the
          // expression against real sample data via /api/preview without a dedicated
          // backend endpoint.
          const buildPreviewConfig = card ? (draftExpr: string): Config | null => {
            const pdef = collectPipelineDef(card as HTMLElement)
            if (!pdef.base) return null
            return {
              name: 'expr_preview',
              output: 'preview.gpkg',
              pipelines: [{
                ...pdef,
                mapping: [{ to: '_expr_preview', expr: draftExpr }],
                layers: [{ layer: 'preview', crs: pdef.working_crs || 'EPSG:25833' }],
              }],
            }
          } : undefined
          openExprDrawer(currentExprValue, availableDetails, val => {
            currentExprValue = val
            inp.value = exprPreviewText(currentExprValue)
            syncReadonly()
            syncFn()
          }, buildPreviewConfig)
        })

        inp.addEventListener('input', () => {
          if (inp.readOnly) return
          currentExprValue = inp.value
          syncFn()
        })
        inp.addEventListener('change', () => {
          if (inp.readOnly) return
          currentExprValue = inp.value
          syncFn()
        })
        valWrap.appendChild(comboEl)
        wireCombos(valWrap)
        refreshIcons()
      } else {
        const inp = mkEl('input')
        inp.placeholder = 'literal value'
        inp.value = (typeof initVal === 'string' || typeof initVal === 'number') ? String(initVal) : ''
        inp.addEventListener('input', syncFn)
        valWrap.appendChild(inp)
      }
    }
  }
  renderVal()
  kindSel.addEventListener('input', () => { renderVal(); syncFn() })
  kindSel.addEventListener('change', () => { renderVal(); syncFn() })

  row.querySelector('[data-del]')!.addEventListener('click', () => {
    const to = row.querySelector<HTMLInputElement>('[data-to]')?.value.trim()
    mutate('remove mapping column', { undoToast: `Removed column${to ? ` "${to}"` : ''}` })
    wrap.classList.add('slide-out'); setTimeout(() => { wrap.remove(); syncFn() }, 250)
  })
  row.querySelectorAll('[data-to]').forEach(i => i.addEventListener('input', syncFn))
  row.querySelector('[data-cast]')!.addEventListener('input', syncFn)
  row.querySelector('[data-cast]')!.addEventListener('change', syncFn)
  wrap.appendChild(row)

  const handle = row.querySelector<HTMLElement>('.drag-handle')!
  handle.addEventListener('mousedown', () => { wrap.draggable = true })
  wrap.addEventListener('dragstart', () => { wrap.classList.add('dragging') })
  wrap.addEventListener('dragend', () => {
    wrap.draggable = false
    wrap.classList.remove('dragging')
    wrap.parentElement?.querySelectorAll('.drop-target-above, .drop-target-below')
      .forEach(el => el.classList.remove('drop-target-above', 'drop-target-below'))
  })

  // Mapping order is meaningful (it's the output column order), and drag was the only way
  // to change it. Alt+Arrow gives the same move without a mouse.
  handle.tabIndex = 0
  handle.setAttribute('role', 'button')
  handle.setAttribute('aria-label', 'Reorder this column — hold Alt and press the up or down arrow')
  wrap.addEventListener('keydown', e => {
    if (!e.altKey) return
    const sib = e.key === 'ArrowUp' ? wrap.previousElementSibling
      : e.key === 'ArrowDown' ? wrap.nextElementSibling : null
    if (!sib) return
    e.preventDefault()
    mutate(e.key === 'ArrowUp' ? 'move column up' : 'move column down')
    if (e.key === 'ArrowUp') wrap.parentNode!.insertBefore(wrap, sib)
    else wrap.parentNode!.insertBefore(sib, wrap)
    handle.focus()
    syncFn()
  })

  wrap._readMapping = () => {
    const to = row.querySelector<HTMLInputElement>('[data-to]')!.value.trim()
    if (!to) return null
    const k = normalizeKind(kindSel.value)
    const out: MapItem = { to }
    if (k === 'func') {
      const inp = valWrap.querySelector('input')
      out.func = (inp ? inp.value : '') as MapItem['func']
    } else if (k === 'codelist') {
      out.codelist = currentCodelist as CodeList
    } else if (k === 'expr') {
      out.expr = currentExprValue
    } else {
      const inp = valWrap.querySelector<HTMLInputElement>('input')
      ;(out as unknown as Record<string, unknown>)[k] = inp ? inp.value : ''
    }
    const cast = row.querySelector<HTMLInputElement>('[data-cast]')!.value
    if (cast) out.cast = cast
    return out
  }

  createIcons({ icons: { Trash2, GripVertical, ChevronDown } })
  return wrap
}

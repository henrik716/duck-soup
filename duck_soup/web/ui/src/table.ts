import { qs } from './dom'
import { showToast } from './toast'

// ---- table ----
let currentPreviewRows: Record<string, unknown>[] = []
let tableSortCol: string | null = null
let tableSortAsc = true
let tableSearchQuery = ''
let listenersInitialized = false

function getFilteredAndSortedRows(): Record<string, unknown>[] {
  let result = [...currentPreviewRows]
  if (tableSearchQuery) {
    result = result.filter(row => {
      return Object.entries(row)
        .filter(([k]) => k !== '__geojson' && k !== 'geom')
        .some(([, v]) => v !== null && v !== undefined && String(v).toLowerCase().includes(tableSearchQuery))
    })
  }
  if (tableSortCol) {
    const col = tableSortCol
    const asc = tableSortAsc
    result.sort((a, b) => {
      const valA = a[col]
      const valB = b[col]
      if (valA === null || valA === undefined) return asc ? 1 : -1
      if (valB === null || valB === undefined) return asc ? -1 : 1
      if (typeof valA === 'number' && typeof valB === 'number') {
        return asc ? valA - valB : valB - valA
      }
      const strA = String(valA).toLowerCase()
      const strB = String(valB).toLowerCase()
      if (strA < strB) return asc ? -1 : 1
      if (strA > strB) return asc ? 1 : -1
      return 0
    })
  }
  return result
}

function exportToCSV(rows: Record<string, unknown>[], filename = 'preview.csv'): void {
  if (!rows || !rows.length) return
  const cols = Object.keys(rows[0]).filter(k => k !== '__geojson' && k !== 'geom')
  const header = cols.join(',')
  const body = rows.map(row =>
    cols.map(col => {
      const val = row[col] === null || row[col] === undefined ? '' : String(row[col])
      if (val.includes(',') || val.includes('"') || val.includes('\n')) {
        return `"${val.replace(/"/g, '""')}"`
      }
      return val
    }).join(',')
  ).join('\n')
  const blob = new Blob([header + '\n' + body], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', filename)
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

function renderTableFilteredSorted(): void {
  const container = qs<HTMLElement>('#preview-table-container')
  if (!container) return
  const filteredSortedRows = getFilteredAndSortedRows()
  if (filteredSortedRows.length === 0) {
    container.innerHTML = '<div style="color:var(--muted);font-size:12px;padding:12px;">No matching rows</div>'
    return
  }
  const cols = Object.keys(currentPreviewRows[0]).filter(k => k !== '__geojson' && k !== 'geom')
  let html = `<table style="width:100%;border-collapse:collapse;font-family:var(--mono);font-size:11px;color:var(--ink);">`
  html += `<thead style="background:var(--panel2);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:1;"><tr>`
  cols.forEach(c => {
    let indicator = ''
    let sortClass = 'sortable'
    if (tableSortCol === c) {
      indicator = tableSortAsc ? ' ▲' : ' ▼'
      sortClass += tableSortAsc ? ' sorted-asc' : ' sorted-desc'
    }
    html += `<th class="${sortClass}" data-col="${c}" style="padding:10px 12px;text-align:left;border-right:1px solid var(--line);font-weight:600;">` +
            `${c}<span class="sort-indicator">${indicator}</span></th>`
  })
  html += `</tr></thead><tbody>`
  filteredSortedRows.forEach((row, i) => {
    html += `<tr style="border-bottom:1px solid var(--line);${i % 2 === 1 ? 'background:rgba(255,255,255,0.01);' : ''}">`
    cols.forEach(c => {
      const v = row[c] !== null && row[c] !== undefined ? row[c] : 'NULL'
      html += `<td style="padding:8px 12px;border-right:1px solid var(--line);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px;" title="${String(v).replace(/"/g, '&quot;')}">${v}</td>`
    })
    html += `</tr>`
  })
  html += `</tbody></table>`
  container.innerHTML = html

  container.querySelectorAll('th.sortable').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.getAttribute('data-col')
      if (!col) return
      if (tableSortCol === col) {
        tableSortAsc = !tableSortAsc
      } else {
        tableSortCol = col
        tableSortAsc = true
      }
      renderTableFilteredSorted()
    })
  })
}

export function showTableError(message: string): void {
  const container = qs<HTMLElement>('#preview-table-container')
  if (!container) return
  container.innerHTML = `<div style="color:var(--warn);font-size:12px;padding:12px;">Preview failed: ${
    message.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }</div>`
}

export function updateTable(rows: Record<string, unknown>[]): void {
  const container = qs<HTMLElement>('#preview-table-container')
  if (!container) return
  if (!rows || rows.length === 0) {
    container.innerHTML = '<div style="color:var(--muted);font-size:12px;padding:12px;">No rows returned</div>'
    return
  }

  currentPreviewRows = rows
  const searchInp = document.getElementById('tableSearch') as HTMLInputElement | null
  if (searchInp) {
    searchInp.value = ''
    tableSearchQuery = ''
  }
  tableSortCol = null
  tableSortAsc = true

  if (!listenersInitialized) {
    if (searchInp) {
      searchInp.addEventListener('input', () => {
        tableSearchQuery = searchInp.value.trim().toLowerCase()
        renderTableFilteredSorted()
      })
    }
    const exportBtn = document.getElementById('tableExportBtn') as HTMLButtonElement | null
    if (exportBtn) {
      exportBtn.addEventListener('click', () => {
        const filtered = getFilteredAndSortedRows()
        exportToCSV(filtered, 'flow_preview_export.csv')
        showToast('CSV exported successfully', 'ok')
      })
    }
    listenersInitialized = true
  }

  renderTableFilteredSorted()
}

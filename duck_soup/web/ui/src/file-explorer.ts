import { createIcons, Folder, ArrowLeft, File } from 'lucide'
import { fetchFiles } from './api'
import { qs, mkEl, esc } from './dom'
import { openOverlay, closeOverlay } from './overlay'

// ---- file explorer ----
let activeExplorerTarget: HTMLInputElement | null = null

export function openFileExplorer(input: HTMLInputElement): void {
  activeExplorerTarget = input
  const modal = qs<HTMLElement>('#fileModal')
  modal?.classList.add('show')
  if (modal) openOverlay(modal, closeFileExplorer)
  loadFileExplorerDir('')
}

export function closeFileExplorer(): void {
  const modal = qs<HTMLElement>('#fileModal')
  modal?.classList.remove('show')
  if (modal) closeOverlay(modal)
  activeExplorerTarget = null
}

export async function loadFileExplorerDir(subpath: string): Promise<void> {
  const listEl = qs<HTMLElement>('#fileExplorerList')
  const pathEl = qs<HTMLElement>('#fileExplorerPath')
  if (!listEl || !pathEl) return
  listEl.innerHTML = '<div style="color:var(--muted);font-size:13px;font-family:var(--mono);">loading…</div>'
  pathEl.textContent = subpath || '/'

  // Rows are divs, so they need explicit keyboard affordances to be reachable at all.
  const makeRow = (className: string, html: string, activate: () => void): HTMLElement => {
    const row = mkEl('div', { className })
    row.innerHTML = html
    row.tabIndex = 0
    row.setAttribute('role', 'button')
    row.onclick = activate
    row.onkeydown = e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate() }
    }
    return row
  }

  try {
    const data = await fetchFiles(subpath)
    listEl.innerHTML = ''

    if (data.parent !== null) {
      listEl.appendChild(makeRow(
        'explorer-item directory',
        `<i data-lucide="arrow-left" style="width:14px;height:14px"></i> <span>.. (parent directory)</span>`,
        () => loadFileExplorerDir(data.parent as string),
      ))
    }

    data.entries.forEach((entry: { name: string; path: string; is_dir: boolean }) => {
      // A File Geodatabase (.gdb) is itself a directory, but it's a leaf source, not
      // something to browse into — the only way to pick one at all is to select the
      // folder itself, same as a regular file.
      const isGdb = entry.is_dir && entry.name.toLowerCase().endsWith('.gdb')
      const selectAsSource = () => {
        if (activeExplorerTarget) {
          activeExplorerTarget.value = entry.path
          activeExplorerTarget.dispatchEvent(new Event('input', { bubbles: true }))
        }
        closeFileExplorer()
      }
      const activate = entry.is_dir && !isGdb
        ? () => loadFileExplorerDir(entry.path)
        : selectAsSource
      listEl.appendChild(makeRow(
        `explorer-item ${entry.is_dir ? 'directory' : 'file'}`,
        `<i data-lucide="${entry.is_dir ? 'folder' : 'file'}" style="width:14px;height:14px"></i> <span>${esc(entry.name)}</span>`,
        activate,
      ))
    })

    createIcons({ icons: { Folder, ArrowLeft, File } })
  } catch (e) {
    listEl.innerHTML = `<div style="color:var(--warn);font-size:13px;font-family:var(--mono);">Error: ${esc((e as Error).message)}</div>`
  }
}

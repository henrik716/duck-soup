import { createIcons, Folder, ArrowLeft, File } from 'lucide'
import { fetchFiles } from './api'
import { qs, mkEl } from './dom'

// ---- file explorer ----
let activeExplorerTarget: HTMLInputElement | null = null

export function openFileExplorer(input: HTMLInputElement): void {
  activeExplorerTarget = input
  qs('#fileModal')?.classList.add('show')
  loadFileExplorerDir('')
}

export function closeFileExplorer(): void {
  qs('#fileModal')?.classList.remove('show')
  activeExplorerTarget = null
}

export async function loadFileExplorerDir(subpath: string): Promise<void> {
  const listEl = qs<HTMLElement>('#fileExplorerList')
  const pathEl = qs<HTMLElement>('#fileExplorerPath')
  if (!listEl || !pathEl) return
  listEl.innerHTML = '<div style="color:var(--muted);font-size:13px;font-family:var(--mono);">loading…</div>'
  pathEl.textContent = subpath || '/'

  try {
    const data = await fetchFiles(subpath)
    listEl.innerHTML = ''

    if (data.parent !== null) {
      const row = mkEl('div', { className: 'explorer-item directory' })
      row.innerHTML = `<i data-lucide="arrow-left" style="width:14px;height:14px"></i> <span>.. (parent directory)</span>`
      row.onclick = () => loadFileExplorerDir(data.parent as string)
      listEl.appendChild(row)
    }

    data.entries.forEach((entry: { name: string; path: string; is_dir: boolean }) => {
      const row = mkEl('div', { className: `explorer-item ${entry.is_dir ? 'directory' : 'file'}` })
      row.innerHTML = `<i data-lucide="${entry.is_dir ? 'folder' : 'file'}" style="width:14px;height:14px"></i> <span>${entry.name}</span>`
      if (entry.is_dir) {
        row.onclick = () => loadFileExplorerDir(entry.path)
      } else {
        row.onclick = () => {
          if (activeExplorerTarget) {
            activeExplorerTarget.value = entry.path
            activeExplorerTarget.dispatchEvent(new Event('input', { bubbles: true }))
          }
          closeFileExplorer()
        }
      }
      listEl.appendChild(row)
    })

    createIcons({ icons: { Folder, ArrowLeft, File } })
  } catch (e) {
    listEl.innerHTML = `<div style="color:var(--warn);font-size:13px;font-family:var(--mono);">Error: ${(e as Error).message}</div>`
  }
}

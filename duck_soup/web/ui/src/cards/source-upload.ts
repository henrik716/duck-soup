import { uploadFile } from '../api'
import { mkEl, refreshIcons } from '../dom'
import { EXT_FORMAT } from '../state'
import { showToast } from '../toast'
import type { Source } from '../types'

// ---- source upload: the sources panel's dropzone ----
// Turns dropped or browsed files into source cards. A .gdb folder is uploaded file-by-file
// and becomes a single fgdb source; a .shp and its sidecars upload together but yield one
// source. Files are uploaded to the server (see /api/upload), which reports where they
// landed; that path becomes the new source's uri.

// ESRI shapefile sidecar extensions that should be uploaded alongside a .shp
// but not surface as their own source card.
const SHAPEFILE_SIDECAR_EXTS = new Set([
  'dbf', 'shx', 'prj', 'cpg', 'sbn', 'sbx', 'xml', 'qmd', 'ain', 'aih', 'atx', 'ixs', 'mxs', 'qix',
])

export function splitExt(name: string): { base: string; ext: string } {
  const dot = name.lastIndexOf('.')
  if (dot <= 0) return { base: name, ext: '' }
  return { base: name.substring(0, dot), ext: name.substring(dot + 1).toLowerCase() }
}

/** A source id from a file name: its base name, lowercased, anything but [a-z0-9_] as `_`. */
export function sourceIdFromName(base: string): string {
  return base.toLowerCase().replace(/[^a-z0-9_]/g, '_')
}

export type ShpGroup = { shpFile: File; sidecars: File[] }

// Extensions/bare names that only ever occur *inside* a File Geodatabase folder, never as
// a standalone file. If someone opens the .gdb folder in their file manager and drags its
// contents (rather than the folder itself) onto the dropzone, the browser hands over real,
// individually-readable files — there's no folder-drop failure to detect here, they're
// legitimate File objects — but uploading them flat, one "source" per file, produces a pile
// of broken sources GDAL can't open (and silently risks missing whatever hidden index/lock
// files the file manager didn't show in the selection). Caught here and rejected with
// guidance instead, so the .gdb folder itself is dragged over — see isGdbInternalFile.
const GDB_INTERNAL_EXTS = new Set(['gdbtable', 'gdbtablx', 'gdbindexes', 'spx', 'freelist', 'horizon'])
const GDB_INTERNAL_NAMES = new Set(['gdb', 'timestamps'])
// FileGDB's system catalog tables (which layers/relationships/domains exist, etc., as
// opposed to a user feature class's own data) are named "a" + 8 hex digits, optionally
// followed by a descriptive suffix — e.g. "a00000004.CatItemsByType.atx", "a00000009.spx".
// Their own extension (.atx, .spx) is too generic to key off alone — .atx is also a
// legitimate standalone shapefile sidecar — but this filename shape is distinctive to a gdb.
const GDB_SYSTEM_TABLE_RE = /^a[0-9a-f]{8}(\.|$)/i

export function isGdbInternalFile(name: string): boolean {
  const { base, ext } = splitExt(name)
  if (GDB_INTERNAL_EXTS.has(ext)) return true
  if (ext === '' && GDB_INTERNAL_NAMES.has(base.toLowerCase())) return true
  return GDB_SYSTEM_TABLE_RE.test(name)
}

// One file inside a dropped .gdb folder, with its path relative to the folder's root
// (e.g. "a00000001.gdbtable") — what the backend needs to reconstruct the folder.
type GdbFileEntry = { file: File; relPath: string }

// FileSystemDirectoryReader.readEntries() only returns a batch at a time and must be
// called repeatedly until it returns an empty array to get the rest (per spec — some
// implementations cap a single batch well below a real File Geodatabase's file count).
function readAllDirectoryEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => {
    const all: FileSystemEntry[] = []
    const readBatch = () => {
      reader.readEntries(entries => {
        if (entries.length === 0) { resolve(all); return }
        all.push(...entries)
        readBatch()
      }, reject)
    }
    readBatch()
  })
}

// Recursively collects every file under a dropped directory entry (a .gdb folder can itself
// only contain files, but this walks nested directories too in case a driver ever nests one).
async function walkGdbEntry(entry: FileSystemEntry, relPrefix: string, out: GdbFileEntry[]): Promise<void> {
  if (entry.isDirectory) {
    const children = await readAllDirectoryEntries((entry as FileSystemDirectoryEntry).createReader())
    for (const child of children) {
      await walkGdbEntry(child, relPrefix ? `${relPrefix}/${child.name}` : child.name, out)
    }
  } else {
    const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject))
    out.push({ file, relPath: relPrefix })
  }
}

// Groups a dropped/selected FileList so a .shp plus its same-basename
// sidecars (dbf/shx/prj/...) become one unit; everything else passes
// through unchanged, one file per unit. Windows/the browser hand over a
// shapefile's sidecar files together on drag-select, and without this
// grouping each one used to become its own source with the same id,
// silently clobbering each other in engine.py's CREATE OR REPLACE VIEW.
export function groupUploadItems(files: FileList | File[]): (File | ShpGroup)[] {
  const list = Array.from(files)
  const byBase = new Map<string, File[]>()
  for (const f of list) {
    const lbase = splitExt(f.name).base.toLowerCase()
    const arr = byBase.get(lbase) ?? []
    arr.push(f)
    byBase.set(lbase, arr)
  }

  const items: (File | ShpGroup)[] = []
  for (const f of list) {
    const { base, ext } = splitExt(f.name)
    const lbase = base.toLowerCase()
    if (ext === 'shp') {
      const sidecars = (byBase.get(lbase) ?? []).filter(
        g => g !== f && SHAPEFILE_SIDECAR_EXTS.has(splitExt(g.name).ext)
      )
      items.push({ shpFile: f, sidecars })
    } else if (SHAPEFILE_SIDECAR_EXTS.has(ext) && (byBase.get(lbase) ?? []).some(g => splitExt(g.name).ext === 'shp')) {
      continue // folded into its .shp's group above
    } else {
      items.push(f)
    }
  }
  return items
}

/** The uploaded .gdb folder's own path, from where one of its files landed (`uploaded`,
 *  as the server reports it) and that file's path inside the folder (`relPath`). The
 *  server decides the real upload dir, so the source's uri has to come from what it
 *  reports rather than being guessed on the client. */
export function gdbRootFromUpload(uploaded: string, relPath: string): string {
  const sep = uploaded.includes('\\') ? '\\' : '/'
  const segs = uploaded.split(sep)
  return segs.slice(0, segs.length - relPath.split('/').length).join(sep)
}

/** Adds a source card for `src` (see the pipeline card's addSource), as one undo step. */
export type AddSource = (src: Partial<Source>, action: string) => HTMLElement

export function wireSourceUpload(card: HTMLElement, addSource: AddSource): void {
  const dropzone = card.querySelector<HTMLElement>('.sources-dropzone')!
  const titleEl = dropzone.querySelector('.title')
  const fileInp = mkEl('input', { type: 'file', multiple: true }) as HTMLInputElement
  fileInp.style.display = 'none'
  dropzone.appendChild(fileInp)

  dropzone.addEventListener('click', e => {
    if (e.target === fileInp) return
    fileInp.click()
  })
  dropzone.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInp.click() }
  })

  const setProgress = (text: string) => { if (titleEl) titleEl.textContent = text }

  // Swap the dropzone's icon. By [data-lucide], not as an <i>: createIcons has already
  // replaced the <i> with an <svg> (keeping data-lucide and the classes), and does again here.
  const setIcon = (name: 'loader' | 'upload-cloud') => {
    const icon = dropzone.querySelector('[data-lucide]')
    if (!icon) return
    icon.setAttribute('data-lucide', name)
    icon.classList.toggle('spin-animation', name === 'loader')
    refreshIcons()
  }

  // The dropzone's spinner and progress text while `work` uploads, put back afterwards
  // however it ends.
  const whileUploading = async (work: () => Promise<void>) => {
    const originalText = titleEl?.textContent
    setIcon('loader')
    try {
      await work()
    } finally {
      setIcon('upload-cloud')
      if (originalText) setProgress(originalText)
    }
  }

  const added = (src: Partial<Source>, label: string) => {
    addSource(src, 'add uploaded source')
    showToast(`Uploaded and added: ${label}`, 'ok')
  }

  const uploadFiles = async (files: FileList | File[]) => {
    const all = Array.from(files)
    const gdbLike = all.filter(f => isGdbInternalFile(f.name))
    const rest = all.filter(f => !isGdbInternalFile(f.name))
    if (gdbLike.length > 0) {
      const sample = gdbLike.slice(0, 3).map(f => f.name).join(', ')
      showToast(
        `Skipped ${gdbLike.length} file${gdbLike.length > 1 ? 's' : ''} that look like the ` +
        `inside of a File Geodatabase (${sample}${gdbLike.length > 3 ? ', …' : ''}) — drag the ` +
        `whole .gdb folder onto this zone instead of its individual files.`,
        'bad',
      )
    }
    if (rest.length === 0) return

    await whileUploading(async () => {
      for (const item of groupUploadItems(rest)) {
        if (item instanceof File) {
          const file = item
          setProgress(`Uploading ${file.name}...`)
          try {
            const res = await uploadFile(file)
            if (res.ok && res.path) {
              const { base, ext } = splitExt(file.name)
              const format = (EXT_FORMAT[ext] || 'geojson') as Source['format']
              added({ id: sourceIdFromName(base), format, uri: res.path }, file.name)
            } else {
              showToast(`Upload failed: ${res.error || 'unknown error'}`, 'bad')
            }
          } catch (err) {
            showToast(`Upload failed: ${(err as Error).message}`, 'bad')
          }
          continue
        }

        // Shapefile group: the .shp plus its same-basename sidecars all need to
        // land on disk together (GDAL reads .dbf/.shx next to the .shp), but
        // only the .shp becomes a source.
        const { shpFile, sidecars } = item
        try {
          setProgress(`Uploading ${shpFile.name}...`)
          const shpRes = await uploadFile(shpFile)
          if (!shpRes.ok || !shpRes.path) {
            showToast(`Upload failed: ${shpFile.name}: ${shpRes.error || 'unknown error'}`, 'bad')
            continue
          }
          for (const sc of sidecars) {
            setProgress(`Uploading ${sc.name}...`)
            const scRes = await uploadFile(sc)
            if (!scRes.ok || !scRes.path) {
              showToast(`Upload failed: ${sc.name}: ${scRes.error || 'unknown error'}`, 'bad')
            }
          }
          added({ id: sourceIdFromName(splitExt(shpFile.name).base), format: 'shp', uri: shpRes.path }, shpFile.name)
        } catch (err) {
          showToast(`Upload failed: ${shpFile.name}: ${(err as Error).message}`, 'bad')
        }
      }
    })
  }

  // Uploads every internal file of a dropped .gdb folder (a File Geodatabase is a directory
  // of many small files — a00000001.gdbtable, timestamps, gdb, ...), preserving their relative
  // paths under the server's upload dir so GDAL's OpenFileGDB driver can open the reconstructed
  // folder afterwards, then wires up exactly one fgdb source pointing at it.
  const uploadGdbFolder = async (name: string, entries: GdbFileEntry[]) => {
    if (entries.length === 0) {
      showToast(`"${name}" is empty — nothing to upload`, 'bad')
      return
    }
    await whileUploading(async () => {
      let gdbRootPath: string | null = null
      for (let i = 0; i < entries.length; i++) {
        const { file, relPath } = entries[i]
        setProgress(`Uploading ${name} (${i + 1}/${entries.length})...`)
        try {
          const res = await uploadFile(file, `${name}/${relPath}`)
          if (!res.ok) {
            showToast(`Upload failed: ${name}/${relPath}: ${res.error || 'unknown error'}`, 'bad')
            return
          }
          if (res.path && !gdbRootPath) gdbRootPath = gdbRootFromUpload(res.path, relPath)
        } catch (err) {
          showToast(`Upload failed: ${name}/${relPath}: ${(err as Error).message}`, 'bad')
          return
        }
      }
      if (gdbRootPath) {
        const base = name.slice(0, name.toLowerCase().lastIndexOf('.gdb')) || name
        added({ id: sourceIdFromName(base), format: 'fgdb', uri: gdbRootPath }, name)
      }
    })
  }

  // A dropped directory only appears in e.dataTransfer.files as an unreadable, size-0
  // placeholder (browsers don't recurse into folders through that API) — walking
  // e.dataTransfer.items with webkitGetAsEntry() is the only way to see its real contents.
  // Entries must be read out of the DataTransferItemList synchronously, before any `await`,
  // since browsers can invalidate it once the drop event handler yields.
  const handleDroppedItems = async (items: DataTransferItem[]) => {
    const entries = items
      .map(it => it.webkitGetAsEntry?.())
      .filter((e): e is FileSystemEntry => !!e)

    const plainFiles: File[] = []
    const gdbFolders: { name: string; entries: GdbFileEntry[] }[] = []

    for (const entry of entries) {
      if (entry.isDirectory) {
        if (entry.name.toLowerCase().endsWith('.gdb')) {
          const collected: GdbFileEntry[] = []
          await walkGdbEntry(entry, '', collected)
          gdbFolders.push({ name: entry.name, entries: collected })
        } else {
          showToast(`Skipped "${entry.name}": folders aren't supported as sources (except .gdb)`, 'bad')
        }
      } else if (entry.isFile) {
        const file = await new Promise<File>((resolve, reject) =>
          (entry as FileSystemFileEntry).file(resolve, reject))
        plainFiles.push(file)
      }
    }

    for (const { name, entries: gdbEntries } of gdbFolders) {
      await uploadGdbFolder(name, gdbEntries)
    }
    if (plainFiles.length > 0) {
      await uploadFiles(plainFiles)
    }
  }

  fileInp.addEventListener('change', () => {
    if (fileInp.files && fileInp.files.length > 0) {
      uploadFiles(fileInp.files)
    }
  })

  dropzone.addEventListener('dragover', e => {
    e.preventDefault()
    e.stopPropagation()
    dropzone.classList.add('drag-active')
  })

  dropzone.addEventListener('dragleave', e => {
    e.preventDefault()
    e.stopPropagation()
    dropzone.classList.remove('drag-active')
  })

  dropzone.addEventListener('drop', e => {
    e.preventDefault()
    e.stopPropagation()
    dropzone.classList.remove('drag-active')
    const dt = e.dataTransfer
    if (!dt) return
    if (dt.items && dt.items.length > 0 && typeof dt.items[0]?.webkitGetAsEntry === 'function') {
      handleDroppedItems(Array.from(dt.items))
    } else if (dt.files && dt.files.length > 0) {
      uploadFiles(dt.files)
    }
  })
}

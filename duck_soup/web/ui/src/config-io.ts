import { createIcons } from 'lucide'
import { qs, appIcons } from './dom'
import { pipelineCard, collectPipelineDef } from './cards/pipeline-card'
import { METADATA_FIELDS } from './metadata'
import type { Config, DatasetMetadata } from './types'

export function collectConfig(): Config {
  const name = qs<HTMLInputElement>('#cfg_name')?.value.trim() || 'config'
  const output = qs<HTMLInputElement>('#cfg_output')?.value.trim() || 'output/data.gpkg'
  const pipelines = [...document.querySelectorAll<HTMLElement>('.pipeline-card')]
    .map(card => collectPipelineDef(card))

  const metaSection = document.querySelector('#cfg-metadata')
  const readMk = (k: string) =>
    metaSection?.querySelector<HTMLInputElement | HTMLTextAreaElement>(`[data-mk="${k}"]`)?.value.trim() || undefined
  const metaFields: DatasetMetadata = {
    name: readMk('name'),
    abstract: readMk('abstract'),
    origin: readMk('origin'),
    update_frequency: readMk('update_frequency'),
    geometric_quality: readMk('geometric_quality'),
    attribute_quality: readMk('attribute_quality'),
    access_method_source: readMk('access_method_source'),
    gdpr: readMk('gdpr'),
  }
  const metaEntries = Object.entries(metaFields).filter(([, v]) => v !== undefined)
  const metadata = metaEntries.length > 0 ? Object.fromEntries(metaEntries) as DatasetMetadata : undefined

  return { name, output, metadata, pipelines }
}

export function hydrate(cfg: Partial<Config>, syncFn: () => void): void {
  const nameEl = qs<HTMLInputElement>('#cfg_name')
  const outputEl = qs<HTMLInputElement>('#cfg_output')
  if (nameEl) nameEl.value = cfg.name || ''
  if (outputEl) outputEl.value = cfg.output || ''

  const metaSection = document.querySelector('#cfg-metadata')
  if (metaSection) {
    const mk = (cfg.metadata ?? {}) as Record<string, string | undefined>
    let lastEl: (HTMLInputElement | HTMLTextAreaElement) | undefined
    METADATA_FIELDS.forEach(k => {
      const el = metaSection.querySelector<HTMLInputElement | HTMLTextAreaElement>(`[data-mk="${k}"]`)
      if (el) { el.value = mk[k] ?? ''; lastEl = el }
    })
    if (lastEl) (lastEl as HTMLInputElement | HTMLTextAreaElement).dispatchEvent(new Event('input', { bubbles: true }))
  }

  const container = document.querySelector<HTMLElement>('#pipelines')!
  container.innerHTML = ''
  const pipelines = cfg.pipelines || []
  pipelines.forEach(p => container.appendChild(pipelineCard(p, syncFn)))
  // Multiple pipelines start minimized so the list is scannable at a glance; a single
  // pipeline (the common case) stays open since there's nothing else to compare it against.
  if (pipelines.length > 1) {
    container.querySelectorAll<HTMLElement>('.pipeline-card').forEach(card => {
      card.classList.add('collapsed')
      ;(card as HTMLElement & { _syncCollapse?: () => void })._syncCollapse?.()
    })
  }
  createIcons({ icons: appIcons })

  syncFn()
}
